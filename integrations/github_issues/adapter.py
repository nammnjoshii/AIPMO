"""GitHub Issues adapter — T-065.

Maps GitHub Issues to delivery tasks. Replaces Jira adapter — free, same REST pattern.
Uses PyGithub with GITHUB_TOKEN from SecretsManager.

Label mapping:
  status: blocked      → new_status=blocked
  status: in-progress  → new_status=in_progress
  dependency: #N       → dependency_link
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent, EventType

logger = logging.getLogger(__name__)


class GitHubIssuesAdapter:
    """Adapts GitHub Issues to PMO DeliveryEvents.

    Does NOT import from state/ or signal_quality/ — only events/schemas/.
    GITHUB_TOKEN loaded via SecretsManager or os.environ directly.
    """

    # Label → status mapping
    _STATUS_LABELS = {
        "status: blocked": "blocked",
        "status: in-progress": "in_progress",
        "status: in progress": "in_progress",
        "status: done": "done",
        "status: todo": "todo",
    }

    def __init__(
        self,
        token: Optional[str] = None,
        org: Optional[str] = None,
    ) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._org = org or os.environ.get("GITHUB_ORG", "")
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

    def to_delivery_event(
        self,
        issue: Any,
        project_id: str,
        tenant_id: str = "default",
    ) -> DeliveryEvent:
        """Convert a GitHub Issue object to a PMO DeliveryEvent.

        Args:
            issue: PyGithub Issue object or dict with issue fields.
            project_id: PMO project ID this issue belongs to.
            tenant_id: Tenant identifier.

        Returns:
            DeliveryEvent with mapped fields.
        """
        if isinstance(issue, dict):
            labels = [l.get("name", "") for l in issue.get("labels", [])]
            issue_number = issue.get("number", 0)
            title = issue.get("title", "")
            state = issue.get("state", "open")
            body = issue.get("body", "")
        else:
            labels = [l.name for l in issue.labels]
            issue_number = issue.number
            title = issue.title
            state = issue.state
            body = issue.body or ""

        new_status = self._map_status(labels)
        blocked_by = self._extract_dependency(labels, body)
        event_type = self._determine_event_type(new_status, labels)

        payload = {
            "task_id": f"gh_issue_{issue_number}",
            "new_status": new_status,
            "title": title,
            "labels": labels,
            "state": state,
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by

        return DeliveryEvent(
            event_type=EventType(event_type),
            project_id=project_id,
            source="github_issues",
            tenant_id=tenant_id,
            payload=payload,
        )

    def fetch_issues(self, repo_name: str, since_timestamp: Optional[str] = None) -> List[Any]:
        """Fetch issues from a GitHub repository.

        Args:
            repo_name: Full repo name e.g. "org/repo".
            since_timestamp: ISO8601 string — only fetch issues updated since this time.

        Returns:
            List of PyGithub Issue objects.
        """
        client = self._get_client()
        repo = client.get_repo(repo_name)
        kwargs: Dict[str, Any] = {"state": "all"}
        if since_timestamp:
            from datetime import datetime
            kwargs["since"] = datetime.fromisoformat(since_timestamp.replace("Z", "+00:00"))
        return list(repo.get_issues(**kwargs))

    # ---- Helpers ----

    def _map_status(self, labels: List[str]) -> str:
        for label in labels:
            lower = label.lower()
            for pattern, status in self._STATUS_LABELS.items():
                if pattern.lower() in lower:
                    return status
        return "unknown"

    def _extract_dependency(self, labels: List[str], body: str) -> Optional[str]:
        for label in labels:
            if "dependency:" in label.lower():
                parts = label.split(":")
                if len(parts) >= 2:
                    return parts[1].strip()
        # Try to extract from body
        import re
        match = re.search(r"depends on #(\d+)", body, re.IGNORECASE)
        if match:
            return f"#{match.group(1)}"
        return None

    def _determine_event_type(self, status: str, labels: List[str]) -> str:
        if status == "blocked":
            return "dependency.blocked"
        label_str = " ".join(labels).lower()
        if "risk" in label_str:
            return "risk.detected"
        if "milestone" in label_str:
            return "milestone.updated"
        return "task.updated"
