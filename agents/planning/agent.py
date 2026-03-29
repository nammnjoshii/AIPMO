"""Planning Agent.

Decision type: DECISION_PREPARATION.
Skills: wbs_generation, dependency_mapping, resource_need_estimation, historical_project_similarity.

Hard rules:
- All estimates must be ranges (low, high)
- No historical cases → confidence_score < 0.60, labeled 'assumption-based'
- Resource gap identified when plan exceeds capacity
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

_MAX_CONFIDENCE_NO_HISTORY = 0.55


class PlanningAgent(BaseAgent):
    """Generates plans and estimates for delivery decisions.

    Decision tier: decision_preparation — human must review estimates.
    """

    def __init__(self) -> None:
        super().__init__("planning_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        confidence = data.signal_quality.get("confidence_score", 0.5)
        is_decayed = data.signal_quality.get("is_decayed", False)
        planning_type = data.extra.get("planning_type", "wbs_generation")

        cases_available = len(data.historical_cases) if data.historical_cases else 0
        assumption_based = cases_available == 0

        wbs = self._wbs_generation(data)
        deps = self._dependency_mapping(data, wbs)
        resource = self._resource_need_estimation(data, wbs, cases_available)
        similarity = self._historical_project_similarity(data)

        # Cap confidence when no history
        if assumption_based:
            confidence = min(confidence, _MAX_CONFIDENCE_NO_HISTORY)

        evidence = self._collect_evidence(wbs, deps, resource, similarity, data)
        decision_factors = self._collect_decision_factors(cases_available, assumption_based, resource)
        uncertainty_notes = self._build_uncertainty_notes(
            confidence, is_decayed, assumption_based, resource, data
        )
        recommendation = self._build_recommendation(resource, wbs, assumption_based)

        has_gap = resource.get("resource_gap", False)
        policy_action = PolicyAction.APPROVAL_REQUIRED if has_gap else PolicyAction.ALLOW_WITH_AUDIT

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.DECISION_PREPARATION,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=decision_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=policy_action,
            recommendation=recommendation,
            proposed_state_updates={},
            proposed_graph_updates=[],
            extra={
                "wbs": wbs,
                "dependencies": deps,
                "resource_estimate": resource,
                "historical_similarity_score": similarity,
                "planning_type": planning_type,
            },
        )

    # ---- Skills ----

    def _wbs_generation(self, data: AgentInput) -> Dict[str, Any]:
        """Generate a work breakdown structure from canonical state."""
        milestones = data.canonical_state.get("milestones", []) or []
        phases = []
        for ms in milestones:
            name = ms.get("name", ms.get("milestone_id", "Phase"))
            phases.append(name)
        if not phases:
            phases = ["Discovery", "Development", "Testing", "Deployment"]

        return {
            "phases": phases,
            "total_tasks_estimated": {"low": len(phases) * 8, "high": len(phases) * 15},
            "basis": "milestone structure" if milestones else "default template",
        }

    def _dependency_mapping(self, data: AgentInput, wbs: Dict[str, Any]) -> List[str]:
        """Map dependencies from graph context and WBS."""
        graph = data.graph_context or {}
        edges = graph.get("edges", [])
        deps = []
        for edge in edges[:5]:
            src = edge.get("from", "")
            tgt = edge.get("to", "")
            if src and tgt:
                deps.append(f"{src} → {tgt}")

        phases = wbs.get("phases", [])
        for i in range(1, len(phases)):
            deps.append(f"{phases[i]} depends on {phases[i-1]} completion")

        return deps[:10]

    def _resource_need_estimation(
        self, data: AgentInput, wbs: Dict[str, Any], cases_available: int
    ) -> Dict[str, Any]:
        """Estimate resource needs as a range (low, high)."""
        phases = wbs.get("phases", [])
        base_engineers_low = max(2, len(phases))
        base_engineers_high = base_engineers_low * 2

        # Duration range based on task estimate
        task_est = wbs.get("total_tasks_estimated", {"low": 20, "high": 40})
        duration_low = max(2, task_est.get("low", 20) // 10)
        duration_high = max(4, task_est.get("high", 40) // 8)

        # Check capacity — use canonical state health as proxy
        health = data.canonical_state.get("health", {})
        open_blockers = int(health.get("open_blockers", 0) or 0)
        resource_gap = open_blockers > 3 or base_engineers_high > 8

        return {
            "engineers": {"low": base_engineers_low, "high": base_engineers_high},
            "duration_weeks": {"low": duration_low, "high": duration_high},
            "resource_gap": resource_gap,
            "basis": "historical analogue" if cases_available > 0 else "assumption-based template",
        }

    def _historical_project_similarity(self, data: AgentInput) -> float:
        """Return similarity score against historical projects (0.0 if no history)."""
        cases = data.historical_cases or []
        if not cases:
            return 0.0
        scores = [c.get("similarity_score", 0.0) for c in cases if isinstance(c, dict)]
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    # ---- Internal helpers ----

    def _collect_evidence(
        self,
        wbs: Dict,
        deps: List[str],
        resource: Dict,
        similarity: float,
        data: AgentInput,
    ) -> List[str]:
        eng = resource.get("engineers", {})
        dur = resource.get("duration_weeks", {})
        return [
            f"WBS phases: {wbs.get('phases', [])}",
            f"Dependencies mapped: {len(deps)}",
            f"Resource estimate: {eng.get('low', '?')}-{eng.get('high', '?')} engineers, "
            f"{dur.get('low', '?')}-{dur.get('high', '?')} weeks",
            f"Historical similarity score: {similarity:.3f}",
            f"Estimate basis: {resource.get('basis', 'unknown')}",
        ]

    def _collect_decision_factors(
        self, cases_available: int, assumption_based: bool, resource: Dict
    ) -> List[str]:
        factors = []
        if assumption_based:
            factors.append(f"0 historical cases — estimates labeled assumption-based, confidence capped at {_MAX_CONFIDENCE_NO_HISTORY}")
        else:
            factors.append(f"{cases_available} historical cases used for calibration")
        if resource.get("resource_gap"):
            factors.append("Resource gap detected — plan exceeds estimated available capacity")
        return factors or ["Planning completed with available data"]

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        assumption_based: bool,
        resource: Dict,
        data: AgentInput,
    ) -> List[str]:
        notes = []
        if assumption_based:
            notes.append(
                f"All estimates are assumption-based (0 historical analogues). "
                f"Confidence capped at {_MAX_CONFIDENCE_NO_HISTORY}."
            )
        eng = resource.get("engineers", {})
        dur = resource.get("duration_weeks", {})
        notes.append(
            f"Resource range {eng.get('low', '?')}-{eng.get('high', '?')} engineers derived from "
            f"WBS phase count and task estimate — verify with team."
        )
        notes.append(
            f"Duration range {dur.get('low', '?')}-{dur.get('high', '?')} weeks based on "
            f"task throughput heuristic (8-10 tasks/engineer/week)."
        )
        if is_decayed:
            notes.append("Signal data is decayed — canonical state may not reflect current project scope.")
        return notes

    def _build_recommendation(
        self, resource: Dict, wbs: Dict, assumption_based: bool
    ) -> str:
        suffix = " (assumption-based — validate with team before committing)" if assumption_based else ""
        if resource.get("resource_gap"):
            eng = resource.get("engineers", {})
            return (
                f"Resource gap detected: plan requires {eng.get('high', '?')} engineers. "
                f"Request resource allocation approval before proceeding.{suffix}"
            )
        return f"Review estimates with PM before committing to delivery plan.{suffix}"
