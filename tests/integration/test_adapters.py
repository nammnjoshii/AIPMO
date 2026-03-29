"""Integration tests for GitHub Issues and Google Sheets adapters — T-072.

Uses mocked HTTP responses (unittest.mock). Does not require live credentials.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# GitHub Issues adapter tests
# ---------------------------------------------------------------------------


class TestGitHubIssuesAdapterToDeliveryEvent:
    """Tests for GitHubIssuesAdapter.to_delivery_event()."""

    def _make_adapter(self):
        from integrations.github_issues.adapter import GitHubIssuesAdapter
        return GitHubIssuesAdapter(token="fake-token")

    def _make_issue(
        self,
        number: int = 42,
        title: str = "Test issue",
        state: str = "open",
        label_names: list = None,
        body: str = "",
        assignees: list = None,
    ) -> MagicMock:
        """Build a mock PyGithub Issue object."""
        issue = MagicMock()
        issue.number = number
        issue.title = title
        issue.state = state
        issue.body = body or ""
        issue.html_url = f"https://github.com/test/repo/issues/{number}"
        issue.updated_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
        issue.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

        labels = []
        for name in (label_names or []):
            lbl = MagicMock()
            lbl.name = name
            labels.append(lbl)
        issue.labels = labels

        assigned = []
        for login in (assignees or []):
            usr = MagicMock()
            usr.login = login
            assigned.append(usr)
        issue.assignees = assigned

        return issue

    def test_blocked_label_maps_to_dependency_blocked(self):
        adapter = self._make_adapter()
        issue = self._make_issue(label_names=["status: blocked"])
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert str(event.event_type) == "dependency.blocked"

    def test_risk_label_maps_to_risk_detected(self):
        adapter = self._make_adapter()
        issue = self._make_issue(label_names=["risk: high"])
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert str(event.event_type) == "risk.detected"

    def test_in_progress_label_maps_to_task_updated(self):
        adapter = self._make_adapter()
        issue = self._make_issue(label_names=["status: in-progress"])
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert str(event.event_type) == "task.updated"
        assert event.payload["new_status"] == "in_progress"

    def test_no_labels_maps_to_task_updated(self):
        adapter = self._make_adapter()
        issue = self._make_issue()
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert str(event.event_type) == "task.updated"

    def test_closed_issue_state_preserved_in_payload(self):
        adapter = self._make_adapter()
        issue = self._make_issue(state="closed")
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        # Closed issues have state=closed in payload; status depends on labels
        assert event.payload["state"] == "closed"

    def test_project_id_propagated(self):
        adapter = self._make_adapter()
        issue = self._make_issue()
        event = adapter.to_delivery_event(issue, project_id="proj_alpha")
        assert event.project_id == "proj_alpha"

    def test_source_is_github_issues(self):
        adapter = self._make_adapter()
        issue = self._make_issue()
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert event.source == "github_issues"

    def test_task_id_contains_issue_number(self):
        adapter = self._make_adapter()
        issue = self._make_issue(number=99)
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert "99" in event.payload["task_id"]

    def test_issue_number_in_task_id(self):
        adapter = self._make_adapter()
        issue = self._make_issue(number=77, assignees=["alice"])
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert "77" in event.payload["task_id"]

    def test_dependency_extracted_from_body_text(self):
        adapter = self._make_adapter()
        issue = self._make_issue(body="depends on #7, also linked to #12")
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        payload = event.payload
        # At least one dependency should appear somewhere in the payload
        dep_val = payload.get("blocked_by", "") or payload.get("dependency", "")
        assert dep_val  # non-empty dependency extracted

    def test_dependency_extracted_from_label(self):
        adapter = self._make_adapter()
        issue = self._make_issue(label_names=["dependency: #5"])
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        payload = event.payload
        dep_val = payload.get("blocked_by", "") or payload.get("dependency", "")
        assert dep_val

    def test_tenant_id_default_is_default(self):
        adapter = self._make_adapter()
        issue = self._make_issue()
        event = adapter.to_delivery_event(issue, project_id="proj_test")
        assert event.tenant_id == "default"


class TestGitHubIssuesAdapterFetchIssues:
    """Tests for GitHubIssuesAdapter.fetch_issues() with mocked PyGithub."""

    def _make_adapter(self):
        from integrations.github_issues.adapter import GitHubIssuesAdapter
        return GitHubIssuesAdapter(token="fake-token")

    def _make_issue(self, number: int = 1, label_names: list = None) -> MagicMock:
        issue = MagicMock()
        issue.number = number
        issue.title = f"Issue {number}"
        issue.state = "open"
        issue.body = ""
        issue.html_url = f"https://github.com/test/repo/issues/{number}"
        issue.updated_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
        issue.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        labels = []
        for name in (label_names or []):
            lbl = MagicMock()
            lbl.name = name
            labels.append(lbl)
        issue.labels = labels
        issue.assignees = []
        return issue

    def test_fetch_issues_returns_list(self):
        import sys
        adapter = self._make_adapter()
        mock_issue = self._make_issue(1)

        mock_repo = MagicMock()
        mock_repo.get_issues.return_value = [mock_issue]

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo
        adapter._client = mock_client  # inject directly to bypass lazy init

        result = adapter.fetch_issues("owner/repo")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_fetch_issues_raises_on_exception(self):
        adapter = self._make_adapter()
        mock_client = MagicMock()
        mock_client.get_repo.side_effect = Exception("network error")
        adapter._client = mock_client

        with pytest.raises(Exception, match="network error"):
            adapter.fetch_issues("owner/repo")


# ---------------------------------------------------------------------------
# Google Sheets adapter tests
# ---------------------------------------------------------------------------


class TestGoogleSheetsAdapterToDeliveryEvent:
    """Tests for GoogleSheetsAdapter.to_delivery_event()."""

    def _make_adapter(self):
        from integrations.google_sheets.adapter import GoogleSheetsAdapter
        return GoogleSheetsAdapter(
            credentials_path="/fake/creds.json",
            spreadsheet_id="fake-sheet-id",
        )

    def test_blocked_status_maps_to_dependency_blocked(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-001", "status": "blocked", "assignee": "bob"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert str(event.event_type) == "dependency.blocked"

    def test_risk_status_maps_to_risk_detected(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-002", "status": "risk: high"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert str(event.event_type) == "risk.detected"

    def test_normal_status_maps_to_task_updated(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-003", "status": "in-progress"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert str(event.event_type) == "task.updated"

    def test_missing_task_id_handled_gracefully(self):
        adapter = self._make_adapter()
        row = {"status": "in-progress", "assignee": "carol"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert event is not None  # no exception

    def test_missing_assignee_handled_gracefully(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-004", "status": "on-track"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert event is not None

    def test_extra_columns_included_in_payload(self):
        adapter = self._make_adapter()
        row = {
            "task_id": "T-005",
            "status": "in-progress",
            "priority": "high",
            "sprint": "S3",
        }
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert event.payload.get("priority") == "high"
        assert event.payload.get("sprint") == "S3"

    def test_source_is_google_sheets(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-006", "status": "on-track"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert event.source == "google_sheets"

    def test_project_id_propagated(self):
        adapter = self._make_adapter()
        row = {"task_id": "T-007", "status": "on-track"}
        event = adapter.to_delivery_event(row, project_id="proj_gamma")
        assert event.project_id == "proj_gamma"

    def test_capitalized_header_keys_handled(self):
        """Google Sheets often returns Title Case headers."""
        adapter = self._make_adapter()
        row = {"Task ID": "T-008", "Status": "blocked", "Assignee": "dave"}
        event = adapter.to_delivery_event(row, project_id="proj_sheets")
        assert event is not None
        assert str(event.event_type) == "dependency.blocked"


class TestGoogleSheetsAdapterFetchRows:
    """Tests for GoogleSheetsAdapter.fetch_rows() with mocked Google API."""

    def _make_adapter(self):
        from integrations.google_sheets.adapter import GoogleSheetsAdapter
        return GoogleSheetsAdapter(
            credentials_path="/fake/creds.json",
            spreadsheet_id="fake-sheet-id",
        )

    def test_fetch_rows_parses_header_row(self):
        adapter = self._make_adapter()
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [
                ["task_id", "status", "assignee"],
                ["T-001", "in-progress", "alice"],
                ["T-002", "blocked", "bob"],
            ]
        }
        adapter._service = mock_service

        rows = adapter.fetch_rows()
        assert len(rows) == 2
        assert rows[0]["task_id"] == "T-001"
        assert rows[1]["status"] == "blocked"

    def test_fetch_rows_empty_spreadsheet(self):
        adapter = self._make_adapter()
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {"values": []}
        adapter._service = mock_service

        rows = adapter.fetch_rows()
        assert rows == []

    def test_fetch_rows_pads_short_rows(self):
        adapter = self._make_adapter()
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [
                ["task_id", "status", "assignee"],
                ["T-001"],  # short row — missing status and assignee
            ]
        }
        adapter._service = mock_service

        rows = adapter.fetch_rows()
        assert len(rows) == 1
        assert rows[0]["status"] == ""
        assert rows[0]["assignee"] == ""

    def test_fetch_rows_returns_empty_on_api_error(self):
        adapter = self._make_adapter()
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.side_effect = Exception("API error")
        adapter._service = mock_service

        rows = adapter.fetch_rows()
        assert rows == []

    def test_fetch_rows_no_spreadsheet_id(self):
        from integrations.google_sheets.adapter import GoogleSheetsAdapter
        adapter = GoogleSheetsAdapter(credentials_path="/fake/creds.json", spreadsheet_id="")
        rows = adapter.fetch_rows()
        assert rows == []


# ---------------------------------------------------------------------------
# Poller deduplication tests (Redis hash)
# ---------------------------------------------------------------------------


class TestGoogleSheetsPollerDeduplication:
    """Tests for GoogleSheetsPoller deduplication via Redis hash."""

    def _make_poller(self, mock_adapter, mock_producer, mock_redis):
        from integrations.google_sheets.poller import GoogleSheetsPoller

        poller = GoogleSheetsPoller(
            adapter=mock_adapter,
            producer=mock_producer,
            redis_url="redis://localhost:6379/0",
            project_id="proj_sheets",
        )
        poller._get_redis = lambda: mock_redis
        return poller

    def test_unchanged_rows_emit_no_events(self):
        row = {"task_id": "T-001", "status": "in-progress", "assignee": "alice"}

        mock_adapter = MagicMock()
        mock_adapter.fetch_rows.return_value = [row]

        from integrations.google_sheets.adapter import GoogleSheetsAdapter
        real_adapter = GoogleSheetsAdapter.__new__(GoogleSheetsAdapter)

        import hashlib, json
        existing_hash = hashlib.sha256(
            json.dumps(row, sort_keys=True).encode()
        ).hexdigest()

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"T-001": existing_hash})
        mock_redis.set.return_value = None

        mock_producer = MagicMock()

        poller = self._make_poller(mock_adapter, mock_producer, mock_redis)
        count = poller.poll_once()
        assert count == 0
        mock_producer.publish.assert_not_called()

    def test_changed_row_emits_event(self):
        row = {"task_id": "T-001", "status": "blocked", "assignee": "alice"}

        mock_adapter = MagicMock()
        mock_adapter.fetch_rows.return_value = [row]

        from events.schemas.event_types import DeliveryEvent, EventType
        mock_event = MagicMock(spec=DeliveryEvent)
        mock_adapter.to_delivery_event.return_value = mock_event

        import json
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"T-001": "old-hash-value"})
        mock_redis.set.return_value = None

        mock_producer = MagicMock()

        poller = self._make_poller(mock_adapter, mock_producer, mock_redis)
        count = poller.poll_once()
        assert count == 1
        mock_producer.publish.assert_called_once()

    def test_new_row_emits_event(self):
        """Row with no prior hash should emit event."""
        row = {"task_id": "T-NEW", "status": "in-progress"}

        mock_adapter = MagicMock()
        mock_adapter.fetch_rows.return_value = [row]

        from events.schemas.event_types import DeliveryEvent
        mock_event = MagicMock(spec=DeliveryEvent)
        mock_adapter.to_delivery_event.return_value = mock_event

        import json
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({})  # empty — no prior hashes
        mock_redis.set.return_value = None

        mock_producer = MagicMock()

        poller = self._make_poller(mock_adapter, mock_producer, mock_redis)
        count = poller.poll_once()
        assert count == 1


# ---------------------------------------------------------------------------
# GitHub Issues bootstrap tests
# ---------------------------------------------------------------------------


def _make_repo_mock(name: str) -> MagicMock:
    repo = MagicMock()
    repo.name = name
    repo.owner = MagicMock()
    repo.owner.login = "test-org"
    return repo


def _install_github_mock(repos=None, raise_on_get_org=False):
    """Inject a fake 'github' module into sys.modules so bootstrap can import it."""
    import sys
    import types

    mock_client = MagicMock()
    if raise_on_get_org:
        mock_client.get_organization.side_effect = Exception("API error")
    else:
        mock_org = MagicMock()
        mock_org.get_repos.return_value = repos or []
        mock_client.get_organization.return_value = mock_org

    mock_gh_class = MagicMock(return_value=mock_client)

    fake_github = types.ModuleType("github")
    fake_github.Github = mock_gh_class

    sys.modules["github"] = fake_github
    return fake_github, mock_client


class TestGitHubIssuesBootstrap:
    """Tests for bootstrap handling of 0 repos and normal registration."""

    def setup_method(self):
        import sys
        # Remove cached github module before each test
        sys.modules.pop("github", None)

    def teardown_method(self):
        import sys
        sys.modules.pop("github", None)

    @pytest.mark.asyncio
    async def test_bootstrap_handles_zero_repos(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _install_github_mock(repos=[])

        from integrations.github_issues.bootstrap import bootstrap
        count = await bootstrap(org="test-org", db_path=db_path)
        assert count == 0

    @pytest.mark.asyncio
    async def test_bootstrap_registers_repos(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _install_github_mock(repos=[
            _make_repo_mock("repo-alpha"),
            _make_repo_mock("repo-beta"),
        ])

        from integrations.github_issues.bootstrap import bootstrap
        count = await bootstrap(org="test-org", db_path=db_path)
        assert count == 2

    @pytest.mark.asyncio
    async def test_bootstrap_skips_empty_org(self, tmp_path):
        db_path = str(tmp_path / "test.db")

        from integrations.github_issues.bootstrap import bootstrap
        count = await bootstrap(org="", db_path=db_path)
        assert count == 0

    @pytest.mark.asyncio
    async def test_bootstrap_handles_github_exception(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _install_github_mock(raise_on_get_org=True)

        from integrations.github_issues.bootstrap import bootstrap
        count = await bootstrap(org="test-org", db_path=db_path)
        assert count == 0
