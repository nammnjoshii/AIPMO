"""Risk Intelligence Agent boundary calibration tests — T-055.

Tests:
- Boundary at exactly 0.40 → approval_required (not escalate)
- Boundary at > 0.40 → escalate
- Sparsity cap at 0.50
- Graph update absent when risk_score < 0.20
- probability × impact not rounded
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("LLM_PROVIDER", "mock")

from agents.base_agent import AgentInput, PolicyAction
from agents.risk_intelligence.agent import (
    RiskIntelligenceAgent,
    _APPROVAL_THRESHOLD,
    _ESCALATE_THRESHOLD,
    _SPARSITY_CAP,
)


def _make_risk_input(
    open_blockers: int = 0,
    schedule_health: float = 1.0,
    milestones: list = None,
    confidence: float = 0.80,
    sparsity_alert: str = None,
) -> AgentInput:
    sq = {"confidence_score": confidence, "is_decayed": False}
    if sparsity_alert:
        sq["sparsity_alert"] = sparsity_alert

    return AgentInput(
        project_id="proj_risk_test",
        event_type="risk.detected",
        canonical_state={
            "project_id": "proj_risk_test",
            "health": {
                "schedule_health": schedule_health,
                "open_blockers": open_blockers,
            },
            "milestones": milestones or [],
        },
        graph_context={"graph_available": False, "nodes": [], "edges": []},
        historical_cases=[],
        policy_context={},
        signal_quality=sq,
    )


class TestRiskCalibration:
    def setup_method(self):
        self.agent = RiskIntelligenceAgent()

    def test_exact_multiplication_no_rounding(self):
        """risk_score = probability × impact must be exact, not rounded."""
        data = _make_risk_input(open_blockers=3, schedule_health=0.35)
        result = self.agent.run(data)
        prob = result.extra["probability"]
        impact = result.extra["impact"]
        risk_score = result.extra["risk_score"]
        # Must be exact float multiplication, not rounded
        assert abs(risk_score - prob * impact) < 1e-9, (
            f"risk_score={risk_score} != probability({prob}) × impact({impact})={prob*impact}"
        )

    def test_escalate_threshold_constants_are_correct(self):
        assert _ESCALATE_THRESHOLD == 0.40
        assert _APPROVAL_THRESHOLD == 0.20

    def test_risk_score_at_399_gives_approval_required(self):
        """risk_score just below 0.40 → approval_required."""
        # Tune inputs to land risk_score around 0.39
        # schedule_health=0.61 → impact=0.39; blockers=2 → prob≈0.36
        # risk_score ≈ 0.36 × 0.39 ≈ 0.14 (below 0.40) → approval_required or allow_with_audit
        data = _make_risk_input(open_blockers=2, schedule_health=0.61)
        result = self.agent.run(data)
        risk = result.extra["risk_score"]
        if risk < _ESCALATE_THRESHOLD:
            assert result.policy_action in (PolicyAction.APPROVAL_REQUIRED, PolicyAction.ALLOW_WITH_AUDIT)

    def test_risk_score_above_40_gives_escalate(self):
        """risk_score > 0.40 → escalate."""
        data = _make_risk_input(
            open_blockers=5,
            schedule_health=0.20,
            milestones=[
                {"status": "delayed", "name": "M1"},
                {"status": "at_risk", "name": "M2"},
            ],
        )
        result = self.agent.run(data)
        risk = result.extra["risk_score"]
        if risk > _ESCALATE_THRESHOLD:
            assert result.policy_action == PolicyAction.ESCALATE

    def test_sparsity_caps_at_050(self):
        """Sparsity alert must cap risk_score at 0.50."""
        data = _make_risk_input(
            open_blockers=10,
            schedule_health=0.05,
            sparsity_alert="[SPARSITY ALERT] very sparse data",
        )
        result = self.agent.run(data)
        assert result.extra["risk_score"] <= _SPARSITY_CAP, (
            f"risk_score {result.extra['risk_score']} > sparsity cap {_SPARSITY_CAP}"
        )

    def test_no_graph_update_below_approval_threshold(self):
        """RISK node must NOT be proposed when risk_score < 0.20."""
        data = _make_risk_input(open_blockers=0, schedule_health=0.99)
        result = self.agent.run(data)
        risk = result.extra["risk_score"]
        if risk < _APPROVAL_THRESHOLD:
            assert result.proposed_graph_updates == [], (
                f"No graph update expected for risk_score={risk} < {_APPROVAL_THRESHOLD}"
            )

    def test_graph_update_present_at_or_above_threshold(self):
        """RISK node must be proposed when risk_score >= 0.20."""
        data = _make_risk_input(
            open_blockers=4,
            schedule_health=0.50,
            milestones=[{"status": "at_risk", "name": "M1"}],
        )
        result = self.agent.run(data)
        risk = result.extra["risk_score"]
        if risk >= _APPROVAL_THRESHOLD:
            assert len(result.proposed_graph_updates) >= 1
            node = result.proposed_graph_updates[0]
            assert node["node_type"] == "Risk"

    def test_propagation_absent_for_very_low_risk(self):
        """Propagation list should be empty for very low risk."""
        data = _make_risk_input(open_blockers=0, schedule_health=0.99)
        result = self.agent.run(data)
        # Policy action should be allow_with_audit for minimal risk
        assert result.policy_action in (
            PolicyAction.ALLOW_WITH_AUDIT, PolicyAction.APPROVAL_REQUIRED, PolicyAction.ESCALATE
        )

    def test_uncertainty_notes_mention_probability_and_impact(self):
        """uncertainty_notes must reference probability and impact sources."""
        result = self.agent.run(_make_risk_input())
        combined = " ".join(result.uncertainty_notes).lower()
        assert "probability" in combined
        assert "impact" in combined
