"""Unit tests for the Signal Quality Pipeline and its components.

Covers:
- ConfidenceDecayCalculator: multiple sources, multiple time points
- MissingDataDetector: all 3 gap rules
- NoiseFilter: dedup and low-signal detection (Redis mocked)
- SignalQualityPipeline: end-to-end, duplicate early exit, sparsity alert,
  decayed source, gap alert propagation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from signal_quality.confidence_decay import ConfidenceDecayCalculator
from signal_quality.missing_data import GapAlert, MissingDataDetector
from signal_quality.noise_filter import NoiseFilter
from signal_quality.pipeline import SignalQualityPipeline
from state.schemas import (
    CanonicalProjectState,
    HealthMetrics,
    Milestone,
    ProjectIdentity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(hours_ago: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def _project_state(
    project_id: str = "proj_test",
    milestones: Optional[list] = None,
) -> CanonicalProjectState:
    return CanonicalProjectState(
        project_id=project_id,
        identity=ProjectIdentity(project_id=project_id, name="Test Project", tenant_id="default"),
        milestones=milestones or [],
        health=HealthMetrics(),
    )


def _milestone(
    mid: str = "m1",
    name: str = "Alpha",
    days_until_due: float = 3.0,
    status: str = "on_track",
) -> Milestone:
    return Milestone(
        milestone_id=mid,
        name=name,
        due_date=_utc(-24 * days_until_due * -1),  # future
        status=status,
        completion_percentage=0.0,
    )


def _future(days: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


# ---------------------------------------------------------------------------
# ConfidenceDecayCalculator tests
# ---------------------------------------------------------------------------

class TestConfidenceDecayCalculator:
    def setup_method(self):
        self.calc = ConfidenceDecayCalculator()

    def test_jira_within_high_confidence_window(self):
        ts = _utc(hours_ago=10.0)  # 10h < 24h threshold
        result = self.calc.calculate("jira", ts)
        assert result.confidence_score == 1.0
        assert result.is_decayed is False

    def test_jira_at_decay_trigger_returns_zero(self):
        ts = _utc(hours_ago=48.0)  # exactly at trigger
        result = self.calc.calculate("jira", ts)
        assert result.confidence_score == 0.0
        assert result.is_decayed is True

    def test_jira_midpoint_linear_interpolation(self):
        # jira: high=24h, trigger=48h → midpoint = 36h → score = 0.5
        ts = _utc(hours_ago=36.0)
        result = self.calc.calculate("jira", ts)
        assert 0.45 < result.confidence_score < 0.55
        assert result.is_decayed is False

    def test_slack_fast_decay_within_window(self):
        ts = _utc(hours_ago=2.0)  # 2h < 4h slack high-conf
        result = self.calc.calculate("slack", ts)
        assert result.confidence_score == 1.0

    def test_slack_fully_decayed_after_24h(self):
        ts = _utc(hours_ago=25.0)
        result = self.calc.calculate("slack", ts)
        assert result.confidence_score == 0.0
        assert result.is_decayed is True

    def test_github_high_conf_window(self):
        ts = _utc(hours_ago=50.0)  # 50h < 72h
        result = self.calc.calculate("github", ts)
        assert result.confidence_score == 1.0

    def test_github_past_decay_trigger_120h(self):
        ts = _utc(hours_ago=121.0)
        result = self.calc.calculate("github", ts)
        assert result.confidence_score == 0.0
        assert result.is_decayed is True

    def test_unknown_source_uses_default_window(self):
        ts = _utc(hours_ago=10.0)
        result = self.calc.calculate("unknown_tool", ts)
        assert result.confidence_score == 1.0  # default high-conf = 24h

    def test_manual_very_fresh_is_high_confidence(self):
        ts = _utc(hours_ago=1.0)  # manual high-conf = 168h
        result = self.calc.calculate("manual", ts)
        assert result.confidence_score == 1.0

    def test_confidence_score_never_exceeds_one(self):
        ts = _utc(hours_ago=0.001)
        result = self.calc.calculate("jira", ts)
        assert result.confidence_score <= 1.0

    def test_confidence_score_never_below_zero(self):
        ts = _utc(hours_ago=9999.0)
        result = self.calc.calculate("slack", ts)
        assert result.confidence_score >= 0.0


# ---------------------------------------------------------------------------
# MissingDataDetector tests
# ---------------------------------------------------------------------------

class TestMissingDataDetector:
    def setup_method(self):
        self.detector = MissingDataDetector()
        self.project_id = "proj_abc"

    def test_rule1_empty_signal_times_returns_high_alert(self):
        alert = self.detector.check_no_recent_signal(self.project_id, {})
        assert alert is not None
        assert alert.rule_id == "gap_rule_1_no_signal"
        assert alert.severity == "high"

    def test_rule1_recent_signal_no_alert(self):
        alert = self.detector.check_no_recent_signal(
            self.project_id, {"jira": _utc(hours_ago=1.0)}
        )
        assert alert is None

    def test_rule1_stale_signal_48h_triggers_alert(self):
        alert = self.detector.check_no_recent_signal(
            self.project_id, {"jira": _utc(hours_ago=49.0)}
        )
        assert alert is not None
        assert alert.severity == "high"

    def test_rule2_milestone_due_soon_no_completion(self):
        m = Milestone(
            milestone_id="m1", name="Beta", due_date=_future(3), status="on_track"
        )
        alert = self.detector.check_milestone_without_completion(
            self.project_id, m, last_completion_time=None
        )
        assert alert is not None
        assert alert.rule_id == "gap_rule_2_milestone_no_completion"
        assert alert.severity == "high"

    def test_rule2_milestone_not_due_soon_no_alert(self):
        m = Milestone(
            milestone_id="m2", name="Gamma", due_date=_future(30), status="on_track"
        )
        alert = self.detector.check_milestone_without_completion(
            self.project_id, m, last_completion_time=None
        )
        assert alert is None

    def test_rule2_completion_stale_72h_triggers_alert(self):
        m = Milestone(
            milestone_id="m3", name="Delta", due_date=_future(5), status="on_track"
        )
        stale_completion = _utc(hours_ago=73.0)
        alert = self.detector.check_milestone_without_completion(
            self.project_id, m, last_completion_time=stale_completion
        )
        assert alert is not None

    def test_rule2_recent_completion_no_alert(self):
        m = Milestone(
            milestone_id="m4", name="Epsilon", due_date=_future(4), status="on_track"
        )
        alert = self.detector.check_milestone_without_completion(
            self.project_id, m, last_completion_time=_utc(hours_ago=1.0)
        )
        assert alert is None

    def test_rule3_at_risk_no_mitigation_triggers_alert(self):
        m = Milestone(
            milestone_id="m5", name="Zeta", due_date=_future(10), status="at_risk"
        )
        alert = self.detector.check_at_risk_without_mitigation(
            self.project_id, m, last_mitigation_time=None
        )
        assert alert is not None
        assert alert.rule_id == "gap_rule_3_at_risk_no_mitigation"
        assert alert.severity == "medium"

    def test_rule3_at_risk_stale_mitigation_triggers_alert(self):
        m = Milestone(
            milestone_id="m6", name="Eta", due_date=_future(10), status="at_risk"
        )
        alert = self.detector.check_at_risk_without_mitigation(
            self.project_id, m, last_mitigation_time=_utc(hours_ago=25.0)
        )
        assert alert is not None

    def test_rule3_on_track_milestone_ignored(self):
        m = Milestone(
            milestone_id="m7", name="Theta", due_date=_future(10), status="on_track"
        )
        alert = self.detector.check_at_risk_without_mitigation(
            self.project_id, m, last_mitigation_time=None
        )
        assert alert is None

    def test_check_all_returns_multiple_alerts(self):
        state = _project_state(
            project_id=self.project_id,
            milestones=[
                Milestone(
                    milestone_id="m8", name="Iota", due_date=_future(2), status="at_risk"
                )
            ],
        )
        alerts = self.detector.check_all(
            state,
            last_signal_times={},                # triggers rule 1
            last_completion_time=None,            # triggers rule 2
            last_mitigation_time=None,            # triggers rule 3
        )
        rule_ids = [a.rule_id for a in alerts]
        assert "gap_rule_1_no_signal" in rule_ids
        assert "gap_rule_2_milestone_no_completion" in rule_ids
        assert "gap_rule_3_at_risk_no_mitigation" in rule_ids


# ---------------------------------------------------------------------------
# NoiseFilter tests
# ---------------------------------------------------------------------------

class TestNoiseFilter:
    def test_is_low_signal_bot_actor(self):
        f = NoiseFilter()
        assert f.is_low_signal({"task_id": "t1"}, "github", actor="dependabot[bot]") is True

    def test_is_low_signal_empty_payload(self):
        f = NoiseFilter()
        assert f.is_low_signal({}, "jira") is True

    def test_is_low_signal_same_status_transition(self):
        f = NoiseFilter()
        assert f.is_low_signal(
            {"old_status": "in_progress", "new_status": "in_progress", "task_id": "t2"},
            "jira",
        ) is True

    def test_is_low_signal_low_signal_label(self):
        f = NoiseFilter()
        assert f.is_low_signal({"labels": ["duplicate"], "task_id": "t3"}, "github") is True

    def test_is_low_signal_normal_event_is_false(self):
        f = NoiseFilter()
        assert f.is_low_signal({"task_id": "t4", "new_status": "blocked"}, "jira") is False

    @patch("signal_quality.noise_filter.NoiseFilter._get_redis")
    def test_is_duplicate_first_occurrence_returns_false(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # SETNX succeeded → not a duplicate
        mock_get_redis.return_value = mock_redis

        f = NoiseFilter()
        result = f.is_duplicate("proj_1", {"task_id": "t5"}, "jira", "task.updated")
        assert result is False

    @patch("signal_quality.noise_filter.NoiseFilter._get_redis")
    def test_is_duplicate_second_occurrence_returns_true(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.set.return_value = None  # SETNX failed → duplicate
        mock_get_redis.return_value = mock_redis

        f = NoiseFilter()
        result = f.is_duplicate("proj_1", {"task_id": "t5"}, "jira", "task.updated")
        assert result is True

    @patch("signal_quality.noise_filter.NoiseFilter._get_redis")
    def test_is_duplicate_redis_unavailable_allows_through(self, mock_get_redis):
        mock_get_redis.return_value = None  # Redis down
        f = NoiseFilter()
        result = f.is_duplicate("proj_1", {"task_id": "t6"}, "jira", "task.updated")
        assert result is False


# ---------------------------------------------------------------------------
# SignalQualityPipeline end-to-end tests
# ---------------------------------------------------------------------------

class TestSignalQualityPipeline:
    def _make_pipeline(self, redis_returns_duplicate: bool = False):
        """Create a pipeline with Redis fully mocked."""
        pipeline = SignalQualityPipeline(redis_url=None)

        mock_redis = MagicMock()
        # SETNX: None = duplicate, True = new
        mock_redis.set.return_value = None if redis_returns_duplicate else True
        mock_redis.ping.return_value = True

        pipeline._noise_filter._redis = mock_redis
        return pipeline

    def test_pipeline_processes_fresh_jira_signal(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_x",
            "event_type": "task.updated",
            "task_id": "jira-123",
            "new_status": "in_progress",
            "timestamp": _utc(hours_ago=1.0).isoformat(),
        }
        result = pipeline.process(raw, source="jira")
        assert result.is_duplicate is False
        assert result.is_low_signal is False
        assert result.confidence_score > 0.0
        assert result.is_decayed is False
        assert result.sparsity_alert is None

    def test_pipeline_duplicate_event_stops_early(self):
        pipeline = self._make_pipeline(redis_returns_duplicate=True)
        raw = {
            "project_id": "proj_dup",
            "event_type": "task.updated",
            "task_id": "jira-999",
            "new_status": "done",
            "timestamp": _utc(hours_ago=0.5).isoformat(),
        }
        result = pipeline.process(raw, source="jira")
        assert result.is_duplicate is True
        assert result.confidence_score == 0.0
        # Gap detection should NOT run on duplicates
        assert result.gap_alerts == []

    def test_pipeline_stale_source_triggers_is_decayed(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_stale",
            "event_type": "task.updated",
            "task_id": "slack-old",
            "message": "old message",
            "timestamp": _utc(hours_ago=30.0).isoformat(),  # slack decay = 24h
        }
        result = pipeline.process(raw, source="slack")
        assert result.is_decayed is True
        assert result.sparsity_alert is not None
        assert "proj_stale" in result.sparsity_alert

    def test_pipeline_sparsity_alert_contains_project_id_and_timestamp(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_sparse",
            "event_type": "task.updated",
            "task_id": "slack-99",
            "message": "hi",
            "timestamp": _utc(hours_ago=25.0).isoformat(),  # past slack 24h
        }
        result = pipeline.process(raw, source="slack")
        assert result.sparsity_alert is not None
        assert "proj_sparse" in result.sparsity_alert
        # Should contain a parseable ISO timestamp
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T", result.sparsity_alert)

    def test_pipeline_gap_detection_with_state(self):
        pipeline = self._make_pipeline()
        state = _project_state(
            project_id="proj_gap",
            milestones=[
                Milestone(
                    milestone_id="m1",
                    name="Launch",
                    due_date=_future(2),
                    status="at_risk",
                )
            ],
        )
        raw = {
            "project_id": "proj_gap",
            "event_type": "task.updated",
            "task_id": "t1",
            "new_status": "in_progress",
            "timestamp": _utc(hours_ago=1.0).isoformat(),
        }
        result = pipeline.process(
            raw,
            source="jira",
            canonical_state=state,
            last_signal_times={},          # rule 1 fires
            last_completion_time=None,     # rule 2 fires
            last_mitigation_time=None,     # rule 3 fires
        )
        assert len(result.gap_alerts) >= 2
        assert result.sparsity_alert is not None

    def test_pipeline_low_signal_bot_actor_flagged(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_bot",
            "event_type": "task.updated",
            "task_id": "t2",
            "new_status": "in_progress",
            "actor": "dependabot[bot]",
            "timestamp": _utc(hours_ago=0.5).isoformat(),
        }
        result = pipeline.process(raw, source="github")
        assert result.is_low_signal is True

    def test_pipeline_no_gap_detection_when_no_state(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_nostate",
            "event_type": "task.updated",
            "task_id": "t3",
            "new_status": "done",
            "timestamp": _utc(hours_ago=1.0).isoformat(),
        }
        result = pipeline.process(raw, source="jira", canonical_state=None)
        assert result.gap_alerts == []

    def test_pipeline_qualified_at_is_utc(self):
        pipeline = self._make_pipeline()
        raw = {
            "project_id": "proj_ts",
            "event_type": "task.updated",
            "task_id": "t4",
            "new_status": "in_progress",
            "timestamp": _utc(hours_ago=1.0).isoformat(),
        }
        result = pipeline.process(raw, source="jira")
        assert result.qualified_at.tzinfo is not None
