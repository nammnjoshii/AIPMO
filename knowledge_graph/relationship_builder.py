"""Relationship builder — T-081.

Builds GraphEdge instances from extracted nodes and event context.
Only creates edges with types in EdgeType enum.
Hard assertion (not log) if disallowed edge type is attempted.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from knowledge_graph.graph_schema import EdgeType, GraphEdge, GraphNode, NodeType

logger = logging.getLogger(__name__)


def _assert_valid_edge_type(edge_type: EdgeType) -> None:
    """Hard assertion — raises ValueError if edge_type is not in EdgeType enum."""
    if not isinstance(edge_type, EdgeType):
        raise ValueError(
            f"Edge type must be an EdgeType enum member. Got: {edge_type!r}. "
            "Only EdgeType enum values are permitted."
        )


class RelationshipBuilder:
    """Builds edges between extracted nodes based on event context.

    Rules:
    - Only EdgeType enum values are used — hard assertion on violation.
    - PROJECT → MILESTONE: CONTAINS
    - PROJECT → RISK: HAS_RISK
    - PROJECT → ISSUE: HAS_ISSUE
    - ISSUE → PROJECT: BLOCKS (when blocked_by present)
    - RISK → MILESTONE: AFFECTS (when risk links to milestone)
    """

    def build(
        self,
        event: Dict[str, Any],
        nodes: List[GraphNode],
        state: Dict[str, Any],
    ) -> List[GraphEdge]:
        """Build edges between the given nodes based on event + state context.

        Args:
            event: Raw event dict.
            nodes: List of GraphNode instances already extracted.
            state: Canonical state slice.

        Returns:
            List of GraphEdge instances. May be empty. Never raises on normal usage.
        """
        edges: List[GraphEdge] = []

        # Index nodes by type for quick lookup
        by_type: Dict[NodeType, List[GraphNode]] = {}
        for node in nodes:
            by_type.setdefault(node.node_type, []).append(node)

        project_nodes = by_type.get(NodeType.PROJECT, [])
        milestone_nodes = by_type.get(NodeType.MILESTONE, [])
        risk_nodes = by_type.get(NodeType.RISK, [])
        issue_nodes = by_type.get(NodeType.ISSUE, [])

        project_id = event.get("project_id", "")

        # PROJECT → MILESTONE: PART_OF (milestone is part of project)
        for proj in project_nodes:
            for ms in milestone_nodes:
                edge_type = EdgeType.PART_OF
                _assert_valid_edge_type(edge_type)
                edges.append(GraphEdge(
                    from_id=ms.node_id,
                    to_id=proj.node_id,
                    edge_type=edge_type,
                    properties={"project_id": project_id},
                ))

        # PROJECT → RISK: RELATED_TO
        for proj in project_nodes:
            for risk in risk_nodes:
                edge_type = EdgeType.RELATED_TO
                _assert_valid_edge_type(edge_type)
                edges.append(GraphEdge(
                    from_id=proj.node_id,
                    to_id=risk.node_id,
                    edge_type=edge_type,
                    properties={"project_id": project_id},
                ))

        # PROJECT → ISSUE: RELATED_TO
        for proj in project_nodes:
            for issue in issue_nodes:
                edge_type = EdgeType.RELATED_TO
                _assert_valid_edge_type(edge_type)
                edges.append(GraphEdge(
                    from_id=proj.node_id,
                    to_id=issue.node_id,
                    edge_type=edge_type,
                    properties={"project_id": project_id},
                ))

        # ISSUE → PROJECT: BLOCKS (when blocked_by references another project)
        payload = event.get("payload", {})
        upstream_project = payload.get("upstream_project", "")
        if upstream_project and issue_nodes:
            for issue in issue_nodes:
                edge_type = EdgeType.BLOCKS
                _assert_valid_edge_type(edge_type)
                edges.append(GraphEdge(
                    from_id=issue.node_id,
                    to_id=project_id,
                    edge_type=edge_type,
                    properties={"upstream_project": upstream_project},
                ))

        # RISK → MILESTONE: AFFECTS (when milestones are at_risk or delayed)
        for risk in risk_nodes:
            for ms in milestone_nodes:
                if ms.properties and ms.properties.get("status") in ("at_risk", "delayed"):
                    edge_type = EdgeType.AFFECTS
                    _assert_valid_edge_type(edge_type)
                    edges.append(GraphEdge(
                        from_id=risk.node_id,
                        to_id=ms.node_id,
                        edge_type=edge_type,
                        properties={
                            "risk_type": risk.properties.get("risk_type", "") if risk.properties else "",
                        },
                    ))

        return edges
