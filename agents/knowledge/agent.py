"""Knowledge Agent.

Decision type: OBSERVATION.
Skills: lesson_extraction, cross_project_lesson_retrieval, mitigation_effectiveness_lookup.

Hard rules:
- Only extract from CONFIRMED outcomes, not in-progress situations
- Project isolation enforced in retrieval
- uncertainty_notes must state cases_available and close_matches counts
- sample_size < 5 heuristics flagged as low-confidence
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, BaseAgent, DecisionType, PolicyAction

logger = logging.getLogger(__name__)

_LOW_SAMPLE_THRESHOLD = 5


class KnowledgeAgent(BaseAgent):
    """Extracts and retrieves lessons learned across project history.

    Decision tier: observation - no approval required.
    """

    def __init__(self) -> None:
        super().__init__("knowledge_agent")

    def run(self, data: AgentInput) -> AgentOutput:
        confidence = data.signal_quality.get("confidence_score", 0.5)
        is_decayed = data.signal_quality.get("is_decayed", False)
        knowledge_type = data.extra.get("knowledge_type", "lesson_extraction")

        cases_available = len(data.historical_cases) if data.historical_cases else 0

        if knowledge_type == "lesson_extraction":
            lessons, close_matches = self._lesson_extraction(data)
        elif knowledge_type == "cross_project_lesson_retrieval":
            lessons, close_matches = self._cross_project_lesson_retrieval(data)
        else:
            lessons, close_matches = self._mitigation_effectiveness_lookup(data)

        evidence = self._collect_evidence(lessons, cases_available, close_matches, data)
        decision_factors = self._collect_decision_factors(cases_available, close_matches)
        uncertainty_notes = self._build_uncertainty_notes(
            confidence, is_decayed, cases_available, close_matches, data
        )
        recommendation = self._build_recommendation(lessons, cases_available)

        adjusted_confidence = confidence
        if cases_available == 0:
            adjusted_confidence = min(confidence, 0.40)
        elif cases_available < _LOW_SAMPLE_THRESHOLD:
            adjusted_confidence = min(confidence, 0.60)

        return AgentOutput(
            agent_name=self.agent_name,
            decision_type=DecisionType.OBSERVATION,
            confidence_score=min(max(adjusted_confidence, 0.0), 1.0),
            evidence=evidence,
            decision_factors=decision_factors,
            uncertainty_notes=uncertainty_notes,
            policy_action=PolicyAction.ALLOW,
            recommendation=recommendation,
            proposed_state_updates={},
            proposed_graph_updates=[],
            extra={
                "lessons_extracted": lessons,
                "cases_available": cases_available,
                "close_matches": close_matches,
            },
        )

    # ---- Skills ----

    def _lesson_extraction(self, data: AgentInput) -> tuple[List[Dict[str, Any]], int]:
        """Extract lessons from confirmed/resolved historical cases only."""
        cases = data.historical_cases or []
        confirmed_cases = [
            c for c in cases
            if isinstance(c, dict) and c.get("outcome") in ("resolved", "completed", "confirmed")
        ]
        if not confirmed_cases:
            return [], 0

        lessons = []
        for case in confirmed_cases[:3]:
            resolution = case.get("resolution", "")
            if resolution:
                lessons.append({
                    "case_id": case.get("case_id", "unknown"),
                    "lesson": f"Resolution pattern: {resolution}",
                    "outcome": case.get("outcome"),
                    "similarity_score": case.get("similarity_score", 0.0),
                })

        return lessons, len(confirmed_cases)

    def _cross_project_lesson_retrieval(self, data: AgentInput) -> tuple[List[Dict[str, Any]], int]:
        """Retrieve lessons with strict project isolation."""
        cases = data.historical_cases or []
        same_project = [
            c for c in cases
            if isinstance(c, dict) and c.get("project_id", data.project_id) == data.project_id
        ]
        lessons = [
            {
                "case_id": c.get("case_id"),
                "lesson": c.get("resolution", ""),
                "outcome": c.get("outcome"),
                "similarity_score": c.get("similarity_score", 0.0),
            }
            for c in same_project[:3]
            if c.get("resolution")
        ]
        return lessons, len(same_project)

    def _mitigation_effectiveness_lookup(self, data: AgentInput) -> tuple[List[Dict[str, Any]], int]:
        """Look up effectiveness of past mitigations for current risk type."""
        cases = data.historical_cases or []
        effective = [
            c for c in cases
            if isinstance(c, dict) and c.get("outcome") == "resolved" and c.get("resolution")
        ]
        results = [
            {
                "mitigation": c.get("resolution"),
                "effectiveness": c.get("similarity_score", 0.5),
                "case_id": c.get("case_id"),
            }
            for c in effective[:3]
        ]
        return results, len(effective)

    # ---- Internal helpers ----

    def _collect_evidence(
        self,
        lessons: List[Dict],
        cases_available: int,
        close_matches: int,
        data: AgentInput,
    ) -> List[str]:
        return [
            f"Historical cases available: {cases_available}",
            f"Close matches found: {close_matches}",
            f"Lessons extracted: {len(lessons)}" if lessons else f"No lessons extracted for project {data.project_id!r}",
        ]

    def _collect_decision_factors(self, cases_available: int, close_matches: int) -> List[str]:
        factors = [
            f"cases_available: {cases_available}",
            f"close_matches: {close_matches}",
        ]
        if cases_available == 0:
            factors.append("No history available — estimates are assumption-based")
        elif cases_available < _LOW_SAMPLE_THRESHOLD:
            factors.append(f"Sample size {cases_available} below {_LOW_SAMPLE_THRESHOLD} — low-confidence heuristics")
        return factors

    def _build_uncertainty_notes(
        self,
        confidence: float,
        is_decayed: bool,
        cases_available: int,
        close_matches: int,
        data: AgentInput,
    ) -> List[str]:
        notes = [
            f"cases_available={cases_available}",
            f"close_matches={close_matches}",
        ]
        if cases_available == 0:
            notes.append("No historical cases — all patterns are heuristic assumptions.")
        elif cases_available < _LOW_SAMPLE_THRESHOLD:
            notes.append(
                f"Sample size {cases_available} is below {_LOW_SAMPLE_THRESHOLD} — "
                "heuristic is low-confidence, treat as indicative only."
            )
        if is_decayed:
            notes.append("Source signal is decayed — lesson relevance may be reduced.")
        if confidence < 0.50:
            notes.append(f"Low input confidence ({confidence:.2f}) — retrieval quality uncertain.")
        return notes

    def _build_recommendation(self, lessons: List[Dict], cases_available: int) -> Optional[str]:
        if not lessons:
            if cases_available == 0:
                return "Build historical case base before relying on knowledge retrieval."
            return "No confirmed lessons available for this event pattern yet."
        top = lessons[0]
        return f"Apply lesson from case {top.get('case_id', 'unknown')!r}: {top.get('lesson', '')}"
