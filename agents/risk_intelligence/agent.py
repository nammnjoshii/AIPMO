"""Risk Intelligence Agent.

Decision type: DECISION_PREPARATION.
Skills: risk_scoring, risk_propagation_analysis, mitigation_recommendation.

Hard rules:
- risk_score = probability × impact (never rounded)
- sparsity_alert → cap risk_score at 0.50
- > 0.40 → escalate, 0.20–0.40 → approval_required, < 0.20 → allow_with_audit
- RISK graph node proposed only when risk_score ≥ 0.20
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

_ESCALATE_THRESHOLD = 0.40
_APPROVAL_THRESHOLD = 0.20
_SPARSITY_CAP = 0.50


class RiskIntelligenceAgent(BaseAgent):
    """Scores delivery risks and routes to appropriate approval level.

    Decision tier: decision_preparation — human decides on every output.
    """

    def __init__(self) -> None:
        super().__init__("risk_intelligence_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        confidence = data.signal_quality.get("confidence_score", 0.5)
        sparsity_alert = data.signal_quality.get("sparsity_alert")
        is_decayed = data.signal_quality.get("is_decayed", False)

        probability, impact = self._risk_scoring(data)
        # Exact multiplication — never round
        risk_score = probability * impact

        if sparsity_alert:
            risk_score = min(risk_score, _SPARSITY_CAP)

        propagation = self._risk_propagation_analysis(data, risk_score)
        mitigations = self._mitigation_recommendation(data, risk_score)

        policy_action = self._determine_policy_action(risk_score)
        evidence = self._collect_evidence(probability, impact, risk_score, propagation, data)
        decision_factors = self._collect_decision_factors(risk_score, sparsity_alert)
        uncertainty_notes = self._build_uncertainty_notes(confidence, is_decayed, sparsity_alert, data, probability, impact)
        recommendation = self._build_recommendation(risk_score, propagation, mitigations)

        proposed_graph: List[Dict[str, Any]] = []
        if risk_score >= _APPROVAL_THRESHOLD:
            proposed_graph = [
                {
                    "node_type": "Risk",
                    "node_id": f"risk_{data.project_id}",
                    "action": "upsert",
                    "risk_score": risk_score,
                    "probability": probability,
                    "impact": impact,
                }
            ]

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
            proposed_graph_updates=proposed_graph,
            extra={"risk_score": risk_score, "probability": probability, "impact": impact},
        )

    # ---- Skills ----

    def _risk_scoring(self, data: AgentInput) -> tuple[float, float]:
        """Compute probability and impact from canonical state and event context."""
        health = data.canonical_state.get("health", {}) or {}
        milestones = data.canonical_state.get("milestones", []) or []

        # Probability: driven by blocker count and milestone at-risk ratio
        open_blockers = float(health.get("open_blockers", 0) or 0)
        total_milestones = max(len(milestones), 1)
        at_risk = sum(1 for m in milestones if m.get("status") in ("at_risk", "delayed"))

        probability = min(0.20 + open_blockers * 0.08 + (at_risk / total_milestones) * 0.30, 1.0)

        # Impact: schedule health inversion
        schedule_health = float(health.get("schedule_health", 0.7) or 0.7)
        impact = min(max(1.0 - schedule_health, 0.0), 1.0)

        # Confidence adjustment — dampen on low confidence signal
        confidence = data.signal_quality.get("confidence_score", 0.5)
        if confidence < 0.50:
            probability *= 0.85
            impact *= 0.85

        return round(probability, 6), round(impact, 6)

    def _risk_propagation_analysis(self, data: AgentInput, risk_score: float) -> List[str]:
        """Identify milestones at risk of propagation."""
        if risk_score < _APPROVAL_THRESHOLD:
            return []
        milestones = data.canonical_state.get("milestones", []) or []
        affected = []
        for m in milestones:
            if m.get("status") in ("at_risk", "delayed", "on_track") and risk_score > 0.30:
                name = m.get("name", m.get("milestone_id", "unknown"))
                affected.append(f"{name} at risk of schedule slip if blocker persists")
        return affected[:3]  # top 3

    def _mitigation_recommendation(self, data: AgentInput, risk_score: float) -> List[str]:
        """Generate ≥2 mitigation options."""
        options = [
            "Engage backup vendor or internal resource to unblock critical path",
            "Descope lowest-priority feature to recover timeline slack",
        ]
        if risk_score > 0.50:
            options.append("Escalate to executive sponsor for resource allocation decision")
        if data.canonical_state.get("milestones"):
            options.append("Replan milestone dates with PM and stakeholders")
        return options

    # ---- Internal helpers ----

    def _determine_policy_action(self, risk_score: float) -> PolicyAction:
        if risk_score > _ESCALATE_THRESHOLD:
            return PolicyAction.ESCALATE
        if risk_score >= _APPROVAL_THRESHOLD:
            return PolicyAction.APPROVAL_REQUIRED
        return PolicyAction.ALLOW_WITH_AUDIT

    def _collect_evidence(
        self,
        probability: float,
        impact: float,
        risk_score: float,
        propagation: List[str],
        data: AgentInput,
    ) -> List[str]:
        ev = [
            f"Probability: {probability:.6f}",
            f"Impact: {impact:.6f}",
            f"risk_score = {probability:.6f} × {impact:.6f} = {risk_score:.6f}",
        ]
        if propagation:
            ev.extend(propagation)
        open_blockers = data.canonical_state.get("health", {}).get("open_blockers", 0)
        if open_blockers:
            ev.append(f"Open blockers: {open_blockers}")
        return ev

    def _collect_decision_factors(self, risk_score: float, sparsity_alert: Optional[str]) -> List[str]:
        factors = [f"risk_score: {risk_score:.6f}"]
        if risk_score > _ESCALATE_THRESHOLD:
            factors.append(f"risk_score {risk_score:.6f} > {_ESCALATE_THRESHOLD} → escalate")
        elif risk_score >= _APPROVAL_THRESHOLD:
            factors.append(f"risk_score {risk_score:.6f} in [{_APPROVAL_THRESHOLD}, {_ESCALATE_THRESHOLD}] → approval_required")
        else:
            factors.append(f"risk_score {risk_score:.6f} < {_APPROVAL_THRESHOLD} → allow_with_audit")
        if sparsity_alert:
            factors.append(f"Sparsity cap applied: risk_score capped at {_SPARSITY_CAP}")
        return factors

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        sparsity_alert: Optional[str],
        data: AgentInput,
        probability: float,
        impact: float,
    ) -> List[str]:
        notes = [
            f"Probability ({probability:.6f}) derived from blocker count and milestone at-risk ratio — heuristic estimate.",
            f"Impact ({impact:.6f}) derived from schedule_health inversion — may not reflect full business impact.",
        ]
        if not data.historical_cases:
            notes.append("No historical analogues — probability estimate has no calibration base.")
        if confidence < 0.60:
            notes.append(f"Low confidence ({confidence:.2f}) — probability and impact dampened by 15%.")
        if is_decayed:
            notes.append("Signal data decayed — risk score may understate current exposure.")
        if sparsity_alert:
            notes.append(f"Sparsity alert active: risk_score capped at {_SPARSITY_CAP}.")
        return notes

    def _build_recommendation(
        self,
        risk_score: float,
        propagation: List[str],
        mitigations: List[str],
    ) -> str:
        if risk_score > _ESCALATE_THRESHOLD:
            prop_str = "; ".join(propagation[:2]) if propagation else "multiple milestones"
            return (
                f"ESCALATE: risk_score={risk_score:.6f} exceeds threshold. "
                f"Propagation: {prop_str}. "
                f"Recommended mitigation: {mitigations[0] if mitigations else 'see options'}."
            )
        if risk_score >= _APPROVAL_THRESHOLD:
            return (
                f"Approval required: risk_score={risk_score:.6f}. "
                f"Review mitigation options: {'; '.join(mitigations[:2])}."
            )
        return f"Monitor: risk_score={risk_score:.6f} below approval threshold. Review at next status check."
