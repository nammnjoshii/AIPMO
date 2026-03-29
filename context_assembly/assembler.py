"""Context Assembler — single entry point all agents must use to get their context.

Hard rules enforced here:
- Agents never receive the full CanonicalProjectState — always a scoped slice.
- No cross-project data leaks — project_id isolation is enforced.
- Assembly time is logged; > 5 seconds triggers a warning.
- Program Director gets 3-hop graph context; all others get 2-hop.

All agents call:
    from context_assembly.assembler import assemble_context
    input = assemble_context(event, state, qualified_signal, agent_name, policy_context)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput
from context_assembly.case_matcher import CaseMatcher
from context_assembly.graph_neighborhood import GraphNeighborhoodFetcher
from context_assembly.state_slicer import StateSlicer
from events.schemas.event_types import DeliveryEvent
from signal_quality.pipeline import QualifiedSignal
from state.schemas import CanonicalProjectState

logger = logging.getLogger(__name__)

_ASSEMBLY_WARN_SECONDS = 5.0
_PROGRAM_DIRECTOR_HOPS = 3
_DEFAULT_HOPS = 2


class ContextAssembler:
    """Assembles a scoped AgentInput from all available context sources.

    Instantiate once and reuse — all stateless, thread-safe.
    """

    def __init__(
        self,
        query_service: Optional[Any] = None,
        vector_store: Optional[Any] = None,
    ) -> None:
        self._slicer = StateSlicer()
        self._graph = GraphNeighborhoodFetcher(query_service=query_service)
        self._cases = CaseMatcher(vector_store=vector_store)

    def assemble(
        self,
        event: DeliveryEvent,
        state: CanonicalProjectState,
        qualified_signal: QualifiedSignal,
        agent_name: str,
        policy_context: Dict[str, Any],
    ) -> AgentInput:
        """Build a fully-scoped AgentInput for the given agent.

        Args:
            event: The triggering DeliveryEvent.
            state: Full canonical project state (will be sliced — never passed raw).
            qualified_signal: Output of the Signal Quality Pipeline.
            agent_name: The target agent's registered name.
            policy_context: Applicable policy rules for this agent+project.

        Returns:
            AgentInput — scoped, isolated, never containing cross-project data.

        Raises:
            Never. All sub-steps are fault-tolerant.
        """
        start = time.monotonic()

        try:
            result = self._assemble(event, state, qualified_signal, agent_name, policy_context)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "ContextAssembler.assemble: unexpected error for agent=%s project=%s: %s — "
                "returning minimal safe AgentInput.",
                agent_name,
                event.project_id,
                exc,
                exc_info=True,
            )
            result = self._minimal_input(event, state, agent_name, policy_context, qualified_signal)

        elapsed = time.monotonic() - start
        if elapsed > _ASSEMBLY_WARN_SECONDS:
            logger.warning(
                "ContextAssembler.assemble: assembly for agent=%s project=%s took %.2fs "
                "(threshold=%ss)",
                agent_name,
                event.project_id,
                elapsed,
                _ASSEMBLY_WARN_SECONDS,
            )
        else:
            logger.debug(
                "ContextAssembler.assemble: agent=%s project=%s assembled in %.3fs",
                agent_name,
                event.project_id,
                elapsed,
            )

        return result

    # ---- Internal ----

    def _assemble(
        self,
        event: DeliveryEvent,
        state: CanonicalProjectState,
        qualified_signal: QualifiedSignal,
        agent_name: str,
        policy_context: Dict[str, Any],
    ) -> AgentInput:
        # Step 1 — Enforce project isolation: state must match event's project
        if state.project_id != event.project_id:
            logger.error(
                "ContextAssembler: project_id mismatch — event.project_id=%s "
                "state.project_id=%s. Refusing assembly to prevent cross-project leak.",
                event.project_id,
                state.project_id,
            )
            raise ValueError(
                f"Cross-project data leak prevented: event project_id={event.project_id} "
                f"does not match state project_id={state.project_id}"
            )

        # Step 2 — Slice canonical state to relevant fields only
        state_slice = self._slicer.slice(state, str(event.event_type))

        # Step 3 — Fetch graph neighborhood (3 hops for program_director)
        hops = _PROGRAM_DIRECTOR_HOPS if "program_director" in agent_name else _DEFAULT_HOPS
        graph_context = self._graph.fetch(
            entity_id=event.project_id,
            hops=hops,
            project_id=event.project_id,
        )

        # Step 4 — Match top-3 historical cases
        historical_cases = self._cases.match(event, state)

        # Step 5 — Build signal quality metadata
        signal_quality = self._signal_quality_dict(qualified_signal)

        return AgentInput(
            project_id=event.project_id,
            event_type=str(event.event_type),
            canonical_state=state_slice,
            graph_context=graph_context,
            historical_cases=historical_cases,
            policy_context=policy_context,
            signal_quality=signal_quality,
            tenant_id=event.tenant_id,
        )

    @staticmethod
    def _signal_quality_dict(qs: QualifiedSignal) -> Dict[str, Any]:
        """Flatten QualifiedSignal into a plain dict for AgentInput."""
        return {
            "confidence_score": qs.confidence_score,
            "is_decayed": qs.is_decayed,
            "is_low_signal": qs.is_low_signal,
            "reliability_tier": qs.reliability_profile.reliability_score,
            "gap_alert_count": len(qs.gap_alerts),
            "sparsity_alert": qs.sparsity_alert,
            "source": qs.event.source,
        }

    @staticmethod
    def _minimal_input(
        event: DeliveryEvent,
        state: CanonicalProjectState,
        agent_name: str,
        policy_context: Dict[str, Any],
        qualified_signal: QualifiedSignal,
    ) -> AgentInput:
        """Return a minimal safe AgentInput when full assembly fails."""
        return AgentInput(
            project_id=event.project_id,
            event_type=str(event.event_type),
            canonical_state={"project_id": event.project_id},
            graph_context={"graph_available": False, "nodes": [], "edges": []},
            historical_cases=[],
            policy_context=policy_context,
            signal_quality={
                "confidence_score": 0.0,
                "is_decayed": True,
                "is_low_signal": False,
                "reliability_tier": "low",
                "gap_alert_count": 0,
                "sparsity_alert": "Assembly failed — degraded context.",
                "source": event.source,
            },
            tenant_id=event.tenant_id,
        )


# ---- Module-level convenience function ----

def assemble_context(
    event: DeliveryEvent,
    state: CanonicalProjectState,
    qualified_signal: QualifiedSignal,
    agent_name: str,
    policy_context: Dict[str, Any],
    query_service: Optional[Any] = None,
    vector_store: Optional[Any] = None,
) -> AgentInput:
    """Convenience wrapper — creates a one-shot ContextAssembler and assembles."""
    assembler = ContextAssembler(
        query_service=query_service,
        vector_store=vector_store,
    )
    return assembler.assemble(event, state, qualified_signal, agent_name, policy_context)
