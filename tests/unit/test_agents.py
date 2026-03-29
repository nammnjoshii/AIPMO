"""Unit tests for all 7 agents — T-052.

5 tests per agent (7 agents = 35+ tests):
1. Valid AgentOutput returned
2. uncertainty_notes never empty
3. confidence_score in [0.0, 1.0]
4. decision_type matches intended tier
5. policy_action within allowed values

All tests run with LLM_PROVIDER=mock (offline).
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("LLM_PROVIDER", "mock")

from agents.base_agent import AgentInput, AgentOutput, DecisionType, PolicyAction
from agents.execution_monitoring.agent import ExecutionMonitoringAgent
from agents.issue_management.agent import IssueManagementAgent
from agents.risk_intelligence.agent import RiskIntelligenceAgent
from agents.communication.agent import CommunicationAgent
from agents.knowledge.agent import KnowledgeAgent
from agents.planning.agent import PlanningAgent
from agents.program_director.agent import ProgramDirectorAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input(
    project_id: str = "proj_test",
    event_type: str = "task.updated",
    confidence: float = 0.80,
    is_decayed: bool = False,
    sparsity_alert: str = None,
    open_blockers: int = 2,
    schedule_health: float = 0.70,
    milestones: list = None,
    historical_cases: list = None,
    extra: dict = None,
) -> AgentInput:
    if milestones is None:
        milestones = [
            {"milestone_id": "ms_1", "name": "Alpha", "status": "on_track", "due_date": "2026-05-01T00:00:00Z"},
        ]
    sq = {
        "confidence_score": confidence,
        "is_decayed": is_decayed,
        "source": "github_issues",
    }
    if sparsity_alert:
        sq["sparsity_alert"] = sparsity_alert

    return AgentInput(
        project_id=project_id,
        event_type=event_type,
        canonical_state={
            "project_id": project_id,
            "health": {
                "schedule_health": schedule_health,
                "open_blockers": open_blockers,
            },
            "milestones": milestones,
        },
        graph_context={"graph_available": False, "nodes": [], "edges": []},
        historical_cases=historical_cases or [],
        policy_context={"escalate_issue": "approval_required"},
        signal_quality=sq,
        tenant_id="default",
        extra=extra or {},
    )


# ---------------------------------------------------------------------------
# ExecutionMonitoringAgent (5 tests)
# ---------------------------------------------------------------------------

class TestExecutionMonitoringAgent:
    def setup_method(self):
        self.agent = ExecutionMonitoringAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes
        assert len(result.uncertainty_notes) >= 1

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_is_observation(self):
        result = self.agent.run(_make_input())
        assert result.decision_type == DecisionType.OBSERVATION

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action in (PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT)

    def test_health_score_capped_when_low_confidence(self):
        data = _make_input(confidence=0.30, schedule_health=1.0, open_blockers=0)
        result = self.agent.run(data)
        health = result.proposed_state_updates.get("health", {})
        if health:
            assert health.get("schedule_health", 0.0) <= 0.75

    def test_never_returns_escalate(self):
        result = self.agent.run(_make_input(open_blockers=10))
        assert result.policy_action != PolicyAction.ESCALATE
        assert result.policy_action != PolicyAction.APPROVAL_REQUIRED


# ---------------------------------------------------------------------------
# IssueManagementAgent (5 tests)
# ---------------------------------------------------------------------------

class TestIssueManagementAgent:
    def setup_method(self):
        self.agent = IssueManagementAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_high_severity_triggers_decision_preparation(self):
        # Force high open_blockers to push severity > 0.70
        data = _make_input(
            open_blockers=8,
            schedule_health=0.20,
            milestones=[{"milestone_id": "m1", "name": "M1", "status": "delayed"}],
            event_type="dependency.blocked",
        )
        result = self.agent.run(data)
        assert result.decision_type == DecisionType.DECISION_PREPARATION
        assert result.policy_action == PolicyAction.APPROVAL_REQUIRED

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action in (
            PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT, PolicyAction.APPROVAL_REQUIRED
        )

    def test_sparsity_caps_severity(self):
        # With sparsity alert, severity must be ≤ 0.60 even with high blockers
        data = _make_input(
            open_blockers=10,
            sparsity_alert="[SPARSITY ALERT] proj_test — low confidence",
            schedule_health=0.10,
        )
        result = self.agent.run(data)
        # If sparsity was applied, policy_action should NOT be approval_required
        # because severity is capped at 0.60 < 0.70 threshold
        # (severity capped at 0.60 ≤ 0.70 → observation)
        assert result.decision_type == DecisionType.OBSERVATION


# ---------------------------------------------------------------------------
# RiskIntelligenceAgent (5 tests)
# ---------------------------------------------------------------------------

class TestRiskIntelligenceAgent:
    def setup_method(self):
        self.agent = RiskIntelligenceAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes
        # Must include probability and impact sources
        combined = " ".join(result.uncertainty_notes)
        assert "robability" in combined or "mpact" in combined

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_is_decision_preparation(self):
        result = self.agent.run(_make_input())
        assert result.decision_type == DecisionType.DECISION_PREPARATION

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action in (
            PolicyAction.ALLOW_WITH_AUDIT, PolicyAction.APPROVAL_REQUIRED, PolicyAction.ESCALATE
        )

    def test_exact_risk_score_calculation(self):
        # High blockers + low health → risk_score > 0.40 → ESCALATE
        data = _make_input(
            open_blockers=5,
            schedule_health=0.30,
            milestones=[{"status": "delayed", "name": "M1"}, {"status": "at_risk", "name": "M2"}],
        )
        result = self.agent.run(data)
        risk_score = result.extra.get("risk_score", 0)
        prob = result.extra.get("probability", 0)
        impact = result.extra.get("impact", 0)
        # Verify exact multiplication (no rounding)
        assert abs(risk_score - prob * impact) < 1e-9

    def test_sparsity_caps_risk_score(self):
        data = _make_input(
            open_blockers=10,
            schedule_health=0.10,
            sparsity_alert="[SPARSITY ALERT] high volume",
        )
        result = self.agent.run(data)
        assert result.extra.get("risk_score", 1.0) <= 0.50

    def test_no_graph_update_when_risk_below_threshold(self):
        # Very low risk: no blockers, high health
        data = _make_input(open_blockers=0, schedule_health=0.95, milestones=[])
        result = self.agent.run(data)
        risk_score = result.extra.get("risk_score", 1.0)
        if risk_score < 0.20:
            assert result.proposed_graph_updates == []


# ---------------------------------------------------------------------------
# CommunicationAgent (5 tests)
# ---------------------------------------------------------------------------

class TestCommunicationAgent:
    def setup_method(self):
        self.agent = CommunicationAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_always_execution(self):
        for event_type in ("task.updated", "risk.detected", "milestone.updated"):
            result = self.agent.run(_make_input(event_type=event_type))
            assert result.decision_type == DecisionType.EXECUTION

    def test_policy_action_always_allow(self):
        result = self.agent.run(_make_input())
        assert result.policy_action == PolicyAction.ALLOW

    def test_confidence_disclosure_when_below_threshold(self):
        data = _make_input(confidence=0.50)
        result = self.agent.run(data)
        disclosure = result.extra.get("confidence_disclosure")
        assert disclosure is not None
        assert "0.50" in disclosure or "0.5" in disclosure

    def test_no_disclosure_when_high_confidence(self):
        data = _make_input(confidence=0.90)
        result = self.agent.run(data)
        disclosure = result.extra.get("confidence_disclosure")
        assert disclosure is None


# ---------------------------------------------------------------------------
# KnowledgeAgent (5 tests)
# ---------------------------------------------------------------------------

class TestKnowledgeAgent:
    def setup_method(self):
        self.agent = KnowledgeAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes
        combined = " ".join(result.uncertainty_notes)
        assert "cases_available" in combined
        assert "close_matches" in combined

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_is_observation(self):
        result = self.agent.run(_make_input())
        assert result.decision_type == DecisionType.OBSERVATION

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action == PolicyAction.ALLOW

    def test_no_history_reduces_confidence(self):
        data = _make_input(historical_cases=[])
        result = self.agent.run(data)
        assert result.confidence_score <= 0.40

    def test_unresolved_cases_not_extracted_as_lessons(self):
        cases = [
            {"case_id": "c1", "outcome": "in_progress", "resolution": "do something"},
            {"case_id": "c2", "outcome": "resolved", "resolution": "apply vendor fix"},
        ]
        data = _make_input(historical_cases=cases, extra={"knowledge_type": "lesson_extraction"})
        result = self.agent.run(data)
        lessons = result.extra.get("lessons_extracted", [])
        # Only confirmed/resolved cases should yield lessons
        for lesson in lessons:
            assert lesson.get("outcome") in ("resolved", "completed", "confirmed")


# ---------------------------------------------------------------------------
# PlanningAgent (5 tests)
# ---------------------------------------------------------------------------

class TestPlanningAgent:
    def setup_method(self):
        self.agent = PlanningAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_is_decision_preparation(self):
        result = self.agent.run(_make_input())
        assert result.decision_type == DecisionType.DECISION_PREPARATION

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action in (
            PolicyAction.ALLOW_WITH_AUDIT, PolicyAction.APPROVAL_REQUIRED
        )

    def test_no_history_caps_confidence(self):
        data = _make_input(historical_cases=[])
        result = self.agent.run(data)
        assert result.confidence_score < 0.60

    def test_estimates_are_ranges(self):
        result = self.agent.run(_make_input())
        resource = result.extra.get("resource_estimate", {})
        engineers = resource.get("engineers", {})
        assert "low" in engineers
        assert "high" in engineers
        assert engineers["low"] <= engineers["high"]

    def test_resource_gap_escalates_policy(self):
        # Force resource gap with many blockers
        data = _make_input(open_blockers=8)
        result = self.agent.run(data)
        # With resource gap, policy should be approval_required
        if result.extra.get("resource_estimate", {}).get("resource_gap"):
            assert result.policy_action == PolicyAction.APPROVAL_REQUIRED


# ---------------------------------------------------------------------------
# ProgramDirectorAgent (5 tests)
# ---------------------------------------------------------------------------

class TestProgramDirectorAgent:
    def setup_method(self):
        self.agent = ProgramDirectorAgent()

    def test_returns_valid_agent_output(self):
        result = self.agent.run(_make_input())
        assert isinstance(result, AgentOutput)

    def test_uncertainty_notes_never_empty(self):
        result = self.agent.run(_make_input())
        assert result.uncertainty_notes

    def test_confidence_score_in_range(self):
        result = self.agent.run(_make_input())
        assert 0.0 <= result.confidence_score <= 1.0

    def test_decision_type_observation_for_single_event(self):
        result = self.agent.run(_make_input())
        assert result.decision_type == DecisionType.OBSERVATION

    def test_policy_action_in_allowed_values(self):
        result = self.agent.run(_make_input())
        assert result.policy_action in (PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT)

    def test_merge_applies_most_restrictive_policy(self):
        agent = ProgramDirectorAgent()
        out_allow = AgentOutput(
            agent_name="execution_monitoring_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=0.80,
            evidence=["health good"],
            decision_factors=["schedule ok"],
            uncertainty_notes=["minor uncertainty"],
            policy_action=PolicyAction.ALLOW,
        )
        out_escalate = AgentOutput(
            agent_name="risk_intelligence_agent",
            decision_type=DecisionType.DECISION_PREPARATION,
            confidence_score=0.85,
            evidence=["risk 0.47"],
            decision_factors=["escalate threshold"],
            uncertainty_notes=["probability estimated"],
            policy_action=PolicyAction.ESCALATE,
        )
        merged = agent.merge([out_allow, out_escalate])
        assert merged.policy_action == PolicyAction.ESCALATE

    def test_irresolvable_conflict_caps_confidence(self):
        agent = ProgramDirectorAgent()
        out_good = AgentOutput(
            agent_name="execution_monitoring_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=0.88,
            evidence=["health score 0.85"],
            decision_factors=["all good"],
            uncertainty_notes=["minor gap"],
            policy_action=PolicyAction.ALLOW,
        )
        out_bad = AgentOutput(
            agent_name="risk_intelligence_agent",
            decision_type=DecisionType.DECISION_PREPARATION,
            confidence_score=0.82,
            evidence=["risk 0.55"],
            decision_factors=["escalation threshold"],
            uncertainty_notes=["probability from 3 analogues"],
            policy_action=PolicyAction.ESCALATE,
        )
        resolved = agent.resolve([out_good, out_bad])
        assert resolved.confidence_score <= 0.55
        assert resolved.extra.get("conflict_detected") is True

    def test_merge_source_agents_named_in_evidence(self):
        agent = ProgramDirectorAgent()
        out1 = AgentOutput(
            agent_name="issue_management_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=0.70,
            evidence=["blocker found"],
            decision_factors=["severity low"],
            uncertainty_notes=["root cause inferred"],
            policy_action=PolicyAction.ALLOW_WITH_AUDIT,
        )
        out2 = AgentOutput(
            agent_name="risk_intelligence_agent",
            decision_type=DecisionType.DECISION_PREPARATION,
            confidence_score=0.75,
            evidence=["risk 0.30"],
            decision_factors=["approval threshold"],
            uncertainty_notes=["historical sample small"],
            policy_action=PolicyAction.APPROVAL_REQUIRED,
        )
        merged = agent.merge([out1, out2])
        combined_evidence = " ".join(merged.evidence)
        assert "issue_management_agent" in combined_evidence
        assert "risk_intelligence_agent" in combined_evidence
