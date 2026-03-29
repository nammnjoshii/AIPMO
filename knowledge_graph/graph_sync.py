"""Graph sync pipeline — T-082.

GraphSyncPipeline.sync(event, state):
  extract → build_relationships → upsert_node → upsert_edge → log_audit

Idempotent: re-syncing the same event produces the same graph state.
Failures during sync do NOT corrupt canonical state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from knowledge_graph.entity_extractor import EntityExtractor
from knowledge_graph.graph_schema import GraphEdge, GraphNode
from knowledge_graph.graph_store import KuzuGraphStore
from knowledge_graph.relationship_builder import RelationshipBuilder

logger = logging.getLogger(__name__)


class GraphSyncPipeline:
    """Orchestrates entity extraction, edge building, and graph persistence.

    All sync errors are caught and logged — they never propagate to callers.
    A failed sync does not affect canonical state or event processing.
    """

    def __init__(
        self,
        store: Optional[KuzuGraphStore] = None,
        extractor: Optional[EntityExtractor] = None,
        builder: Optional[RelationshipBuilder] = None,
        audit_logger=None,
    ) -> None:
        self._store = store or KuzuGraphStore()
        self._extractor = extractor or EntityExtractor()
        self._builder = builder or RelationshipBuilder()
        self._audit_logger = audit_logger

    def sync(
        self,
        event: Dict[str, Any],
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Sync an event and state into the knowledge graph.

        Sequence:
          1. extract nodes
          2. build relationships
          3. upsert each node
          4. upsert each edge
          5. log audit record

        Args:
            event: Raw event dict.
            state: Canonical state slice dict.

        Returns:
            Summary dict: nodes_upserted, edges_upserted, errors, success.
        """
        result = {
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_failed": 0,
            "edges_failed": 0,
            "errors": [],
            "success": False,
        }

        project_id = event.get("project_id", "unknown")
        event_type = event.get("event_type", "unknown")

        # Step 1: Extract nodes
        nodes: List[GraphNode] = []
        try:
            nodes = self._extractor.extract(event, state)
        except Exception as e:
            logger.warning(
                "GraphSyncPipeline: entity extraction failed for %s: %s",
                project_id,
                e,
            )
            result["errors"].append(f"extraction: {e}")
            # Return early — no nodes to sync
            return result

        # Step 2: Build relationships
        edges: List[GraphEdge] = []
        try:
            edges = self._builder.build(event, nodes, state)
        except Exception as e:
            logger.warning(
                "GraphSyncPipeline: relationship building failed for %s: %s",
                project_id,
                e,
            )
            result["errors"].append(f"relationship_build: {e}")
            # Continue with nodes only — no edges

        # Step 3: Upsert nodes
        for node in nodes:
            try:
                success = self._store.upsert_node(node)
                if success:
                    result["nodes_upserted"] += 1
                else:
                    result["nodes_failed"] += 1
            except Exception as e:
                result["nodes_failed"] += 1
                result["errors"].append(f"upsert_node({node.node_id}): {e}")

        # Step 4: Upsert edges
        for edge in edges:
            try:
                success = self._store.upsert_edge(edge)
                if success:
                    result["edges_upserted"] += 1
                else:
                    result["edges_failed"] += 1
            except Exception as e:
                result["edges_failed"] += 1
                result["errors"].append(f"upsert_edge({edge.from_id}→{edge.to_id}): {e}")

        # Step 5: Audit log
        if self._audit_logger:
            try:
                import asyncio
                audit_record = {
                    "actor": "graph_sync_pipeline",
                    "action": "graph_sync",
                    "project_id": project_id,
                    "inputs": {"event_type": event_type},
                    "outputs": {
                        "nodes_upserted": result["nodes_upserted"],
                        "edges_upserted": result["edges_upserted"],
                    },
                    "policy_result": "allow_with_audit",
                }
                if asyncio.get_event_loop().is_running():
                    asyncio.ensure_future(self._audit_logger.log(**audit_record))
                else:
                    asyncio.run(self._audit_logger.log(**audit_record))
            except Exception as e:
                logger.warning("GraphSyncPipeline: audit log failed: %s", e)

        result["success"] = (
            result["nodes_failed"] == 0
            and result["edges_failed"] == 0
            and not result["errors"]
        )

        if result["nodes_upserted"] or result["edges_upserted"]:
            logger.info(
                "GraphSyncPipeline: synced %s — %d nodes, %d edges",
                project_id,
                result["nodes_upserted"],
                result["edges_upserted"],
            )

        return result
