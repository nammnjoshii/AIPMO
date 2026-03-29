"""Signal normalization — converts raw source payloads to DeliveryEvent.

Never raises on partial or malformed input. Missing fields get safe defaults.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)

# Maps source-specific status labels to canonical new_status values
_GITHUB_LABEL_STATUS_MAP: Dict[str, str] = {
    "status: blocked": "blocked",
    "status: in-progress": "in_progress",
    "status: done": "done",
    "status: todo": "todo",
    "status: review": "review",
}

# Maps raw source names to canonical source identifiers
_SOURCE_ALIASES: Dict[str, str] = {
    "github": "github_issues",
    "github_issues": "github_issues",
    "jira": "jira",
    "google_sheets": "google_sheets",
    "smartsheet": "google_sheets",
    "slack": "slack",
    "manual": "manual",
    "ms_project": "ms_project",
}


class SignalNormalizer:
    """Normalizes raw signals from any source into DeliveryEvent objects.

    All methods are fault-tolerant — missing or malformed fields are replaced
    with safe defaults and a WARNING is logged. Never raises.
    """

    def normalize(
        self,
        raw_signal: Dict[str, Any],
        source: str,
        project_id: Optional[str] = None,
        tenant_id: str = "default",
    ) -> DeliveryEvent:
        """Convert a raw signal dict into a DeliveryEvent.

        Args:
            raw_signal: Arbitrary dict from source webhook or poller.
            source: Source name (github_issues, jira, google_sheets, slack, etc.)
            project_id: Optional override; falls back to raw_signal["project_id"].
            tenant_id: Tenant scoping identifier.

        Returns:
            A valid DeliveryEvent with all required fields populated.
        """
        try:
            return self._normalize(raw_signal, source, project_id, tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SignalNormalizer.normalize caught unexpected error for source=%s: %s. "
                "Returning minimal fallback event.",
                source,
                exc,
            )
            return self._fallback_event(raw_signal, source, project_id, tenant_id)

    def _normalize(
        self,
        raw: Dict[str, Any],
        source: str,
        project_id: Optional[str],
        tenant_id: str,
    ) -> DeliveryEvent:
        canonical_source = _SOURCE_ALIASES.get(source.lower(), source.lower())

        # Resolve project_id
        pid = project_id or raw.get("project_id") or raw.get("repo") or "unknown_project"
        if pid == "unknown_project":
            logger.warning(
                "SignalNormalizer: no project_id found in raw signal from source=%s. "
                "Using 'unknown_project'.",
                source,
            )

        # Resolve timestamp
        timestamp = self._parse_timestamp(raw, source)

        # Resolve event_type
        event_type = self._resolve_event_type(raw, canonical_source)

        # Normalize payload
        payload = self._normalize_payload(raw, canonical_source)

        return DeliveryEvent(
            event_id=raw.get("event_id") or str(uuid4()),
            event_type=event_type,
            project_id=pid,
            source=canonical_source,
            tenant_id=raw.get("tenant_id", tenant_id),
            timestamp=timestamp,
            payload=payload,
            signal_quality=raw.get("signal_quality"),
        )

    def _parse_timestamp(self, raw: Dict[str, Any], source: str) -> datetime:
        """Parse timestamp from raw signal, defaulting to now() with a WARNING."""
        raw_ts = raw.get("timestamp") or raw.get("updated_at") or raw.get("created_at")
        if raw_ts is None:
            logger.warning(
                "SignalNormalizer: missing timestamp field in raw signal from source=%s. "
                "Using current UTC time as default.",
                source,
            )
            return datetime.now(timezone.utc)

        if isinstance(raw_ts, datetime):
            return raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)

        if isinstance(raw_ts, (int, float)):
            return datetime.fromtimestamp(raw_ts, tz=timezone.utc)

        if isinstance(raw_ts, str):
            try:
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning(
                    "SignalNormalizer: unparseable timestamp '%s' from source=%s. "
                    "Using current UTC time.",
                    raw_ts,
                    source,
                )
                return datetime.now(timezone.utc)

        logger.warning(
            "SignalNormalizer: unexpected timestamp type %s from source=%s. "
            "Using current UTC time.",
            type(raw_ts).__name__,
            source,
        )
        return datetime.now(timezone.utc)

    def _resolve_event_type(self, raw: Dict[str, Any], source: str) -> EventType:
        """Map raw event_type string or source signals to EventType enum."""
        raw_type = raw.get("event_type", "")

        # Direct match
        for et in EventType:
            if et.value == raw_type:
                return et

        # Infer from payload signals
        labels = raw.get("labels", [])
        if isinstance(labels, list):
            label_values = [
                (lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)).lower()
                for lbl in labels
            ]
            if any("blocked" in lv for lv in label_values):
                return EventType.DEPENDENCY_BLOCKED
            if any("risk" in lv for lv in label_values):
                return EventType.RISK_DETECTED

        if raw.get("new_status") == "blocked" or raw.get("status") == "blocked":
            return EventType.DEPENDENCY_BLOCKED

        if "milestone" in raw_type.lower() or raw.get("milestone"):
            return EventType.MILESTONE_UPDATED

        if "risk" in raw_type.lower():
            return EventType.RISK_DETECTED

        if "capacity" in raw_type.lower():
            return EventType.CAPACITY_CHANGED

        # Default
        return EventType.TASK_UPDATED

    def _normalize_payload(self, raw: Dict[str, Any], source: str) -> Dict[str, Any]:
        """Extract and normalize the relevant payload fields."""
        # Pass through payload sub-dict if present
        if "payload" in raw and isinstance(raw["payload"], dict):
            payload = dict(raw["payload"])
        else:
            # Build payload from top-level fields, excluding meta fields
            _meta = {"event_id", "event_type", "project_id", "source", "tenant_id",
                     "timestamp", "updated_at", "created_at", "signal_quality"}
            payload = {k: v for k, v in raw.items() if k not in _meta}

        # GitHub-specific: normalize label list to new_status
        if source == "github_issues" and "labels" in payload:
            labels = payload.get("labels", [])
            label_names = [
                (lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)).lower()
                for lbl in labels
            ]
            for label_name in label_names:
                if label_name in _GITHUB_LABEL_STATUS_MAP:
                    payload.setdefault("new_status", _GITHUB_LABEL_STATUS_MAP[label_name])
                    break

        return payload

    def _fallback_event(
        self,
        raw: Dict[str, Any],
        source: str,
        project_id: Optional[str],
        tenant_id: str,
    ) -> DeliveryEvent:
        """Minimal safe DeliveryEvent for completely unprocessable input."""
        return DeliveryEvent(
            event_type=EventType.TASK_UPDATED,
            project_id=project_id or raw.get("project_id", "unknown_project"),
            source=_SOURCE_ALIASES.get(source.lower(), source.lower()),
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc),
            payload={"raw_fallback": True},
        )
