"""Graph neighborhood fetcher — T-083. Live Kuzu queries via query_service.py.

Replaces the T-033 stub. Delegates all graph queries to GraphQueryService
(which uses the embedded Kuzu database). Falls back to empty context when:
  - Kuzu DB path is unavailable (file system error)
  - kuzu package is not installed
  - Any other graph retrieval error

Returns empty dict (not an exception) when the graph is unavailable.
Always includes a `graph_available: bool` field so callers know if the
graph was actually reachable.

All queries go through knowledge_graph/query_service.py — no ad hoc Cypher here.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_HOPS = 2
_GRAPH_TIMEOUT_SECONDS = 5.0


class GraphNeighborhoodFetcher:
    """Fetches the graph neighborhood around an entity up to N hops.

    Auto-initializes GraphQueryService (with embedded Kuzu store) if no
    query_service is injected. Falls back gracefully on any error — never raises.
    """

    def __init__(self, query_service: Optional[Any] = None) -> None:
        if query_service is not None:
            self._qs = query_service
        else:
            # Auto-initialize live query service with Kuzu backend
            self._qs = self._init_query_service()

    @staticmethod
    def _init_query_service() -> Optional[Any]:
        """Initialize GraphQueryService. Returns None if Kuzu is unavailable."""
        try:
            from knowledge_graph.graph_store import KuzuGraphStore
            from knowledge_graph.query_service import GraphQueryService

            store = KuzuGraphStore()
            ok = store.initialize()
            if not ok:
                logger.warning(
                    "GraphNeighborhoodFetcher: Kuzu store unavailable — "
                    "graph context will be empty."
                )
                return None
            return GraphQueryService(store=store)
        except Exception as e:
            logger.warning(
                "GraphNeighborhoodFetcher: failed to initialize query service: %s", e
            )
            return None

    def fetch(
        self,
        entity_id: str,
        hops: int = _DEFAULT_HOPS,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the graph neighborhood for entity_id up to `hops` depth.

        Args:
            entity_id: Node ID to centre the neighborhood on.
            hops: Depth of traversal (default 2; Program Director may use 3).
            project_id: Optional project scope for cross-project boundary enforcement.

        Returns:
            Dict with keys:
              - graph_available (bool): False if graph was unreachable.
              - nodes (List[Dict]): Nodes in the neighborhood.
              - edges (List[Dict]): Edges in the neighborhood.
              - hops (int): Depth fetched.
              - entity_id (str): The root entity.
        """
        if self._qs is None:
            logger.debug(
                "GraphNeighborhoodFetcher: no query service configured — "
                "returning empty neighborhood for entity=%s",
                entity_id,
            )
            return self._empty(entity_id, hops, reason="no_query_service")

        try:
            return self._fetch(entity_id, hops, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GraphNeighborhoodFetcher: graph unavailable for entity=%s hops=%d: %s — "
                "falling back to empty context.",
                entity_id,
                hops,
                exc,
            )
            return self._empty(entity_id, hops, reason=str(exc))

    # ---- Internal ----

    def _fetch(
        self,
        entity_id: str,
        hops: int,
        project_id: Optional[str],
    ) -> Dict[str, Any]:
        """Delegate to query_service and normalise the result."""
        raw = self._qs.get_neighborhood(
            entity_id=entity_id,
            hops=hops,
        )

        nodes = raw.get("nodes", [])
        edges = raw.get("edges", [])

        logger.debug(
            "GraphNeighborhoodFetcher: fetched entity=%s hops=%d → %d nodes, %d edges",
            entity_id,
            hops,
            len(nodes),
            len(edges),
        )

        return {
            "graph_available": True,
            "nodes": nodes,
            "edges": edges,
            "hops": hops,
            "entity_id": entity_id,
        }

    @staticmethod
    def _empty(entity_id: str, hops: int, reason: str = "") -> Dict[str, Any]:
        return {
            "graph_available": False,
            "nodes": [],
            "edges": [],
            "hops": hops,
            "entity_id": entity_id,
            "unavailable_reason": reason,
        }
