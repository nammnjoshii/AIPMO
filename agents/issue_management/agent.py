"""Issue Management Agent.

Decision type: OBSERVATION (severity ≤ 0.70) or DECISION_PREPARATION (severity > 0.70).
Skills: blocker_classification, root_cause_pattern_matching, severity_estimation.

Hard rules:
- severity > 0.70 → DECISION_PREPARATION + APPROVAL_REQUIRED
- sparsity_alert present → cap severity at 0.60
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

_HIGH_SEVERITY_THRESHOLD = 0.70
_SPARSITY_SEVERITY_CAP = 0.60


class IssueManagementAgent(BaseAgent):
    """Classifies blockers and estimates severity to drive escalation routing.

    Decision tier: observation (severity ≤ 0.70) or decision_preparation (severity > 0.70).
    """

    def __init__(self) -> None:
        super().__init__("issue_management_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        """Classify blockers, match root cause patterns, estimate severity."""
        confidence = data.signal_quality.get("confidence_score", 0.5)
        sparsity_alert = data.signal_quality.get("sparsity_alert")
        is_decayed = data.signal_quality.get("is_decayed", False)

        classification = self._blocker_classification(data)
        root_cause = self._root_cause_pattern_matching(data, classification)
        severity = self._severity_estimation(data, classification, root_cause)

        # Apply sparsity cap before threshold comparison
        if sparsity_alert:
            severity = min(severity, _SPARSITY_SEVERITY_CAP)

        if severity > _HIGH_SEVERITY_THRESHOLD:
            decision_type = DecisionType.DECISION_PREPARATION
            policy_action = PolicyAction.APPROVAL_REQUIRED
        else:
            decision_type = DecisionType.OBSERVATION
            policy_action = PolicyAction.ALLOW_WITH_AUDIT

        evidence = self._collect_evidence(classification, root_cause, severity, data)
        decision_factors = self._collect_decision_factors(severity, sparsity_alert)
        uncertainty_notes = self._build_uncertainty_notes(confidence, is_decayed, sparsity_alert, data)
        recommendation = self._build_recommendation(severity, classification, root_cause)

        proposed_state: Dict[str, Any] = {}
        proposed_graph: List[Dict[str, Any]] = []
        if severity > 0.40:
            proposed_state = {"health": {"open_blockers": self._count_blockers(data)}}
            proposed_graph = [
                {"node_type": "Issue", "node_id": f"issue_{data.project_id}", "action": "upsert", "severity": severity}
            ]

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=decision_type,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=decision_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=policy_action,
            recommendation=recommendation,
            proposed_state_updates=proposed_state,
            proposed_graph_updates=proposed_graph,
        )

    # ---- Skills ----

    def _blocker_classification(self, data: AgentInput) -> str:
        """Classify the primary blocker type from event payload and state."""
        event_type = data.event_type
        payload = data.extra.get("payload", {})

        if "dependency" in event_type or payload.get("blocked_by"):
            return "external_dependency"
        if "capacity" in str(payload).lower():
            return "internal_capacity"
        if "scope" in str(payload).lower():
            return "scope_ambiguity"
        if data.canonical_state.get("health", {}).get("open_blockers", 0) > 3:
            return "resource_conflict"
        return "unknown"

    def _root_cause_pattern_matching(self, data: AgentInput, classification: str) -> str:
        """Match root cause pattern from historical cases and classification."""
        cases = data.historical_cases or []
        if cases:
            # Use most common root cause from historical matches
            patterns = [c.get("root_cause_pattern", "unknown_pattern") for c in cases if isinstance(c, dict)]
            if patterns:
                return max(set(patterns), key=patterns.count)

        if classification == "external_dependency":
            return "third_party_api_delay"
        if classification == "internal_capacity":
            return "team_capacity_overload"
        if classification == "scope_ambiguity":
            return "unclear_requirements"
        return "unknown_pattern"

    def _severity_estimation(
        self,
        data: AgentInput,
        classification: str,
        root_cause: str,
    ) -> float:
        """Estimate issue severity (0.0-1.0) from multiple factors."""
        base = 0.40

        # Blocker count contribution
        open_blockers = data.canonical_state.get("health", {}).get("open_blockers", 0)
        if isinstance(open_blockers, (int, float)):
            base += min(open_blockers * 0.05, 0.20)

        # Classification weight
        severity_weights = {
            "external_dependency": 0.10,
            "resource_conflict": 0.15,
            "internal_capacity": 0.05,
            "scope_ambiguity": 0.05,
            "unknown": 0.00,
        }
        base += severity_weights.get(classification, 0.0)

        # Milestone proximity contribution
        milestones = data.canonical_state.get("milestones", [])
        at_risk = [m for m in milestones if m.get("status") in ("at_risk", "delayed")]
        if at_risk:
            base += 0.10

        # Signal confidence adjustment
        confidence = data.signal_quality.get("confidence_score", 0.5)
        if confidence < 0.50:
            base *= 0.90  # dampen severity on low confidence

        return min(max(round(base, 4), 0.0), 1.0)

    # ---- Internal helpers ----

    def _count_blockers(self, data: AgentInput) -> int:
        return int(data.canonical_state.get("health", {}).get("open_blockers", 0) or 0)

    def _collect_evidence(
        self,
        classification: str,
        root_cause: str,
        severity: float,
        data: AgentInput,
    ) -> List[str]:
        ev = [
            f"Blocker classification: {classification}",
            f"Root cause pattern: {root_cause}",
            f"Estimated severity: {severity:.3f}",
        ]
        blockers = self._count_blockers(data)
        if blockers:
            ev.append(f"Open blockers in canonical state: {blockers}")
        return ev

    def _collect_decision_factors(self, severity: float, sparsity_alert: Optional[str]) -> List[str]:
        factors = [f"Severity: {severity:.3f}"]
        if severity > _HIGH_SEVERITY_THRESHOLD:
            factors.append(f"Severity {severity:.3f} > {_HIGH_SEVERITY_THRESHOLD} → decision_preparation + approval_required")
        else:
            factors.append(f"Severity {severity:.3f} ≤ {_HIGH_SEVERITY_THRESHOLD} → observation tier")
        if sparsity_alert:
            factors.append(f"Sparsity alert active — severity capped at {_SPARSITY_SEVERITY_CAP}")
        return factors

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        sparsity_alert: Optional[str],
        data: AgentInput,
    ) -> List[str]:
        notes: List[str] = []
        if not data.historical_cases:
            notes.append("Root cause inferred from classification heuristics — no historical cases available for pattern matching.")
        if confidence < 0.60:
            notes.append(f"Low confidence ({confidence:.2f}) — severity estimate may be inaccurate.")
        if is_decayed:
            notes.append("Signal data has decayed — blocker state may not reflect current reality.")
        if sparsity_alert:
            notes.append(f"Sparsity alert: severity capped at {_SPARSITY_SEVERITY_CAP}. {sparsity_alert}")
        if not notes:
            notes.append(
                f"Blocker classification based on event type and payload analysis for project {data.project_id!r}. "
                "No significant evidence gaps identified."
            )
        return notes

    def _build_recommendation(self, severity: float, classification: str, root_cause: str) -> Optional[str]:
        if severity > _HIGH_SEVERITY_THRESHOLD:
            return (
                f"PM decision required: severity {severity:.2f} exceeds escalation threshold. "
                f"Classification: {classification} / Root cause: {root_cause}. "
                "Confirm blocker status and approve mitigation path."
            )
        if severity > 0.40:
            return f"Monitor closely: {classification} blocker with root cause {root_cause!r}. Update status within 24 hours."
        return None
