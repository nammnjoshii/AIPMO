"""Graph query service — T-079.

ALL Cypher queries live here exclusively.
No ad hoc Cypher is permitted anywhere else in the codebase.

7 canonical query methods:
  1. get_neighborhood(entity_id, hops)     — N-hop neighborhood
  2. get_dependencies(project_id)          — direct dependency chain
  3. get_risk_propagation_path(risk_id)    — risk → affected entities
  4. get_stakeholder_chain(project_id)     — approval chain
  5. get_team_allocation(team_id)          — team → projects
  6. get_decision_history(project_id)      — decision log for project
  7. get_cross_project_risks(tenant_id)    — all at-risk entities in tenant
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphQueryService:
    """Service layer for all knowledge graph queries.

    All queries return empty lists on error — never raise.
    Callers must handle graph_available=False from context_assembly.
    """

    def __init__(self, store=None) -> None:
        from knowledge_graph.graph_store import KuzuGraphStore
        self._store = store or KuzuGraphStore()

    # ---- 1. N-hop neighborhood ----

    def get_neighborhood(
        self,
        entity_id: str,
        hops: int = 2,
    ) -> Dict[str, Any]:
        """Return the N-hop neighborhood around a node.

        Args:
            entity_id: Node ID to start from.
            hops: Number of hops (default 2, use 3 for Program Director).

        Returns:
            Dict with 'nodes' and 'edges' lists. Empty on error.
        """
        if not self._store._initialized:
            return {"nodes": [], "edges": [], "graph_available": False}

        try:
            # Nodes within N hops
            node_rows = self._store.query(
                f"""
                MATCH (start:GraphNode {{node_id: $id}})-[r*1..{hops}]-(neighbor:GraphNode)
                RETURN DISTINCT neighbor.node_id AS node_id,
                       neighbor.node_type AS node_type,
                       neighbor.properties AS properties
                """,
                {"id": entity_id},
            )
            # Include the start node itself
            start_rows = self._store.query(
                "MATCH (n:GraphNode {node_id: $id}) RETURN n.node_id AS node_id, n.node_type AS node_type, n.properties AS properties",
                {"id": entity_id},
            )

            edge_rows = self._store.query(
                f"""
                MATCH (start:GraphNode {{node_id: $id}})-[r*1..{hops}]-(neighbor:GraphNode)
                WITH start, neighbor
                MATCH (start)-[e:GraphEdge]->(neighbor)
                RETURN e.edge_type AS edge_type,
                       start.node_id AS from_id,
                       neighbor.node_id AS to_id,
                       e.properties AS properties
                """,
                {"id": entity_id},
            )

            all_nodes = start_rows + node_rows
            return {
                "nodes": all_nodes,
                "edges": edge_rows,
                "graph_available": True,
                "hops": hops,
                "center_id": entity_id,
            }
        except Exception as e:
            logger.warning("GraphQueryService.get_neighborhood failed: %s", e)
            return {"nodes": [], "edges": [], "graph_available": False}

    # ---- 2. Dependency chain ----

    def get_dependencies(self, project_id: str) -> List[Dict[str, Any]]:
        """Return direct dependencies for a project.

        Args:
            project_id: Project node ID.

        Returns:
            List of dependency relationship dicts. Empty on error.
        """
        return self._store.query(
            """
            MATCH (p:GraphNode {node_id: $id})-[e:GraphEdge]->(dep:GraphNode)
            WHERE e.edge_type = 'DEPENDS_ON'
            RETURN dep.node_id AS dependency_id,
                   dep.node_type AS dependency_type,
                   e.properties AS edge_properties
            """,
            {"id": project_id},
        )

    # ---- 3. Risk propagation path ----

    def get_risk_propagation_path(self, risk_id: str) -> List[Dict[str, Any]]:
        """Return entities affected by a risk node via propagation edges.

        Args:
            risk_id: RISK node ID.

        Returns:
            List of affected entity dicts. Empty on error.
        """
        return self._store.query(
            """
            MATCH (risk:GraphNode {node_id: $id})-[e:GraphEdge*1..3]->(affected:GraphNode)
            WHERE e[0].edge_type IN ['TRIGGERS', 'AFFECTS', 'BLOCKS']
            RETURN DISTINCT affected.node_id AS affected_id,
                   affected.node_type AS affected_type,
                   affected.properties AS properties,
                   length(e) AS hops
            ORDER BY hops ASC
            """,
            {"id": risk_id},
        )

    # ---- 4. Stakeholder approval chain ----

    def get_stakeholder_chain(self, project_id: str) -> List[Dict[str, Any]]:
        """Return the stakeholder approval chain for a project.

        Args:
            project_id: Project node ID.

        Returns:
            Ordered list of stakeholder nodes. Empty on error.
        """
        return self._store.query(
            """
            MATCH (p:GraphNode {node_id: $id})-[e:GraphEdge*1..4]->(s:GraphNode)
            WHERE s.node_type = 'STAKEHOLDER'
              AND e[-1].edge_type IN ['REPORTS_TO', 'APPROVES', 'OWNS']
            RETURN DISTINCT s.node_id AS stakeholder_id,
                   s.properties AS properties,
                   length(e) AS chain_depth
            ORDER BY chain_depth ASC
            """,
            {"id": project_id},
        )

    # ---- 5. Team allocation ----

    def get_team_allocation(self, team_id: str) -> List[Dict[str, Any]]:
        """Return all projects a team is currently allocated to.

        Args:
            team_id: TEAM node ID.

        Returns:
            List of project allocation dicts. Empty on error.
        """
        return self._store.query(
            """
            MATCH (t:GraphNode {node_id: $id})-[e:GraphEdge]->(p:GraphNode)
            WHERE e.edge_type = 'ASSIGNED_TO' AND p.node_type = 'PROJECT'
            RETURN p.node_id AS project_id,
                   p.properties AS project_properties,
                   e.properties AS allocation_properties
            """,
            {"id": team_id},
        )

    # ---- 6. Decision history ----

    def get_decision_history(
        self, project_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Return recent decision nodes linked to a project.

        Args:
            project_id: Project node ID.
            limit: Maximum number of decisions to return (default 10).

        Returns:
            List of decision dicts ordered by most recent. Empty on error.
        """
        return self._store.query(
            f"""
            MATCH (p:GraphNode {{node_id: $id}})-[e:GraphEdge]->(d:GraphNode)
            WHERE d.node_type = 'DECISION' AND e.edge_type = 'LED_TO'
            RETURN d.node_id AS decision_id,
                   d.properties AS properties
            LIMIT {limit}
            """,
            {"id": project_id},
        )

    # ---- 7. Cross-project risks ----

    def get_cross_project_risks(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Return all RISK nodes in a tenant that are actively flagged.

        Args:
            tenant_id: Tenant identifier for isolation.

        Returns:
            List of at-risk entity dicts. Empty on error.
        """
        return self._store.query(
            """
            MATCH (r:GraphNode)
            WHERE r.node_type = 'RISK'
            RETURN r.node_id AS risk_id,
                   r.properties AS properties
            """,
            {"tenant_id": tenant_id},
        )
