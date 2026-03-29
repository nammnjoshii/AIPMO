"""Node and edge type definitions for the Delivery Knowledge Graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class NodeType(str, Enum):
    PROJECT = "Project"
    MILESTONE = "Milestone"
    TEAM = "Team"
    STAKEHOLDER = "Stakeholder"
    RISK = "Risk"
    ISSUE = "Issue"
    DECISION = "Decision"
    DEPENDENCY = "Dependency"
    SYSTEM = "System"
    CAPABILITY = "Capability"
    RESOURCE = "Resource"
    PROGRAM = "Program"
    PORTFOLIO = "Portfolio"
    LESSON = "Lesson"
    MITIGATION = "Mitigation"
    OUTCOME = "Outcome"
    ESCALATION = "Escalation"


class EdgeType(str, Enum):
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKS = "BLOCKS"
    OWNS = "OWNS"
    ASSIGNED_TO = "ASSIGNED_TO"
    ESCALATED_TO = "ESCALATED_TO"
    MITIGATES = "MITIGATES"
    CAUSED_BY = "CAUSED_BY"
    AFFECTS = "AFFECTS"
    PART_OF = "PART_OF"
    APPROVED_BY = "APPROVED_BY"
    RESOLVED_BY = "RESOLVED_BY"
    RELATED_TO = "RELATED_TO"
    LEARNED_FROM = "LEARNED_FROM"
    PROPAGATES_TO = "PROPAGATES_TO"
    REPORTED_BY = "REPORTED_BY"


@dataclass
class GraphNode:
    node_id: str
    node_type: NodeType
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "properties": self.properties,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class GraphEdge:
    edge_id: str
    edge_type: EdgeType
    source_node_id: str
    target_node_id: str
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "properties": self.properties,
            "created_at": self.created_at.isoformat(),
        }
