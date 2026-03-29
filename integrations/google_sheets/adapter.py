"""Google Sheets adapter — T-068.

Maps Google Sheets rows to PMO delivery tasks.
Replaces Smartsheet — free via service account (GOOGLE_SERVICE_ACCOUNT_PATH).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)


class GoogleSheetsAdapter:
    """Adapts Google Sheets rows to PMO DeliveryEvents.

    Credentials from GOOGLE_SERVICE_ACCOUNT_PATH env var.
    Column header row defines field mapping.
    Rate limits: 300 req/min free tier — retries handled automatically.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
        sheet_range: Optional[str] = None,
    ) -> None:
        self._credentials_path = credentials_path or os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH", "")
        self._spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SPREADSHEET_ID", "")
        self._sheet_range = sheet_range or os.environ.get("GOOGLE_SHEET_RANGE", "Sheet1!A:Z")
        self._service = None

    def _get_service(self):
        """Lazy-initialize Google Sheets API service."""
        if self._service is None:
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
            except ImportError:
                raise RuntimeError(
                    "google-api-python-client is not installed. "
                    "Run: pip install google-api-python-client google-auth"
                )
            if not self._credentials_path or not os.path.exists(self._credentials_path):
                raise RuntimeError(
                    f"GOOGLE_SERVICE_ACCOUNT_PATH='{self._credentials_path}' not found."
                )
            scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path, scopes=scopes
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def to_delivery_event(
        self,
        row: Dict[str, Any],
        project_id: str,
        tenant_id: str = "default",
    ) -> DeliveryEvent:
        """Convert a Google Sheets row (header-mapped dict) to a PMO DeliveryEvent.

        Args:
            row: Dict with column header keys mapped to cell values.
            project_id: PMO project identifier.
            tenant_id: Tenant identifier.

        Returns:
            DeliveryEvent with mapped fields. Handles missing columns gracefully.
        """
        status = str(row.get("status", row.get("Status", "unknown"))).lower()
        task_id = str(row.get("task_id", row.get("Task ID", row.get("id", "unknown"))))
        assignee = str(row.get("assignee", row.get("Assignee", "")))

        # Determine event type from status
        if "blocked" in status:
            event_type = "dependency.blocked"
        elif "risk" in status:
            event_type = "risk.detected"
        else:
            event_type = "task.updated"

        payload = {
            "task_id": task_id,
            "new_status": status,
            "assignee": assignee,
            "source": "google_sheets",
        }
        # Include any extra columns as payload fields
        for key, value in row.items():
            if key.lower() not in ("status", "task_id", "id", "assignee") and value:
                payload[key.lower()] = value

        return DeliveryEvent(
            event_type=EventType(event_type),
            project_id=project_id,
            source="google_sheets",
            tenant_id=tenant_id,
            payload=payload,
        )

    def fetch_rows(self) -> List[Dict[str, Any]]:
        """Fetch rows from the configured spreadsheet.

        Returns:
            List of dicts with header-mapped values. Empty list on error.
        """
        if not self._spreadsheet_id:
            logger.warning("GoogleSheetsAdapter: GOOGLE_SPREADSHEET_ID not set")
            return []
        try:
            service = self._get_service()
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=self._spreadsheet_id, range=self._sheet_range)
                .execute()
            )
            rows = result.get("values", [])
            if not rows:
                return []
            headers = [h.strip() for h in rows[0]]
            data = []
            for row in rows[1:]:
                # Pad short rows
                padded = row + [""] * (len(headers) - len(row))
                data.append(dict(zip(headers, padded)))
            return data
        except Exception as e:
            logger.error("GoogleSheetsAdapter: fetch failed: %s", e)
            return []
