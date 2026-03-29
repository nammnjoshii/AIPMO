"""GitHub Issues poller — T-067.

APScheduler polling every 5 minutes. Fetches issues updated since last poll timestamp
(stored in Redis key poller:github_issues:{repo}:last_seen).
Emits DeliveryEvents only for changed issues.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_POLL_INTERVAL = int(os.environ.get("GITHUB_POLL_INTERVAL_SECONDS", "300"))
_REDIS_KEY_TEMPLATE = "poller:github_issues:{repo}:last_seen"


class GitHubIssuesPoller:
    """Polls GitHub Issues repos and emits DeliveryEvents for changed issues."""

    def __init__(
        self,
        adapter=None,
        producer=None,
        redis_url: Optional[str] = None,
        repos: Optional[List[str]] = None,
        project_mapping: Optional[dict] = None,
    ) -> None:
        from integrations.github_issues.adapter import GitHubIssuesAdapter

        self._adapter = adapter or GitHubIssuesAdapter()
        self._producer = producer
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._repos = repos or []
        self._project_mapping = project_mapping or {}

    def _get_redis(self):
        import redis as redis_lib
        return redis_lib.from_url(self._redis_url, decode_responses=True)

    def _get_last_seen(self, repo: str) -> Optional[str]:
        key = _REDIS_KEY_TEMPLATE.format(repo=repo.replace("/", "_"))
        try:
            r = self._get_redis()
            return r.get(key)
        except Exception:
            return None

    def _set_last_seen(self, repo: str, timestamp: str) -> None:
        key = _REDIS_KEY_TEMPLATE.format(repo=repo.replace("/", "_"))
        try:
            r = self._get_redis()
            r.set(key, timestamp)
        except Exception as e:
            logger.warning("Poller: could not update last_seen for %s: %s", repo, e)

    def poll_once(self) -> int:
        """Poll all registered repos and emit events for changed issues.

        Returns:
            Total number of events emitted.
        """
        total = 0
        for repo in self._repos:
            count = self._poll_repo(repo)
            total += count
        return total

    def _poll_repo(self, repo: str) -> int:
        """Poll a single repo. Returns number of events emitted."""
        last_seen = self._get_last_seen(repo)
        project_id = self._project_mapping.get(repo, f"gh_{repo.split('/')[-1]}")
        now = datetime.now(timezone.utc).isoformat()

        try:
            issues = self._adapter.fetch_issues(repo, since_timestamp=last_seen)
        except Exception as e:
            logger.error("Poller: failed to fetch issues from %s: %s", repo, e)
            return 0

        emitted = 0
        for issue in issues:
            try:
                event = self._adapter.to_delivery_event(issue, project_id=project_id)
                if self._producer:
                    self._producer.publish(event)
                emitted += 1
            except Exception as e:
                logger.warning("Poller: failed to convert/emit issue: %s", e)

        if issues:
            self._set_last_seen(repo, now)
            logger.info("Poller: %s → %d events emitted", repo, emitted)

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
            id="github_issues_poller",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("GitHubIssuesPoller: started, interval=%ds, repos=%s", _POLL_INTERVAL, self._repos)
