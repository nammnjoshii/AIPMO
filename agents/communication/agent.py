"""Communication Agent.

Decision type: EXECUTION (always).
Policy action: ALLOW (always).
Skills: executive_summary_generation, stakeholder_personalization, decision_preparation_brief.

Hard rules:
- decision_type = EXECUTION, policy_action = ALLOW always
- Confidence disclosure when any input confidence < 0.65
- No banned phrases in brief body
- ≤5 bullet points for executive audience
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction
from agents.communication.prompts import BANNED_PHRASES

logger = logging.getLogger(__name__)

_CONFIDENCE_DISCLOSURE_THRESHOLD = 0.65


class CommunicationAgent(BaseAgent):
    """Synthesizes agent outputs into stakeholder-ready decision preparation briefs.

    Decision tier: execution — pre-approved, no human approval required.
    """

    def __init__(self) -> None:
        super().__init__("communication_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        start = time.monotonic()
        confidence = data.signal_quality.get("confidence_score", 0.5)
        is_decayed = data.signal_quality.get("is_decayed", False)

        audience = data.extra.get("audience", "executive")
        agent_outputs = data.extra.get("agent_outputs", [])

        brief = self._decision_preparation_brief(data, audience, agent_outputs, confidence)
        brief = self._enforce_no_banned_phrases(brief)

        confidence_disclosure = None
        if confidence < _CONFIDENCE_DISCLOSURE_THRESHOLD:
            confidence_disclosure = (
                f"Note: this brief is based on signal data with confidence {confidence:.2f} "
                f"(below the {_CONFIDENCE_DISCLOSURE_THRESHOLD} threshold). "
                "Treat as indicative — verify key facts before acting."
            )

        elapsed = time.monotonic() - start
        if elapsed > 25.0:
            logger.warning(
                "CommunicationAgent: brief generation took %.1fs (target < 30s)", elapsed
            )
        logger.debug("CommunicationAgent: brief generated in %.2fs", elapsed)

        evidence = self._collect_evidence(agent_outputs, data)
        decision_factors = [
            f"Audience: {audience}",
            f"Input confidence: {confidence:.2f}",
            f"Confidence disclosure required: {confidence < _CONFIDENCE_DISCLOSURE_THRESHOLD}",
        ]
        uncertainty_notes = self._build_uncertainty_notes(confidence, is_decayed, confidence_disclosure)

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.EXECUTION,
            confidence_score=min(max(confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=decision_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=PolicyAction.ALLOW,
            recommendation=None,
            proposed_state_updates={},
            proposed_graph_updates=[],
            extra={
                "brief_title": brief.get("title", ""),
                "body": brief.get("body", ""),
                "bullets": brief.get("bullets", []),
                "audience": audience,
                "confidence_disclosure": confidence_disclosure,
                "generation_latency_s": round(elapsed, 3),
            },
        )

    # ---- Skills ----

    def _executive_summary_generation(
        self, data: AgentInput, agent_outputs: List[Any]
    ) -> Dict[str, Any]:
        """Generate a concise executive summary (≤5 bullets)."""
        project_id = data.project_id
        risk_score = self._extract_risk_score(agent_outputs)
        open_blockers = data.canonical_state.get("health", {}).get("open_blockers", 0)
        schedule_health = data.canonical_state.get("health", {}).get("schedule_health", 0.7)

        status = "at risk" if schedule_health < 0.6 else "on track"
        bullets = [
            f"Project {project_id} delivery status: {status} (schedule health: {schedule_health:.0%})",
            f"Open blockers: {open_blockers}",
        ]
        if risk_score is not None:
            bullets.append(f"Risk score: {risk_score:.3f} — {'escalation required' if risk_score > 0.40 else 'monitoring'}")

        # Cap at 5 bullets for executive audience
        bullets = bullets[:5]

        return {
            "title": f"Executive Summary — {project_id}",
            "bullets": bullets,
            "body": "\n".join(f"• {b}" for b in bullets),
        }

    def _stakeholder_personalization(
        self, audience: str, content: Dict[str, Any], data: AgentInput
    ) -> Dict[str, Any]:
        """Adapt brief content to the specific audience."""
        if audience == "executive":
            return {
                "title": content.get("title", f"Executive Summary — {data.project_id}"),
                "body": content.get("body", ""),
                "bullets": content.get("bullets", [])[:5],
            }
        if audience == "program_director":
            risk_score = content.get("risk_score", "N/A")
            return {
                "title": f"Program Director Brief — {data.project_id}",
                "body": (
                    f"Situation: {content.get('body', '')}\n"
                    f"Risk Score: {risk_score}\n"
                    f"Recommended Path: {content.get('recommendation', 'Review agent outputs.')}"
                ),
                "bullets": content.get("bullets", []),
            }
        if audience == "project_manager":
            return {
                "title": f"PM Action Brief — {data.project_id}",
                "body": content.get("body", ""),
                "bullets": content.get("bullets", []),
            }
        # team_member or default
        return {
            "title": f"Team Update — {data.project_id}",
            "body": content.get("body", ""),
            "bullets": content.get("bullets", []),
        }

    def _decision_preparation_brief(
        self,
        data: AgentInput,
        audience: str,
        agent_outputs: List[Any],
        confidence: float,
    ) -> Dict[str, Any]:
        """Orchestrate brief generation through all three skills."""
        raw = self._executive_summary_generation(data, agent_outputs)
        risk_score = self._extract_risk_score(agent_outputs)
        if risk_score is not None:
            raw["risk_score"] = risk_score

        # Build recommendation from agent outputs
        recommendations = []
        for output in agent_outputs:
            if isinstance(output, dict):
                rec = output.get("recommendation")
            elif hasattr(output, "recommendation"):
                rec = output.recommendation
            else:
                rec = None
            if rec:
                recommendations.append(rec)
        if recommendations:
            raw["recommendation"] = " | ".join(recommendations[:2])

        personalized = self._stakeholder_personalization(audience, raw, data)
        return personalized

    # ---- Internal helpers ----

    def _enforce_no_banned_phrases(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        body = brief.get("body", "")
        for phrase in BANNED_PHRASES:
            if phrase.lower() in body.lower():
                body = body.replace(phrase, "[omitted]")
                logger.warning("CommunicationAgent: banned phrase '%s' removed from brief", phrase)
        brief["body"] = body
        return brief

    def _extract_risk_score(self, agent_outputs: List[Any]) -> Optional[float]:
        for output in agent_outputs:
            if isinstance(output, dict):
                rs = output.get("risk_score") or output.get("extra", {}).get("risk_score")
            elif hasattr(output, "extra"):
                rs = output.extra.get("risk_score")
            else:
                rs = None
            if rs is not None:
                return float(rs)
        return None

    def _collect_evidence(self, agent_outputs: List[Any], data: AgentInput) -> List[str]:
        ev = [f"Brief generated for project {data.project_id!r} at event type {data.event_type!r}"]
        if agent_outputs:
            ev.append(f"Synthesized outputs from {len(agent_outputs)} upstream agent(s)")
        return ev

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        confidence_disclosure: Optional[str],
    ) -> List[str]:
        notes: List[str] = []
        if confidence_disclosure:
            notes.append(confidence_disclosure)
        if is_decayed:
            notes.append("Brief based on decayed signal data — facts may not reflect latest project state.")
        if not notes:
            notes.append(
                f"Brief based on signal data with confidence {confidence:.2f}. "
                "Verify key facts with project team before distributing."
            )
        return notes
