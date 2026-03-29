"""Case matcher — retrieves top-k historically similar delivery cases.

Uses pgvector similarity search when a vector store is available.
Returns empty list gracefully when no historical cases exist or the store
is unavailable. Each returned case includes similarity_score and outcome.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent
from state.schemas import CanonicalProjectState

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 3


class CaseMatcher:
    """Matches current delivery context against historical cases.

    The vector_store dependency is optional — if None, returns empty list.
    This allows the assembler to work without a live pgvector instance.
    """

    def __init__(self, vector_store: Optional[Any] = None) -> None:
        self._store = vector_store

    def match(
        self,
        event: DeliveryEvent,
        state: CanonicalProjectState,
        top_k: int = _DEFAULT_TOP_K,
    ) -> List[Dict[str, Any]]:
        """Return top-k most similar historical cases for the current context.

        Args:
            event: The triggering delivery event.
            state: Current canonical project state.
            top_k: Number of cases to return (default 3).

        Returns:
            List of case dicts, each containing:
              case_id, similarity_score, event_type, resolution, outcome.
            Empty list if no cases exist or store is unavailable.
        """
        if self._store is None:
            logger.debug(
                "CaseMatcher: no vector store configured — returning empty case list "
                "for project=%s event_type=%s",
                state.project_id,
                event.event_type,
            )
            return []

        try:
            return self._match(event, state, top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CaseMatcher: case retrieval failed for project=%s event_type=%s: %s — "
                "returning empty list.",
                state.project_id,
                event.event_type,
                exc,
            )
            return []

    # ---- Internal ----

    def _match(
        self,
        event: DeliveryEvent,
        state: CanonicalProjectState,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        query_vector = self._build_query_vector(event, state)
        raw_cases = self._store.similarity_search(
            vector=query_vector,
            top_k=top_k,
            filter={"event_type": str(event.event_type)},
        )

        cases = []
        for raw in raw_cases:
            cases.append({
                "case_id": raw.get("case_id", "unknown"),
                "similarity_score": float(raw.get("score", 0.0)),
                "event_type": raw.get("event_type", str(event.event_type)),
                "resolution": raw.get("resolution", ""),
                "outcome": raw.get("outcome", ""),
            })

        logger.debug(
            "CaseMatcher: matched %d cases for project=%s event_type=%s",
            len(cases),
            state.project_id,
            event.event_type,
        )
        return cases

    @staticmethod
    def _build_query_vector(
        event: DeliveryEvent,
        state: CanonicalProjectState,
    ) -> List[float]:
        """Build a query embedding from event + state context.

        In production this calls the embedding model. Here we return a
        placeholder — the actual embedding call lives in the vector store adapter.
        """
        # The vector store adapter handles actual embedding generation.
        # We return the raw context dict; the adapter embeds it.
        return {  # type: ignore[return-value]
            "event_type": str(event.event_type),
            "project_id": event.project_id,
            "source": event.source,
            "health_overall": state.health.overall_health,
            "open_blockers": state.health.open_blockers,
            "milestone_count": len(state.milestones),
        }
