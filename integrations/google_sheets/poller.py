"""Google Sheets poller — T-069.

APScheduler polling every 5 minutes.
Compares row data to last-seen hash in Redis.
Emits DeliveryEvents only for changed rows.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

_POLL_INTERVAL = int(os.environ.get("GOOGLE_SHEETS_POLL_INTERVAL_SECONDS", "300"))
_REDIS_KEY_TEMPLATE = "poller:gsheets:{spreadsheet_id}:last_seen"


class GoogleSheetsPoller:
    """Polls Google Sheets and emits DeliveryEvents for changed rows."""

    def __init__(
        self,
        adapter=None,
        producer=None,
        redis_url: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        from integrations.google_sheets.adapter import GoogleSheetsAdapter
        self._adapter = adapter or GoogleSheetsAdapter()
        self._producer = producer
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._project_id = project_id or os.environ.get("GSHEETS_PROJECT_ID", "proj_sheets_default")
        self._spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "default")

    def _get_redis(self):
        import redis as redis_lib
        return redis_lib.from_url(self._redis_url, decode_responses=True)

    def _row_hash(self, row: dict) -> str:
        serialized = json.dumps(row, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def _load_last_hashes(self) -> dict:
        key = _REDIS_KEY_TEMPLATE.format(spreadsheet_id=self._spreadsheet_id)
        try:
            r = self._get_redis()
            val = r.get(key)
            return json.loads(val) if val else {}
        except Exception:
            return {}

    def _save_hashes(self, hashes: dict) -> None:
        key = _REDIS_KEY_TEMPLATE.format(spreadsheet_id=self._spreadsheet_id)
        try:
            r = self._get_redis()
            r.set(key, json.dumps(hashes))
        except Exception as e:
            logger.warning("GoogleSheetsPoller: could not save hashes: %s", e)

    def poll_once(self) -> int:
        """Poll spreadsheet and emit events for changed rows.

        Returns:
            Number of events emitted.
        """
        rows = self._adapter.fetch_rows()
        if not rows:
            return 0

        last_hashes = self._load_last_hashes()
        new_hashes = {}
        emitted = 0

        for i, row in enumerate(rows):
            row_key = str(row.get("task_id", row.get("id", i)))
            current_hash = self._row_hash(row)
            new_hashes[row_key] = current_hash

            if last_hashes.get(row_key) == current_hash:
                continue  # unchanged

            try:
                event = self._adapter.to_delivery_event(row, project_id=self._project_id)
                if self._producer:
                    self._producer.publish(event)
                emitted += 1
            except Exception as e:
                logger.warning("GoogleSheetsPoller: failed to emit row %s: %s", row_key, e)

        self._save_hashes(new_hashes)
        if emitted:
            logger.info("GoogleSheetsPoller: %d events emitted", emitted)
        return emitted

    def start(self) -> None:
        """Start APScheduler polling loop."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            raise RuntimeError("APScheduler not installed. Run: pip install apscheduler")

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            self.poll_once,
            trigger="interval",
            seconds=_POLL_INTERVAL,
            id="google_sheets_poller",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("GoogleSheetsPoller: started, interval=%ds", _POLL_INTERVAL)
