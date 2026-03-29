"""Entity extractor — T-080.

Extracts GraphNode instances from events and canonical state.
Does NOT create TASK nodes — task-level granularity is out of graph scope.
Valid extractable types: PROJECT, MILESTONE, TEAM, STAKEHOLDER, RISK, ISSUE, DECISION.

Hard assertion (not log) if disallowed NodeType is attempted.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from knowledge_graph.graph_schema import GraphNode, NodeType

logger = logging.getLogger(__name__)

# Node types allowed to be created by the extractor (Title Case values)
_ALLOWED_TYPES = frozenset({
    NodeType.PROJECT,
    NodeType.MILESTONE,
    NodeType.TEAM,
    NodeType.STAKEHOLDER,
    NodeType.RISK,
    NodeType.ISSUE,
    NodeType.DECISION,
})

# Convenient lookups by value for internal assertions
_ALLOWED_VALUES = frozenset(t.value for t in _ALLOWED_TYPES)

# TASK is explicitly excluded — not in the NodeType enum by design
# (task-level granularity is out of graph scope per README.md)
_DISALLOWED_VALUES = frozenset({"task", "Task", "TASK"})


def _assert_allowed(node_type: NodeType) -> None:
    """Hard assertion — raises ValueError if node_type is disallowed."""
    if node_type.value in _DISALLOWED_VALUES:
        raise ValueError(
            f"NodeType {node_type.value!r} is not allowed in the knowledge graph. "
            "Task-level granularity is out of graph scope. "
            "See README.md §Knowledge Graph for scope boundaries."
        )
    if node_type not in _ALLOWED_TYPES:
        raise ValueError(
            f"NodeType {node_type.value!r} is not in the allowed extraction types: "
            + ", ".join(t.value for t in _ALLOWED_TYPES)
        )


class EntityExtractor:
    """Extracts graph-relevant entities from PMO events and state.

    Rules:
    - TASK nodes are never created.
    - PROJECT node is always created from canonical_state.project_id.
    - MILESTONE nodes from at_risk or delayed milestones.
    - RISK node when risk is detected (risk.detected or risk_score >= 0.20).
    - ISSUE node for blockers (dependency.blocked).
    """

    def extract(
        self,
        event: Dict[str, Any],
        state: Dict[str, Any],
    ) -> List[GraphNode]:
        """Extract graph nodes from event + canonical state.

        Args:
            event: Raw event dict with event_type, project_id, payload.
            state: Canonical state slice dict.

        Returns:
            List of GraphNode instances. May be empty. Never raises.
        """
        nodes: List[GraphNode] = []

        project_id = event.get("project_id") or state.get("project_id", "")
        event_type = event.get("event_type", "")
        payload = event.get("payload", {})

        # Always: PROJECT node
        if project_id:
            nodes.append(self._make_project_node(project_id, state))

        # Milestones: extract at_risk and delayed
        for ms in state.get("milestones", []):
            if ms.get("status") in ("at_risk", "delayed"):
                nodes.append(self._make_milestone_node(ms, project_id))

        # RISK node: risk.detected or dependency.blocked with risk context
        if event_type == "risk.detected":
            risk_id = f"risk_{project_id}_{payload.get('task_id', 'unknown')}"
            _assert_allowed(NodeType.RISK)
            nodes.append(GraphNode(
                node_id=risk_id,
                node_type=NodeType.RISK,
                properties={
                    "project_id": project_id,
                    "risk_type": payload.get("risk_type", "unknown"),
                    "confidence": payload.get("confidence", 0.0),
                    "description": payload.get("description", ""),
                    "source_event_type": event_type,
                },
            ))

        # ISSUE node: dependency.blocked
        if event_type == "dependency.blocked":
            issue_id = f"issue_{project_id}_{payload.get('task_id', 'unknown')}"
            _assert_allowed(NodeType.ISSUE)
            nodes.append(GraphNode(
                node_id=issue_id,
                node_type=NodeType.ISSUE,
                properties={
                    "project_id": project_id,
                    "blocked_by": payload.get("blocked_by", ""),
                    "task_id": payload.get("task_id", ""),
                    "severity": payload.get("severity", 0.0),
                    "source_event_type": event_type,
                },
            ))

            # Also create a RISK node when a blocker has detected risk
            risk_score = payload.get("severity", 0.0)
            if risk_score >= 0.20:
                risk_id = f"risk_blocker_{project_id}_{payload.get('task_id', 'unknown')}"
                _assert_allowed(NodeType.RISK)
                nodes.append(GraphNode(
                    node_id=risk_id,
                    node_type=NodeType.RISK,
                    properties={
                        "project_id": project_id,
                        "risk_type": "dependency_blocker",
                        "severity": risk_score,
                        "source_event_type": event_type,
                    },
                ))

        return nodes

    def _make_project_node(
        self, project_id: str, state: Dict[str, Any]
    ) -> GraphNode:
        _assert_allowed(NodeType.PROJECT)
        return GraphNode(
            node_id=project_id,
            node_type=NodeType.PROJECT,
            properties={
                "schedule_health": state.get("schedule_health", 0.75),
                "open_blockers": state.get("open_blockers", 0),
                "tenant_id": state.get("tenant_id", "default"),
            },
        )

    def _make_milestone_node(
        self, milestone: Dict[str, Any], project_id: str
    ) -> GraphNode:
        _assert_allowed(NodeType.MILESTONE)
        return GraphNode(
            node_id=milestone.get("id", f"ms_{project_id}_unknown"),
            node_type=NodeType.MILESTONE,
            properties={
                "project_id": project_id,
                "status": milestone.get("status", "unknown"),
                "due_days_from_start": milestone.get("due_days_from_start"),
                "name": milestone.get("name", ""),
            },
        )
