"""MockLLMClient — deterministic LLM responses for offline testing.

Activated by: LLM_PROVIDER=mock (set automatically in tests/conftest.py)
Returns schema-valid JSON for each agent type so all agent unit tests run
without any API key, network call, or Ollama instance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---- Minimal OpenAI-compatible response shape ----

@dataclass
class MockMessage:
    content: str
    role: str = "assistant"


@dataclass
class MockChoice:
    message: MockMessage
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class MockCompletion:
    choices: List[MockChoice]
    model: str = "mock"
    id: str = "mock-completion-001"


# ---- Per-agent response templates ----

_AGENT_RESPONSES: Dict[str, Dict[str, Any]] = {
    "execution_monitoring_agent": {
        "schedule_variance_days": -3,
        "throughput_trend": "declining",
        "bottlenecks": ["task_42 blocked by external dependency"],
        "health_score": 0.68,
        "observation_summary": "Schedule is running 3 days behind. Throughput has declined 15% over the last sprint.",
        "evidence": ["3 open blockers", "velocity dropped from 22 to 18 points"],
        "decision_factors": ["blocker count increased", "velocity trend negative"],
        "uncertainty_notes": ["Only 2 sprints of data available — trend may not be stable"],
        "confidence_score": 0.72,
        "decision_type": "observation",
        "policy_action": "allow_with_audit",
        "recommendation": None,
        "proposed_state_updates": {"health.schedule_health": 0.68},
        "proposed_graph_updates": [],
    },
    "issue_management_agent": {
        "blocker_classification": "external_dependency",
        "root_cause_pattern": "third_party_api_delay",
        "severity": 0.65,
        "affected_tasks": ["task_42", "task_43"],
        "evidence": ["task_42 has status blocked", "dependency on external API confirmed"],
        "decision_factors": ["severity 0.65 exceeds 0.50 threshold", "2 tasks affected"],
        "uncertainty_notes": ["Root cause inferred from labels — no direct confirmation from team"],
        "confidence_score": 0.70,
        "decision_type": "decision_preparation",
        "policy_action": "approval_required",
        "recommendation": "Request ETA update from external team by end of day.",
        "proposed_state_updates": {"health.dependency_health": 0.55},
        "proposed_graph_updates": [
            {"node_type": "Issue", "node_id": "issue_task_42", "action": "upsert"}
        ],
    },
    "risk_intelligence_agent": {
        "risk_score": 0.468,
        "probability": 0.72,
        "impact": 0.65,
        "affected_milestones": ["ms_001"],
        "risk_propagation": ["ms_001 at risk of 7-day slip"],
        "mitigation_options": ["Fast-track alternative vendor", "Descope feature X"],
        "evidence": ["dependency blocked", "milestone due in 18 days"],
        "decision_factors": ["risk_score 0.468 > 0.40 → escalate"],
        "uncertainty_notes": ["Probability estimated from 3 historical analogues — small sample"],
        "confidence_score": 0.76,
        "decision_type": "decision_preparation",
        "policy_action": "escalate",
        "recommendation": "Escalate to program director: milestone ms_001 at risk.",
        "proposed_state_updates": {},
        "proposed_graph_updates": [
            {"node_type": "Risk", "node_id": "risk_001", "action": "upsert"}
        ],
    },
    "communication_agent": {
        "brief_title": "Decision Preparation: Beta Release Milestone at Risk",
        "audience": "executive",
        "body": "The Beta Release milestone (April 15) is at risk due to an external dependency blocker affecting tasks 42 and 43. Risk score is 0.47. Recommended action: approve fast-track vendor alternative or descope feature X.",
        "bullet_points": [
            "Beta milestone at risk — external API delay",
            "Risk score 0.47 — escalation threshold crossed",
            "Two mitigation options available for PM decision",
        ],
        "confidence_disclosure": None,
        "evidence": ["risk_score=0.47", "3 open blockers"],
        "decision_factors": ["threshold crossed", "milestone within 18 days"],
        "uncertainty_notes": ["Brief based on signal from last 4 hours — confirm with team"],
        "confidence_score": 0.80,
        "decision_type": "execution",
        "policy_action": "allow",
        "recommendation": None,
        "proposed_state_updates": {},
        "proposed_graph_updates": [],
    },
    "knowledge_agent": {
        "lessons_extracted": [],
        "retrieved_lessons": [],
        "mitigation_effectiveness": {},
        "cases_available": 0,
        "close_matches": 0,
        "evidence": ["no historical cases in database yet"],
        "decision_factors": ["insufficient history"],
        "uncertainty_notes": [
            "0 historical cases available — all estimates are assumption-based",
            "close_matches=0 — retrieval quality low",
        ],
        "confidence_score": 0.35,
        "decision_type": "observation",
        "policy_action": "allow",
        "recommendation": None,
        "proposed_state_updates": {},
        "proposed_graph_updates": [],
    },
    "planning_agent": {
        "wbs": {"phases": ["Phase 1", "Phase 2"], "total_tasks_estimated": 45},
        "dependencies": ["Phase 2 depends on Phase 1 completion"],
        "resource_estimate": {"low": 3, "high": 5, "unit": "engineers"},
        "duration_estimate": {"low": 6, "high": 9, "unit": "weeks"},
        "historical_similarity_score": 0.0,
        "evidence": ["no historical analogues"],
        "decision_factors": ["empty history → confidence below 0.60"],
        "uncertainty_notes": [
            "0 historical cases — estimates are assumption-based",
            "Resource estimate range is wide due to low confidence",
        ],
        "confidence_score": 0.45,
        "decision_type": "decision_preparation",
        "policy_action": "allow_with_audit",
        "recommendation": "Review estimates with team before committing.",
        "proposed_state_updates": {},
        "proposed_graph_updates": [],
    },
    "program_director_agent": {
        "routing_decision": "escalate_to_human_review",
        "merged_evidence": ["risk_score=0.47", "3 open blockers", "milestone at risk"],
        "conflict_detected": False,
        "policy_action_selected": "escalate",
        "evidence": ["issue_management and risk_intelligence both indicate escalation"],
        "decision_factors": ["most restrictive policy_action across inputs is escalate"],
        "uncertainty_notes": ["Conflict resolution not required — agents in agreement"],
        "confidence_score": 0.78,
        "decision_type": "execution",
        "policy_action": "escalate",
        "recommendation": "Route to human review queue for PM approval.",
        "proposed_state_updates": {},
        "proposed_graph_updates": [],
    },
}

_DEFAULT_RESPONSE: Dict[str, Any] = {
    "summary": "Mock response — no specific template for this agent",
    "evidence": ["mock evidence"],
    "decision_factors": ["mock factor"],
    "uncertainty_notes": ["This is a mock response — real uncertainty unknown"],
    "confidence_score": 0.5,
    "decision_type": "observation",
    "policy_action": "allow",
    "recommendation": None,
    "proposed_state_updates": {},
    "proposed_graph_updates": [],
}


class MockLLMClient:
    """OpenAI-compatible mock client for offline testing.

    Detects agent name from the system message and returns
    a deterministic, schema-valid response for that agent.
    """

    def __init__(self) -> None:
        self.chat = self._ChatCompletions(self)

    class _ChatCompletions:
        def __init__(self, client: "MockLLMClient") -> None:
            self._client = client

        def create(
            self,
            model: str,
            messages: List[Dict[str, str]],
            **kwargs: Any,
        ) -> MockCompletion:
            # Detect agent from system message
            agent_name = "default"
            for msg in messages:
                if msg.get("role") == "system":
                    content = msg.get("content", "").lower()
                    for name in _AGENT_RESPONSES:
                        if name.replace("_agent", "").replace("_", " ") in content:
                            agent_name = name
                            break
                    break

            payload = _AGENT_RESPONSES.get(agent_name, _DEFAULT_RESPONSE)
            content = json.dumps(payload)
            return MockCompletion(choices=[MockChoice(message=MockMessage(content=content))])
