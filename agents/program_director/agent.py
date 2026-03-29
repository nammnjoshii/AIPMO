"""Program Director Agent.

Decision type: OBSERVATION (single event) or EXECUTION (merged/conflict resolved).
Skills: run() for single-event routing, merge() for parallel results, resolve() for conflicts.

Hard rules:
- policy_action = most restrictive across all inputs
- Irresolvable conflict → confidence_score ≤ 0.55
- Source agents named in evidence
- ESCALATE > APPROVAL_REQUIRED > ALLOW_WITH_AUDIT > ALLOW
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

# Policy action restrictiveness order (highest = most restrictive)
_POLICY_RANK: Dict[str, int] = {
    PolicyAction.ALLOW: 0,
    PolicyAction.ALLOW_WITH_AUDIT: 1,
    PolicyAction.APPROVAL_REQUIRED: 2,
    PolicyAction.DENY: 3,
    PolicyAction.ESCALATE: 4,
}

_CONFLICT_CONFIDENCE_CAP = 0.55
_HIGH_CONFIDENCE_THRESHOLD = 0.70


class ProgramDirectorAgent(BaseAgent):
    """Orchestrates multi-agent outputs and routes to policy.

    Decision tier: execution for merged/conflict outputs, observation for pass-through.
    """

    def __init__(self) -> None:
        super().__init__("program_director_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        """Handle a single event — act as the entry routing agent."""
        confidence = data.signal_quality.get("confidence_score", 0.5)
        is_decayed = data.signal_quality.get("is_decayed", False)

        evidence = [
            f"Program Director routing event {data.event_type!r} for project {data.project_id!r}",
            f"Signal confidence: {confidence:.2f}",
        ]
        if is_decayed:
            evidence.append("Signal data is decayed — routing with reduced confidence")

        uncertainty_notes = [
            f"Single-event routing observation — no agent conflict resolution required. "
            f"Downstream agents will provide detailed analysis."
        ]

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.OBSERVATION,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=[f"Routing {data.event_type!r} to agent coordination pipeline"],
            uncertainty_notes=uncertainty_notes,
            policy_action=PolicyAction.ALLOW,
            recommendation=f"Route event to coordination pipeline for project {data.project_id!r}.",
            proposed_state_updates={},
            proposed_graph_updates=[],
        )

    def merge(self, outputs: List[AgentOutput]) -> AgentOutput:
        """Merge parallel agent outputs into a single coordinated output.

        Applies the most restrictive policy action across all inputs.
        Detects and resolves conflicts per the 5-step process.
        """
        if not outputs:
            raise ValueError("ProgramDirectorAgent.merge() called with empty outputs list")

        conflict_detected, conflict_reason = self._detect_conflict(outputs)
        policy_action = self._most_restrictive_policy(outputs)

        merged_evidence = self._merge_evidence(outputs)
        merged_factors = self._merge_decision_factors(outputs)

        if conflict_detected:
            confidence = self._resolve_conflict_confidence(outputs)
        else:
            confidence = self._average_confidence(outputs)

        uncertainty_notes = self._build_merge_uncertainty_notes(
            outputs, conflict_detected, conflict_reason
        )

        recommendation = self._build_merge_recommendation(
            outputs, policy_action, conflict_detected
        )

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.EXECUTION,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=merged_evidence,
            decision_factors=merged_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=policy_action,
            recommendation=recommendation,
            proposed_state_updates={},
            proposed_graph_updates=[],
            extra={
                "conflict_detected": conflict_detected,
                "conflict_reason": conflict_reason,
                "source_agents": [o.agent_name for o in outputs],
                "policy_action_selected": policy_action.value,
            },
        )

    def resolve(self, outputs: List[AgentOutput]) -> AgentOutput:
        """Resolve a detected conflict between agent outputs.

        Applies the 5-step conflict resolution process.
        Irresolvable conflicts produce confidence ≤ 0.55.
        """
        if len(outputs) < 2:
            return self.merge(outputs)

        conflict_detected, conflict_reason = self._detect_conflict(outputs)
        if not conflict_detected:
            return self.merge(outputs)

        # Both agents have high confidence and opposing conclusions
        high_conf_agents = [o for o in outputs if o.confidence_score >= _HIGH_CONFIDENCE_THRESHOLD]
        irresolvable = len(high_conf_agents) >= 2

        policy_action = self._most_restrictive_policy(outputs)

        if irresolvable:
            confidence = _CONFLICT_CONFIDENCE_CAP * 0.95  # ensure ≤ 0.55
        else:
            # Prefer output with higher confidence
            best = max(outputs, key=lambda o: o.confidence_score)
            confidence = best.confidence_score * 0.90  # slight reduction for uncertainty

        evidence = self._merge_evidence(outputs)
        evidence.append(f"Conflict: {conflict_reason}")
        if irresolvable:
            evidence.append("IRRESOLVABLE: both agents high confidence, opposing conclusions — human decision required")

        uncertainty_notes = [
            f"Conflict detected: {conflict_reason}",
        ]
        if irresolvable:
            uncertainty_notes.append(
                "Irresolvable conflict — confidence capped at 0.55. "
                "Human decision required to break the deadlock."
            )
        uncertainty_notes.extend(self._build_merge_uncertainty_notes(outputs, True, conflict_reason))

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.EXECUTION,
            confidence_score=min(max(confidence, 0.0), _CONFLICT_CONFIDENCE_CAP if irresolvable else 1.0),
            evidence=evidence,
            decision_factors=[
                "Conflict arbitration applied",
                f"Most restrictive policy: {policy_action.value}",
                f"Irresolvable: {irresolvable}",
            ],
            uncertainty_notes=uncertainty_notes,
            policy_action=policy_action,
            recommendation=(
                "Human decision required — agents disagree on severity assessment. "
                f"Most restrictive action applied: {policy_action.value}."
                if irresolvable
                else f"Resolved: higher-confidence assessment prevailed. Policy: {policy_action.value}."
            ),
            proposed_state_updates={},
            proposed_graph_updates=[],
            extra={
                "conflict_detected": True,
                "irresolvable": irresolvable,
                "conflict_reason": conflict_reason,
                "source_agents": [o.agent_name for o in outputs],
                "policy_action_selected": policy_action.value,
            },
        )

    # ---- Internal helpers ----

    def _detect_conflict(self, outputs: List[AgentOutput]) -> tuple[bool, str]:
        """Return (conflict_detected, reason_string)."""
        if len(outputs) < 2:
            return False, ""

        policy_actions = [o.policy_action for o in outputs]
        unique_policies = set(policy_actions)

        # No conflict if all agree on policy direction
        # Allow + Allow_with_audit are compatible; Approval/Escalate vs Allow = conflict
        allow_group = {PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT}
        escalate_group = {PolicyAction.APPROVAL_REQUIRED, PolicyAction.ESCALATE}

        has_allow = any(a in allow_group for a in policy_actions)
        has_escalate = any(a in escalate_group for a in policy_actions)

        if has_allow and has_escalate:
            # Check if both are high confidence
            high_conf = [o for o in outputs if o.confidence_score >= _HIGH_CONFIDENCE_THRESHOLD]
            if len(high_conf) >= 2:
                agents = [o.agent_name for o in outputs]
                return (
                    True,
                    f"High-confidence disagreement: {agents[0]} says {outputs[0].policy_action.value} "
                    f"(conf={outputs[0].confidence_score:.2f}), "
                    f"{agents[1]} says {outputs[1].policy_action.value} "
                    f"(conf={outputs[1].confidence_score:.2f})",
                )

        return False, ""

    def _most_restrictive_policy(self, outputs: List[AgentOutput]) -> PolicyAction:
        """Return the most restrictive PolicyAction across all outputs."""
        return max(outputs, key=lambda o: _POLICY_RANK.get(o.policy_action, 0)).policy_action

    def _average_confidence(self, outputs: List[AgentOutput]) -> float:
        if not outputs:
            return 0.5
        avg = sum(o.confidence_score for o in outputs) / len(outputs)
        return round(min(avg, 0.90), 4)

    def _resolve_conflict_confidence(self, outputs: List[AgentOutput]) -> float:
        """Return confidence for conflict case — capped at _CONFLICT_CONFIDENCE_CAP if irresolvable."""
        high_conf = [o for o in outputs if o.confidence_score >= _HIGH_CONFIDENCE_THRESHOLD]
        if len(high_conf) >= 2:
            return _CONFLICT_CONFIDENCE_CAP - 0.03  # ensure ≤ 0.55
        return min(self._average_confidence(outputs), 0.70)

    def _merge_evidence(self, outputs: List[AgentOutput]) -> List[str]:
        merged = []
        for o in outputs:
            merged.append(f"[{o.agent_name}]: {'; '.join(o.evidence[:2])}")
        return merged

    def _merge_decision_factors(self, outputs: List[AgentOutput]) -> List[str]:
        factors = [f"Most restrictive policy action: {self._most_restrictive_policy(outputs).value}"]
        for o in outputs:
            factors.append(f"{o.agent_name}: {o.policy_action.value} (conf={o.confidence_score:.2f})")
        return factors

    def _build_merge_uncertainty_notes(
        self,
        outputs: List[AgentOutput],
        conflict_detected: bool,
        conflict_reason: str,
    ) -> List[str]:
        notes = []
        if conflict_detected:
            notes.append(f"Conflict resolved: {conflict_reason}")
        for o in outputs:
            if o.uncertainty_notes:
                notes.append(f"[{o.agent_name}] {o.uncertainty_notes[0]}")
        if not notes:
            notes.append(
                f"Merged {len(outputs)} agent outputs. "
                "No significant uncertainty gaps identified at coordination level."
            )
        return notes

    def _build_merge_recommendation(
        self,
        outputs: List[AgentOutput],
        policy_action: PolicyAction,
        conflict_detected: bool,
    ) -> str:
        recs = [o.recommendation for o in outputs if o.recommendation]
        if conflict_detected:
            return (
                f"Conflict arbitration complete. Applied most restrictive policy: {policy_action.value}. "
                + (f"Key recommendation: {recs[0]}" if recs else "Review individual agent outputs.")
            )
        return (
            f"Route to {policy_action.value} workflow. "
            + (f"Primary recommendation: {recs[0]}" if recs else "No specific recommendation from agents.")
        )
