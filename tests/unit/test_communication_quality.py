"""Communication Agent quality tests — T-054.

Tests:
- No banned phrases in brief body
- Confidence disclosure fires at < 0.65
- Executive brief has ≤ 5 bullet points
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("LLM_PROVIDER", "mock")

from agents.base_agent import AgentInput, PolicyAction
from agents.communication.agent import CommunicationAgent, _CONFIDENCE_DISCLOSURE_THRESHOLD
from agents.communication.prompts import BANNED_PHRASES


def _make_comm_input(confidence: float = 0.80, audience: str = "executive") -> AgentInput:
    return AgentInput(
        project_id="proj_comm_test",
        event_type="risk.detected",
        canonical_state={
            "project_id": "proj_comm_test",
            "health": {"schedule_health": 0.65, "open_blockers": 2},
            "milestones": [{"name": "Beta", "status": "at_risk"}],
        },
        graph_context={"graph_available": False, "nodes": [], "edges": []},
        historical_cases=[],
        policy_context={},
        signal_quality={"confidence_score": confidence, "is_decayed": False},
        extra={"audience": audience, "agent_outputs": []},
    )


class TestCommunicationQuality:
    def setup_method(self):
        self.agent = CommunicationAgent()

    def test_no_banned_phrases_in_body(self):
        result = self.agent.run(_make_comm_input())
        body = result.extra.get("body", "")
        for phrase in BANNED_PHRASES:
            assert phrase not in body, f"Banned phrase '{phrase}' found in brief body"

    def test_confidence_disclosure_fires_below_threshold(self):
        result = self.agent.run(_make_comm_input(confidence=0.50))
        disclosure = result.extra.get("confidence_disclosure")
        assert disclosure is not None, "Confidence disclosure must be present when confidence < 0.65"
        assert len(disclosure) > 10

    def test_confidence_disclosure_absent_above_threshold(self):
        result = self.agent.run(_make_comm_input(confidence=0.80))
        disclosure = result.extra.get("confidence_disclosure")
        assert disclosure is None, "No confidence disclosure should appear at high confidence"

    def test_disclosure_threshold_exactly_at_boundary(self):
        # exactly at threshold (0.65) should NOT trigger disclosure
        result = self.agent.run(_make_comm_input(confidence=_CONFIDENCE_DISCLOSURE_THRESHOLD))
        disclosure = result.extra.get("confidence_disclosure")
        assert disclosure is None, "Disclosure should not fire at exactly the threshold value"

    def test_executive_brief_max_5_bullets(self):
        result = self.agent.run(_make_comm_input(audience="executive"))
        bullets = result.extra.get("bullets", [])
        assert len(bullets) <= 5, f"Executive brief has {len(bullets)} bullets (max 5)"

    def test_policy_action_always_allow(self):
        for confidence in (0.30, 0.65, 0.99):
            result = self.agent.run(_make_comm_input(confidence=confidence))
            assert result.policy_action == PolicyAction.ALLOW

    def test_decision_type_always_execution(self):
        from agents.base_agent import DecisionType
        result = self.agent.run(_make_comm_input())
        assert result.decision_type == DecisionType.EXECUTION

    def test_banned_phrases_list_has_expected_count(self):
        assert len(BANNED_PHRASES) >= 8, (
            f"BANNED_PHRASES has {len(BANNED_PHRASES)} entries, expected >= 8"
        )

    def test_brief_contains_project_id(self):
        result = self.agent.run(_make_comm_input())
        title = result.extra.get("brief_title", "")
        body = result.extra.get("body", "")
        assert "proj_comm_test" in title or "proj_comm_test" in body
