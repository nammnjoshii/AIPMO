"""Unit tests for all data contracts — T-012.

Run: pytest tests/unit/test_contracts.py -v
All tests must pass with zero external dependencies.
"""
import pytest
from datetime import datetime, timezone

from agents.base_agent import AgentOutput, AgentInput, DecisionType, PolicyAction
from events.schemas.event_types import (
    EventType,
    DeliveryEvent,
    TaskUpdatedPayload,
    MilestoneUpdatedPayload,
    RiskDetectedPayload,
    DependencyBlockedPayload,
)
from knowledge_graph.graph_schema import NodeType, EdgeType, GraphNode, GraphEdge
from policy.schemas import PolicyEvaluationResult, ProjectPolicy
from state.schemas import (
    CanonicalProjectState,
    ProjectIdentity,
    HealthMetrics,
    Milestone,
    SourceReliabilityProfile,
)


# ---- AgentOutput contract enforcement ----

def test_agent_output_raises_on_empty_uncertainty_notes():
    with pytest.raises(ValueError, match="empty uncertainty_notes"):
        AgentOutput(
            agent_name="test_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=0.8,
            evidence=["some evidence"],
            decision_factors=["factor A"],
            uncertainty_notes=[],  # must raise
            policy_action=PolicyAction.ALLOW,
        )


def test_agent_output_raises_on_confidence_score_too_high():
    with pytest.raises(ValueError, match="confidence_score"):
        AgentOutput(
            agent_name="test_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=1.5,
            evidence=["e"],
            decision_factors=["f"],
            uncertainty_notes=["some note"],
            policy_action=PolicyAction.ALLOW,
        )


def test_agent_output_raises_on_confidence_score_negative():
    with pytest.raises(ValueError, match="confidence_score"):
        AgentOutput(
            agent_name="test_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=-0.1,
            evidence=["e"],
            decision_factors=["f"],
            uncertainty_notes=["some note"],
            policy_action=PolicyAction.ALLOW,
        )


def test_agent_output_valid_creation():
    output = AgentOutput(
        agent_name="test_agent",
        decision_type=DecisionType.DECISION_PREPARATION,
        confidence_score=0.75,
        evidence=["signal from jira"],
        decision_factors=["blocker count increased"],
        uncertainty_notes=["Only 3 days of signal available — small sample"],
        policy_action=PolicyAction.ALLOW_WITH_AUDIT,
        recommendation="Escalate to PM for review",
    )
    assert output.agent_name == "test_agent"
    assert output.confidence_score == 0.75
    assert len(output.uncertainty_notes) > 0


def test_agent_output_boundary_confidence_zero():
    output = AgentOutput(
        agent_name="a",
        decision_type=DecisionType.OBSERVATION,
        confidence_score=0.0,
        evidence=["e"],
        decision_factors=["f"],
        uncertainty_notes=["Very low confidence — no data available"],
        policy_action=PolicyAction.DENY,
    )
    assert output.confidence_score == 0.0


def test_agent_output_boundary_confidence_one():
    output = AgentOutput(
        agent_name="a",
        decision_type=DecisionType.EXECUTION,
        confidence_score=1.0,
        evidence=["e"],
        decision_factors=["f"],
        uncertainty_notes=["High confidence but no model is perfect"],
        policy_action=PolicyAction.ALLOW,
    )
    assert output.confidence_score == 1.0


# ---- EventType enum ----

def test_event_type_enum_has_seven_values():
    assert len(EventType) == 7


def test_event_type_values():
    assert EventType.TASK_UPDATED == "task.updated"
    assert EventType.DEPENDENCY_BLOCKED == "dependency.blocked"
    assert EventType.RISK_DETECTED == "risk.detected"


def test_delivery_event_instantiation():
    event = DeliveryEvent(
        event_type=EventType.TASK_UPDATED,
        project_id="proj_demo_001",
        source="github_issues",
        payload={"task_id": "task_42", "new_status": "blocked"},
    )
    assert event.project_id == "proj_demo_001"
    assert event.timestamp.tzinfo is not None


# ---- CanonicalProjectState JSON round-trip ----

def test_canonical_state_json_round_trip():
    state = CanonicalProjectState(
        project_id="proj_demo_001",
        identity=ProjectIdentity(
            project_id="proj_demo_001",
            name="Demo Project",
            tenant_id="default",
        ),
        health=HealthMetrics(
            schedule_health=0.68,
            open_blockers=3,
        ),
    )
    dumped = state.model_dump(mode="json")
    restored = CanonicalProjectState.model_validate(dumped)
    assert restored.project_id == state.project_id
    assert restored.health.open_blockers == 3


def test_health_metrics_rejects_out_of_range():
    with pytest.raises(Exception):
        HealthMetrics(schedule_health=1.5)


def test_health_metrics_rejects_negative():
    with pytest.raises(Exception):
        HealthMetrics(resource_health=-0.1)


# ---- NodeType and EdgeType ----

def test_node_type_has_seventeen_values():
    assert len(NodeType) == 17


def test_edge_type_has_fifteen_values():
    assert len(EdgeType) == 15


def test_graph_node_serializable():
    node = GraphNode(node_id="proj_001", node_type=NodeType.PROJECT)
    d = node.to_dict()
    assert d["node_type"] == "Project"
    assert "created_at" in d


def test_graph_edge_serializable():
    edge = GraphEdge(
        edge_id="e1",
        edge_type=EdgeType.BLOCKS,
        source_node_id="task_1",
        target_node_id="milestone_2",
    )
    d = edge.to_dict()
    assert d["edge_type"] == "BLOCKS"


# ---- PolicyAction ----

def test_policy_action_has_five_values():
    assert len(PolicyAction) == 5
    values = {p.value for p in PolicyAction}
    assert values == {"allow", "allow_with_audit", "approval_required", "deny", "escalate"}


# ---- ProjectPolicy validates from dict ----

def test_project_policy_validates():
    policy = ProjectPolicy(
        version="1.0",
        scope="project",
        project_id="proj_demo_001",
        actions={
            "generate_status_report": PolicyAction.ALLOW,
            "escalate_issue": PolicyAction.APPROVAL_REQUIRED,
        },
    )
    assert policy.actions["generate_status_report"] == PolicyAction.ALLOW
