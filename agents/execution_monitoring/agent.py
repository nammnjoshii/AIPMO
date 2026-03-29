"""Execution Monitoring Agent.

Decision type: OBSERVATION only.
Skills: schedule_variance_detection, throughput_analysis, bottleneck_detection.

Hard rules:
- Never returns DECISION_PREPARATION or ESCALATE policy actions.
- Health score capped at 0.75 when signal confidence < 0.5.
- Always populates uncertainty_notes with specific data gaps.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

_HEALTH_CAP_LOW_CONFIDENCE = 0.75
_LOW_CONFIDENCE_THRESHOLD = 0.50


class ExecutionMonitoringAgent(BaseAgent):
    """Observes delivery execution data and produces health score updates.

    Decision tier: observation — no human approval required for any output.
    Policy actions allowed: allow, allow_with_audit.
    """

    def __init__(self) -> None:
        super().__init__("execution_monitoring_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        """Execute schedule variance, throughput, and bottleneck analysis.

        Args:
            data: Scoped AgentInput from context_assembly/assembler.py.

        Returns:
            AgentOutput with decision_type=OBSERVATION and policy_action in {allow, allow_with_audit}.
        """
        confidence = data.signal_quality.get("confidence_score", 0.5)
        is_decayed = data.signal_quality.get("is_decayed", False)
        sparsity_alert = data.signal_quality.get("sparsity_alert")

        variance = self._schedule_variance_detection(data)
        throughput = self._throughput_analysis(data)
        bottleneck = self._bottleneck_detection(data)

        health_score = self._compute_health_score(variance, throughput, bottleneck)
        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            health_score = min(health_score, _HEALTH_CAP_LOW_CONFIDENCE)

        evidence = self._collect_evidence(variance, throughput, bottleneck, data)
        decision_factors = self._collect_decision_factors(variance, throughput, bottleneck, confidence)
        uncertainty_notes = self._build_uncertainty_notes(confidence, is_decayed, sparsity_alert, data)

        # State changes → allow_with_audit; observation-only → allow
        has_state_update = bool(variance or throughput or bottleneck)
        policy_action = PolicyAction.ALLOW_WITH_AUDIT if has_state_update else PolicyAction.ALLOW

        proposed_state: Dict[str, Any] = {}
        if has_state_update:
            open_blockers = bottleneck.get("blocker_count", 0) if bottleneck else 0
            throughput_score = throughput.get("throughput_score", 0.7) if throughput else 0.7
            proposed_state = {
                "health": {
                    "schedule_health": health_score,
                    "throughput_score": min(max(throughput_score, 0.0), 1.0),
                    "open_blockers": open_blockers,
                }
            }

        recommendation = self._build_recommendation(variance, bottleneck)

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.OBSERVATION,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=decision_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=policy_action,
            recommendation=recommendation,
            proposed_state_updates=proposed_state,
            proposed_graph_updates=[],
        )

    # ---- Skills ----

    def _schedule_variance_detection(self, data: AgentInput) -> Dict[str, Any]:
        """Detect schedule variance from canonical state milestones."""
        state = data.canonical_state
        milestones = state.get("milestones", [])
        if not milestones:
            return {}

        at_risk = [m for m in milestones if m.get("status") in ("at_risk", "delayed")]
        on_track = [m for m in milestones if m.get("status") == "on_track"]
        total = len(milestones)

        if total == 0:
            return {}

        variance_pct = len(at_risk) / total if total > 0 else 0.0
        return {
            "variance_pct": variance_pct,
            "at_risk_count": len(at_risk),
            "on_track_count": len(on_track),
            "total_milestones": total,
            "affected_milestones": [m.get("name", "unknown") for m in at_risk],
        }

    def _throughput_analysis(self, data: AgentInput) -> Dict[str, Any]:
        """Analyse task throughput from event payload and canonical state."""
        payload = data.extra.get("payload", {})
        health = data.canonical_state.get("health", {})

        # Use health metrics as proxy when direct throughput data unavailable
        schedule_health = health.get("schedule_health", 0.7) if isinstance(health, dict) else 0.7
        throughput_score = schedule_health  # simplified proxy for MVP

        return {
            "throughput_score": min(max(throughput_score, 0.0), 1.0),
            "trend": "stable",
            "data_source": "canonical_state_health_proxy",
            "payload_keys_available": list(payload.keys()),
        }

    def _bottleneck_detection(self, data: AgentInput) -> Dict[str, Any]:
        """Identify blockers from canonical state health metrics."""
        health = data.canonical_state.get("health", {})
        if not isinstance(health, dict):
            return {}

        open_blockers = health.get("open_blockers", 0)
        if open_blockers is None:
            open_blockers = 0

        return {
            "blocker_count": open_blockers,
            "primary_constraint": "open_blockers" if open_blockers > 0 else "none",
            "estimated_impact_days": open_blockers * 1.5,  # heuristic: 1.5 days per blocker
        }

    # ---- Internal helpers ----

    def _compute_health_score(
        self,
        variance: Dict[str, Any],
        throughput: Dict[str, Any],
        bottleneck: Dict[str, Any],
    ) -> float:
        base = 1.0
        if variance:
            base -= variance.get("variance_pct", 0.0) * 0.5
        if bottleneck:
            blocker_penalty = min(bottleneck.get("blocker_count", 0) * 0.05, 0.30)
            base -= blocker_penalty
        if throughput:
            t_score = throughput.get("throughput_score", 1.0)
            base = base * 0.6 + t_score * 0.4
        return min(max(round(base, 4), 0.0), 1.0)

    def _collect_evidence(
        self,
        variance: Dict[str, Any],
        throughput: Dict[str, Any],
        bottleneck: Dict[str, Any],
        data: AgentInput,
    ) -> List[str]:
        ev: List[str] = []
        if variance:
            ev.append(
                f"Schedule variance: {variance.get('variance_pct', 0):.0%} of milestones at risk "
                f"({variance.get('at_risk_count', 0)}/{variance.get('total_milestones', 0)})"
            )
        if throughput:
            ev.append(
                f"Throughput score: {throughput.get('throughput_score', 0):.2f} "
                f"(trend: {throughput.get('trend', 'unknown')})"
            )
        if bottleneck:
            bc = bottleneck.get("blocker_count", 0)
            ev.append(
                f"Open blockers: {bc} — estimated delay: "
                f"{bottleneck.get('estimated_impact_days', 0):.1f} days"
            )
        if not ev:
            ev.append(f"Observed event type {data.event_type!r} for project {data.project_id!r} — no anomalies detected.")
        return ev

    def _collect_decision_factors(
        self,
        variance: Dict[str, Any],
        throughput: Dict[str, Any],
        bottleneck: Dict[str, Any],
        confidence: float,
    ) -> List[str]:
        factors: List[str] = []
        if variance:
            factors.append(f"Milestone variance pct: {variance.get('variance_pct', 0):.2%}")
        if throughput:
            factors.append(f"Throughput score: {throughput.get('throughput_score', 0):.2f}")
        if bottleneck:
            factors.append(f"Open blocker count: {bottleneck.get('blocker_count', 0)}")
        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            factors.append(f"Health score capped at {_HEALTH_CAP_LOW_CONFIDENCE} due to low signal confidence ({confidence:.2f})")
        if not factors:
            factors.append("No significant variance factors detected.")
        return factors

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        sparsity_alert: Optional[str],
        data: AgentInput,
    ) -> List[str]:
        notes: List[str] = []
        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            notes.append(
                f"Low signal confidence ({confidence:.2f}): health score capped at "
                f"{_HEALTH_CAP_LOW_CONFIDENCE}. Results are indicative only."
            )
        if is_decayed:
            notes.append("Signal data has decayed past the freshness window — throughput scores may lag reality.")
        if sparsity_alert:
            notes.append(f"Sparsity alert active: {sparsity_alert}")
        if not data.canonical_state.get("milestones"):
            notes.append("No milestone data in canonical state — schedule variance analysis skipped.")
        if not notes:
            notes.append(
                f"Analysis based on available canonical state for project {data.project_id!r}. "
                "No significant data gaps identified at time of observation."
            )
        return notes

    def _build_recommendation(
        self,
        variance: Dict[str, Any],
        bottleneck: Dict[str, Any],
    ) -> Optional[str]:
        if bottleneck and bottleneck.get("blocker_count", 0) > 2:
            return (
                f"PM attention: {bottleneck['blocker_count']} open blockers detected. "
                "Review dependency queue and unblock top priority items."
            )
        if variance and variance.get("variance_pct", 0) > 0.3:
            affected = ", ".join(variance.get("affected_milestones", [])[:3])
            return f"Review milestone plan — {variance['at_risk_count']} milestone(s) at risk: {affected}."
        return None
