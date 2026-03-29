"""GitHub velocity adapter — T-070.

Tracks commit frequency for throughput analysis. Separate from GitHub Issues
(T-065) — this adapter measures engineering velocity, not task status.

Maps commit activity windows to DeliveryEvent payloads so the Execution
Monitoring Agent can compute throughput_rate and detect velocity drops.

Usage:
    adapter = GitHubVelocityAdapter()
    events = adapter.get_velocity_events("org/repo", project_id="proj_001")
    for evt in events:
        await producer.publish(evt)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)

# Default lookback window for velocity computation
_DEFAULT_WINDOW_DAYS = 7


class GitHubVelocityAdapter:
    """Maps GitHub commit frequency to throughput DeliveryEvents.

    Does NOT import from state/ or signal_quality/ — only events/schemas/.
    Uses GITHUB_TOKEN from environment (same token as github_issues adapter).
    """

    def __init__(
        self,
        token: Optional[str] = None,
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._window_days = window_days
        self._client = None

    def _get_client(self):
        """Lazy-initialize PyGithub client."""
        if self._client is None:
            try:
                from github import Github
                self._client = Github(self._token) if self._token else Github()
            except ImportError:
                raise RuntimeError(
                    "PyGithub is not installed. Run: pip install PyGithub"
                )
        return self._client

    def get_velocity_events(
        self,
        repo_name: str,
        project_id: str,
        tenant_id: str = "default",
        branch: str = "main",
    ) -> List[DeliveryEvent]:
        """Fetch commit activity for a repo and return throughput DeliveryEvents.

        One event is emitted per branch per repo containing:
          - commits_last_7d: count of commits in the lookback window
          - throughput_rate: commits / day
          - velocity_trend: "increasing" | "stable" | "declining"
          - last_commit_at: ISO8601 timestamp of most recent commit

        Args:
            repo_name: Full repo name e.g. "org/repo".
            project_id: PMO project this repo maps to.
            tenant_id: Tenant identifier.
            branch: Branch to measure velocity on (default: main).

        Returns:
            List with one DeliveryEvent (empty list on error — never raises).
        """
        try:
            client = self._get_client()
            repo = client.get_repo(repo_name)
            since = datetime.now(timezone.utc) - timedelta(days=self._window_days)

            commits = list(repo.get_commits(sha=branch, since=since))
            commit_count = len(commits)
            throughput_rate = round(commit_count / max(self._window_days, 1), 2)

            last_commit_at = None
            if commits:
                last_commit_at = commits[0].commit.author.date.isoformat()

            # Compare to prior window to determine trend
            prior_since = since - timedelta(days=self._window_days)
            prior_commits = list(repo.get_commits(sha=branch, since=prior_since, until=since))
            prior_count = len(prior_commits)

            velocity_trend = _compute_trend(commit_count, prior_count)

            payload = {
                "repo": repo_name,
                "branch": branch,
                "commits_last_7d": commit_count,
                "throughput_rate": throughput_rate,
                "velocity_trend": velocity_trend,
                "window_days": self._window_days,
                "last_commit_at": last_commit_at,
                "prior_window_commits": prior_count,
            }

            logger.info(
                "GitHubVelocityAdapter: %s branch=%s commits=%d rate=%.2f/day trend=%s",
                repo_name,
                branch,
                commit_count,
                throughput_rate,
                velocity_trend,
            )

            return [
                DeliveryEvent(
                    event_type=EventType.TASK_UPDATED,
                    project_id=project_id,
                    source="github_velocity",
                    tenant_id=tenant_id,
                    payload=payload,
                )
            ]

        except Exception as e:
            logger.warning(
                "GitHubVelocityAdapter: failed to fetch velocity for %s: %s",
                repo_name,
                e,
            )
            return []

    def get_contributor_activity(
        self,
        repo_name: str,
        project_id: str,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """Return per-contributor commit counts for the lookback window.

        Used by the Execution Monitoring Agent for bottleneck detection
        (contributor absence patterns).

        Returns:
            Dict mapping contributor login → commit count. Empty dict on error.
        """
        try:
            client = self._get_client()
            repo = client.get_repo(repo_name)
            since = datetime.now(timezone.utc) - timedelta(days=self._window_days)

            activity: Dict[str, int] = {}
            for commit in repo.get_commits(since=since):
                login = commit.author.login if commit.author else "unknown"
                activity[login] = activity.get(login, 0) + 1

            return activity

        except Exception as e:
            logger.warning(
                "GitHubVelocityAdapter: contributor activity failed for %s: %s",
                repo_name,
                e,
            )
            return {}

    def to_throughput_payload(
        self,
        repo_name: str,
        commit_count: int,
        prior_count: int,
        last_commit_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a throughput payload dict from raw commit counts.

        Useful for testing without a live GitHub connection.

        Args:
            repo_name: Repository name.
            commit_count: Commits in the current window.
            prior_count: Commits in the prior window (for trend).
            last_commit_at: ISO8601 timestamp of the most recent commit.

        Returns:
            Throughput payload dict compatible with DeliveryEvent.payload.
        """
        throughput_rate = round(commit_count / max(self._window_days, 1), 2)
        velocity_trend = _compute_trend(commit_count, prior_count)

        return {
            "repo": repo_name,
            "commits_last_7d": commit_count,
            "throughput_rate": throughput_rate,
            "velocity_trend": velocity_trend,
            "window_days": self._window_days,
            "last_commit_at": last_commit_at,
            "prior_window_commits": prior_count,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_trend(current: int, prior: int) -> str:
    """Classify velocity trend from two commit count windows.

    Returns:
        "increasing" | "stable" | "declining"
    """
    if prior == 0:
        return "stable" if current == 0 else "increasing"
    change_pct = (current - prior) / prior
    if change_pct >= 0.15:
        return "increasing"
    if change_pct <= -0.15:
        return "declining"
    return "stable"
