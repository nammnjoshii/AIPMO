"""Policy engine tests — all five outcomes, fail-closed behavior, thresholds, reload.

Every test must pass before any agent is built (per INSTRUCTIONS.md Phase 3).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from agents.base_agent import PolicyAction
from policy.engine import PolicyEngine, _load_yaml_policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_POLICY = {
    "version": "1.0",
    "scope": "global",
    "actions": {
        "generate_status_report": "allow",
        "create_risk_log_entry": "allow_with_audit",
        "escalate_issue": "approval_required",
        "modify_schedule": "deny",
    },
    "thresholds": {
        "schedule_slip_probability": {"escalate_if_greater_than": 0.40},
        "resource_overload": {"notify_if_greater_than": 0.30},
        "risk_score": {
            "escalate_if_greater_than": 0.40,
            "approval_required_if_greater_than": 0.20,
        },
        "confidence_score": {"sparsity_alert_if_less_than": 0.50},
    },
    "unknown_action_default": "deny",
}


def _write_policy(d: dict, tmp_path: Path) -> str:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.dump(d))
    return str(path)


def _engine(policy_dict: dict, tmp_path: Path) -> PolicyEngine:
    engine = PolicyEngine()
    engine.load(_write_policy(policy_dict, tmp_path))
    return engine


# ---------------------------------------------------------------------------
# Action outcome tests
# ---------------------------------------------------------------------------

class TestActionOutcomes:
    def test_allow_action(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("generate_status_report", "proj_1")
        assert result.policy_action == PolicyAction.ALLOW

    def test_allow_with_audit_action(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("create_risk_log_entry", "proj_1")
        assert result.policy_action == PolicyAction.ALLOW_WITH_AUDIT

    def test_approval_required_action(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("escalate_issue", "proj_1")
        assert result.policy_action == PolicyAction.APPROVAL_REQUIRED
        assert result.requires_human_approval is True

    def test_deny_action(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("modify_schedule", "proj_1")
        assert result.policy_action == PolicyAction.DENY

    def test_unknown_action_defaults_to_deny(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("nonexistent_action_xyz", "proj_1")
        assert result.policy_action == PolicyAction.DENY

    def test_result_contains_action_and_project(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("generate_status_report", "proj_abc", agent_name="comm_agent")
        assert result.action == "generate_status_report"
        assert result.project_id == "proj_abc"
        assert result.agent_name == "comm_agent"


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_engine_exception_returns_deny(self, tmp_path):
        """If policy engine throws internally, must return DENY (not raise)."""
        engine = PolicyEngine()
        # Force an exception by corrupting internal state
        engine._policies = None  # type: ignore[assignment]

        result = engine.evaluate("any_action", "proj_x")
        assert result.policy_action == PolicyAction.DENY
        assert "error" in result.justification.lower()

    def test_threshold_exception_returns_deny(self, tmp_path):
        engine = PolicyEngine()
        engine._policies = None  # type: ignore[assignment]

        result = engine.evaluate_threshold("risk_score", 0.5, "proj_x")
        assert result.policy_action == PolicyAction.DENY

    def test_no_loaded_policy_unknown_action_denies(self):
        """Engine with no loaded policies must deny unknown actions."""
        engine = PolicyEngine()
        result = engine.evaluate("some_action", "proj_1")
        assert result.policy_action == PolicyAction.DENY


# ---------------------------------------------------------------------------
# Regulatory hardcoded rules
# ---------------------------------------------------------------------------

class TestRegulatoryRules:
    def test_delete_audit_record_always_denied(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("delete_audit_record", "proj_1")
        assert result.policy_action == PolicyAction.DENY

    def test_bulk_data_export_requires_approval(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate("bulk_data_export", "proj_1")
        assert result.policy_action == PolicyAction.APPROVAL_REQUIRED

    def test_regulatory_deny_cannot_be_overridden_by_yaml(self, tmp_path):
        """Even if a YAML policy says 'allow' for delete_audit_record, regulatory rule wins."""
        policy = {**_DEFAULT_POLICY, "actions": {"delete_audit_record": "allow"}}
        engine = _engine(policy, tmp_path)
        result = engine.evaluate("delete_audit_record", "proj_1")
        assert result.policy_action == PolicyAction.DENY


# ---------------------------------------------------------------------------
# Threshold tests
# ---------------------------------------------------------------------------

class TestThresholdEvaluation:
    def test_threshold_above_escalate_returns_escalate(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("schedule_slip_probability", 0.55, "proj_1")
        assert result.policy_action == PolicyAction.ESCALATE

    def test_threshold_below_escalate_returns_allow(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("schedule_slip_probability", 0.30, "proj_1")
        assert result.policy_action == PolicyAction.ALLOW

    def test_threshold_above_notify_returns_allow_with_audit(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("resource_overload", 0.35, "proj_1")
        assert result.policy_action == PolicyAction.ALLOW_WITH_AUDIT

    def test_threshold_risk_score_above_approval(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("risk_score", 0.25, "proj_1")
        assert result.policy_action == PolicyAction.APPROVAL_REQUIRED

    def test_threshold_risk_score_above_escalate(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("risk_score", 0.50, "proj_1")
        assert result.policy_action == PolicyAction.ESCALATE

    def test_threshold_sparsity_alert(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("confidence_score", 0.30, "proj_1")
        assert result.policy_action == PolicyAction.ALLOW_WITH_AUDIT

    def test_undefined_threshold_metric_returns_allow(self, tmp_path):
        engine = _engine(_DEFAULT_POLICY, tmp_path)
        result = engine.evaluate_threshold("unknown_metric", 999.0, "proj_1")
        assert result.policy_action == PolicyAction.ALLOW


# ---------------------------------------------------------------------------
# Project-specific policy overrides global
# ---------------------------------------------------------------------------

class TestProjectPolicyPrecedence:
    def test_project_policy_overrides_global(self, tmp_path):
        """Project-specific 'allow' for an action that global marks as 'deny'."""
        global_dir = tmp_path / "g"
        global_dir.mkdir()
        global_policy = _write_policy(_DEFAULT_POLICY, global_dir)

        proj_dir = tmp_path / "p"
        proj_dir.mkdir()
        project_policy_dict = {
            "version": "2.0",
            "scope": "project",
            "project_id": "proj_override",
            "actions": {"modify_schedule": "allow"},
            "unknown_action_default": "deny",
        }
        project_policy = _write_policy(project_policy_dict, proj_dir)

        engine = PolicyEngine()
        engine.load(global_policy)
        engine.load(project_policy)

        result = engine.evaluate("modify_schedule", "proj_override")
        assert result.policy_action == PolicyAction.ALLOW

    def test_project_policy_falls_back_to_global(self, tmp_path):
        """Action not in project policy → fall through to global."""
        global_dir = tmp_path / "g"
        global_dir.mkdir()
        global_policy = _write_policy(_DEFAULT_POLICY, global_dir)

        proj_dir = tmp_path / "p"
        proj_dir.mkdir()
        project_policy_dict = {
            "version": "1.0",
            "scope": "project",
            "project_id": "proj_fallback",
            "actions": {},
            "unknown_action_default": "deny",
        }
        project_policy = _write_policy(project_policy_dict, proj_dir)

        engine = PolicyEngine()
        engine.load(global_policy)
        engine.load(project_policy)

        result = engine.evaluate("generate_status_report", "proj_fallback")
        assert result.policy_action == PolicyAction.ALLOW


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

class TestPolicyReload:
    def test_reload_updates_in_memory_rules(self, tmp_path):
        """Reload replaces the policy without restart."""
        policy_path = tmp_path / "reload_test.yaml"
        policy_v1 = {**_DEFAULT_POLICY, "actions": {"test_action": "allow"}}
        policy_path.write_text(yaml.dump(policy_v1))

        engine = PolicyEngine()
        engine.load(str(policy_path))

        assert engine.evaluate("test_action", "p1").policy_action == PolicyAction.ALLOW

        # Update the file
        policy_v2 = {**_DEFAULT_POLICY, "actions": {"test_action": "deny"}}
        policy_path.write_text(yaml.dump(policy_v2))

        engine.reload()
        assert engine.evaluate("test_action", "p1").policy_action == PolicyAction.DENY

    def test_reload_with_no_config_path_is_noop(self):
        """Reload without prior load() does nothing and doesn't raise."""
        engine = PolicyEngine()
        engine.reload()  # Should not raise


# ---------------------------------------------------------------------------
# YAML validation
# ---------------------------------------------------------------------------

class TestYAMLValidation:
    def test_validate_valid_policy_file(self, tmp_path):
        path = _write_policy(_DEFAULT_POLICY, tmp_path)
        policy = _load_yaml_policy(path)
        assert policy.version == "1.0"

    def test_validate_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_yaml_policy(str(tmp_path / "nonexistent.yaml"))

    def test_validate_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with pytest.raises(ValueError):
            _load_yaml_policy(str(empty))
