"""Unit test fixtures — T-013."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agents.base_agent import AgentInput
from events.schemas.event_types import EventType
from state.schemas import (
    CanonicalProjectState,
    HealthMetrics,
    Milestone,
    ProjectIdentity,
)


@pytest.fixture
def demo_project_state() -> CanonicalProjectState:
    """Canonical project state for proj_demo_001 with realistic health values."""
    return CanonicalProjectState(
        project_id="proj_demo_001",
        identity=ProjectIdentity(
            project_id="proj_demo_001",
            name="Demo Project Alpha",
            tenant_id="default",
            owner="alice@example.com",
        ),
        health=HealthMetrics(
            schedule_health=0.68,
            resource_health=0.80,
            scope_health=0.90,
            dependency_health=0.55,
            overall_health=0.68,
            open_blockers=3,
            tasks_completed=47,
            tasks_total=120,
        ),
        milestones=[
            Milestone(
                milestone_id="ms_001",
                name="Beta Release",
                due_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
                status="at_risk",
                completion_percentage=0.62,
            )
        ],
    )


@pytest.fixture
def mock_agent_input(demo_project_state: CanonicalProjectState) -> AgentInput:
    """Valid AgentInput for a task.updated event on proj_demo_001."""
    return AgentInput(
        project_id="proj_demo_001",
        event_type=EventType.TASK_UPDATED,
        canonical_state=demo_project_state.model_dump(mode="json"),
        graph_context={"graph_available": False, "nodes": [], "edges": []},
        historical_cases=[],
        policy_context={
            "generate_status_report": "allow",
            "escalate_issue": "approval_required",
        },
        signal_quality={
            "confidence_score": 0.85,
            "is_decayed": False,
            "source": "github_issues",
            "reliability": "high",
        },
        tenant_id="default",
    )
