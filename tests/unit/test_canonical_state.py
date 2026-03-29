"""Unit tests for CanonicalStateStore.

Each test creates a fresh in-memory SQLite database — no Docker, no external deps.
Covers: upsert idempotency, get returns None for unknown, update_health field
isolation, append_decision, list_projects.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from state.canonical_state import CanonicalStateStore
from state.schemas import (
    CanonicalProjectState,
    DecisionRecord,
    HealthMetrics,
    Milestone,
    ProjectIdentity,
)
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _identity(project_id: str) -> ProjectIdentity:
    return ProjectIdentity(project_id=project_id, name=f"Project {project_id}", tenant_id="test")


def _state(project_id: str, version: int = 0) -> CanonicalProjectState:
    return CanonicalProjectState(
        project_id=project_id,
        identity=_identity(project_id),
        version=version,
    )


def _future(days: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store():
    """Fresh in-memory CanonicalStateStore per test."""
    s = CanonicalStateStore(db_path=":memory:")
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_unknown_project_returns_none(store):
    result = await store.get("does_not_exist")
    assert result is None


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip(store):
    state = _state("proj_001")
    await store.upsert(state)

    fetched = await store.get("proj_001")
    assert fetched is not None
    assert fetched.project_id == "proj_001"
    assert fetched.identity.name == "Project proj_001"


@pytest.mark.asyncio
async def test_upsert_is_idempotent(store):
    state = _state("proj_idem", version=1)
    await store.upsert(state)
    await store.upsert(state)  # second upsert same data

    fetched = await store.get("proj_idem")
    assert fetched is not None
    assert fetched.version == 1  # version unchanged from double-upsert of same state


@pytest.mark.asyncio
async def test_upsert_overwrites_previous_state(store):
    state_v1 = _state("proj_over", version=1)
    await store.upsert(state_v1)

    state_v2 = _state("proj_over", version=2)
    await store.upsert(state_v2)

    fetched = await store.get("proj_over")
    assert fetched.version == 2


@pytest.mark.asyncio
async def test_update_health_changes_only_health_fields(store):
    state = _state("proj_health")
    state.identity.owner = "alice"
    await store.upsert(state)

    updated = await store.update_health(
        "proj_health",
        {"schedule_health": 0.5, "open_blockers": 3},
    )
    assert updated is True

    fetched = await store.get("proj_health")
    assert fetched.health.schedule_health == 0.5
    assert fetched.health.open_blockers == 3
    # Unrelated fields must be preserved
    assert fetched.identity.owner == "alice"
    # Other health fields not included in the update stay at their defaults
    assert fetched.health.resource_health == 1.0


@pytest.mark.asyncio
async def test_update_health_unknown_project_returns_false(store):
    result = await store.update_health("nonexistent", {"schedule_health": 0.1})
    assert result is False


@pytest.mark.asyncio
async def test_update_health_ignores_unknown_fields(store):
    state = _state("proj_hfield")
    await store.upsert(state)

    await store.update_health("proj_hfield", {"nonexistent_field": 99, "scope_health": 0.8})

    fetched = await store.get("proj_hfield")
    assert fetched.health.scope_health == 0.8


@pytest.mark.asyncio
async def test_append_decision_adds_to_history(store):
    state = _state("proj_dec")
    await store.upsert(state)

    decision = DecisionRecord(
        decision_id="d001",
        agent_name="risk_intelligence",
        decision_type="decision_preparation",
        summary="Risk threshold exceeded for milestone Alpha",
        policy_action="approval_required",
    )
    result = await store.append_decision("proj_dec", decision)
    assert result is True

    fetched = await store.get("proj_dec")
    assert len(fetched.decision_history) == 1
    assert fetched.decision_history[0].decision_id == "d001"


@pytest.mark.asyncio
async def test_append_decision_unknown_project_returns_false(store):
    decision = DecisionRecord(
        decision_id="d_ghost",
        agent_name="planning",
        decision_type="observation",
        summary="No-op",
        policy_action="allow",
    )
    result = await store.append_decision("ghost_project", decision)
    assert result is False


@pytest.mark.asyncio
async def test_append_multiple_decisions_preserves_order(store):
    state = _state("proj_multi_dec")
    await store.upsert(state)

    for i in range(3):
        d = DecisionRecord(
            decision_id=f"d{i:03d}",
            agent_name="communication",
            decision_type="execution",
            summary=f"Report {i}",
            policy_action="allow",
        )
        await store.append_decision("proj_multi_dec", d)

    fetched = await store.get("proj_multi_dec")
    ids = [d.decision_id for d in fetched.decision_history]
    assert ids == ["d000", "d001", "d002"]


@pytest.mark.asyncio
async def test_list_projects_returns_all_ids(store):
    for pid in ("p1", "p2", "p3"):
        await store.upsert(_state(pid))

    projects = await store.list_projects()
    assert set(projects) == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_list_projects_empty_store(store):
    result = await store.list_projects()
    assert result == []


@pytest.mark.asyncio
async def test_state_with_milestones_roundtrip(store):
    state = _state("proj_ms")
    state.milestones = [
        Milestone(
            milestone_id="m1",
            name="Beta Launch",
            due_date=_future(30),
            status="on_track",
            completion_percentage=0.45,
        )
    ]
    await store.upsert(state)

    fetched = await store.get("proj_ms")
    assert len(fetched.milestones) == 1
    assert fetched.milestones[0].name == "Beta Launch"
    assert abs(fetched.milestones[0].completion_percentage - 0.45) < 0.001


@pytest.mark.asyncio
async def test_update_health_increments_version(store):
    state = _state("proj_ver", version=5)
    await store.upsert(state)

    await store.update_health("proj_ver", {"dependency_health": 0.7})

    fetched = await store.get("proj_ver")
    assert fetched.version == 6
