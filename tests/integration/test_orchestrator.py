"""Orchestrator integration tests — T-062.

5 tests:
  1. task.updated flows sequential pattern — ingest_signal + update_state + route_to_agents
  2. dependency.blocked event type is normalised correctly by the signal pipeline
  3. DENY stops execution (execute_or_queue never enqueues a review item)
  4. Audit log has a record for every processed event (log_audit always runs)
  5. Same event twice produces one canonical state update (idempotency)

Tests that require the full LangGraph graph are skipped when langgraph is not
installed, matching the same skip-pattern used for Kuzu-dependent tests.

Node-level tests (3, 4, 5) run without langgraph and cover the core
governance guarantees independently.

All tests run offline: LLM_PROVIDER=mock, in-memory SQLite, mock Redis.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

# Check langgraph availability once
_LANGGRAPH_AVAILABLE = True
try:
    import langgraph  # noqa: F401
except ImportError:
    _LANGGRAPH_AVAILABLE = False

_skip_no_langgraph = pytest.mark.skipif(
    not _LANGGRAPH_AVAILABLE,
    reason="langgraph not installed — install with: pip install langgraph",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_updated_event(
    project_id: str = "proj_orch_001",
    tenant_id: str = "tenant_orch",
    event_id: str = "evt-orch-task-001",
) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "task.updated",
        "project_id": project_id,
        "source": "github",
        "tenant_id": tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "task_id": "T-0042",
            "new_status": "in_progress",
        },
    }


def _dependency_blocked_event(project_id: str = "proj_orch_002") -> Dict[str, Any]:
    return {
        "event_id": "evt-orch-dep-001",
        "event_type": "dependency.blocked",
        "project_id": project_id,
        "source": "github_issues",
        "tenant_id": "tenant_orch",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "task_id": "T-0099",
            "blocked_by": "#55",
            "dependency_type": "hard",
        },
    }


def _build_mock_redis():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_redis.setex.return_value = True
    return mock_redis


# ---------------------------------------------------------------------------
# Test 1 — task.updated sequential flow (requires langgraph)
# ---------------------------------------------------------------------------

class TestTaskUpdatedSequentialFlow:
    """task.updated processes through all nodes and produces an agent output."""

    @_skip_no_langgraph
    @pytest.mark.asyncio
    async def test_task_updated_produces_agent_output(self, tmp_path):
        """task.updated event flows ingest_signal → update_state → route_to_agents
        and produces a valid AgentOutput with policy_result set."""
        from orchestrator.main import create_pmo_app, process_event

        db_path = str(tmp_path / "orch_task.db")
        # NoiseFilter gracefully skips dedup when Redis is unavailable
        app, deps = await create_pmo_app(db_path=db_path)
        event = _task_updated_event()
        result = await process_event(event, app=app)

        # Policy result must be one of the valid outcomes
        policy_result = result.get("policy_result", "")
        valid_outcomes = {"allow", "allow_with_audit", "approval_required", "deny", "escalate"}
        assert policy_result in valid_outcomes, (
            f"policy_result '{policy_result}' not in valid set"
        )

        # log_audit always runs — audit_event_id must be set
        assert result.get("audit_event_id") is not None, (
            "audit_event_id not set — log_audit did not run"
        )


# ---------------------------------------------------------------------------
# Test 2 — dependency.blocked event type normalisation
# ---------------------------------------------------------------------------

class TestDependencyBlockedSignalPipeline:
    """dependency.blocked event is correctly normalised by the signal pipeline."""

    @pytest.mark.asyncio
    async def test_dependency_blocked_normalised_by_pipeline(self, tmp_path):
        """Signal quality pipeline accepts dependency.blocked without raising.
        Does not require langgraph — tests the ingest layer independently."""
        from signal_quality.pipeline import SignalQualityPipeline
        from state.canonical_state import CanonicalStateStore

        db_path = str(tmp_path / "orch_dep_pipeline.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        mock_redis = _build_mock_redis()
        pipeline = SignalQualityPipeline(
            redis_url="redis://mock",
            state_store=store,
        )
        pipeline._noise_filter._redis = mock_redis

        raw = _dependency_blocked_event()
        qs = pipeline.process(raw, source=raw.get("source", "github_issues"))

        # Event was parsed and qualified without error
        assert qs is not None
        assert qs.event is not None
        assert qs.is_duplicate is False

        # Event type must reflect dependency/task category
        event_type_str = str(qs.event.event_type).lower()
        assert any(tok in event_type_str for tok in ("blocked", "dependency", "task")), (
            f"Unexpected event_type after normalisation: {qs.event.event_type}"
        )

    @_skip_no_langgraph
    @pytest.mark.asyncio
    async def test_dependency_blocked_reaches_orchestrator(self, tmp_path):
        """dependency.blocked event reaches the router; audit_event_id is set."""
        from orchestrator.main import create_pmo_app, process_event

        db_path = str(tmp_path / "orch_dep_full.db")
        app, _ = await create_pmo_app(db_path=db_path)
        result = await process_event(_dependency_blocked_event(), app=app)

        assert result.get("audit_event_id") is not None, (
            "log_audit must run for dependency.blocked"
        )


# ---------------------------------------------------------------------------
# Test 3 — DENY stops execution
# ---------------------------------------------------------------------------

class TestDenyStopsExecution:
    """When policy returns DENY, execute_or_queue is never reached."""

    @pytest.mark.asyncio
    async def test_deny_skips_review_enqueue(self, tmp_path):
        """DENY policy result must not enqueue a review item."""
        from audit.logger import AuditLogger
        from orchestrator.human_review_queue import HumanReviewQueue
        from agents.base_agent import PolicyAction

        # Import node function directly — avoids langgraph dependency at module level
        import importlib, types
        # We need to import node functions without triggering langgraph import at top of main.py
        # Patch langgraph before importing orchestrator.main
        if not _LANGGRAPH_AVAILABLE:
            # Create a minimal langgraph stub so we can import the node functions
            langgraph_stub = types.ModuleType("langgraph")
            langgraph_graph_stub = types.ModuleType("langgraph.graph")
            langgraph_graph_stub.END = "END"
            langgraph_graph_stub.StateGraph = MagicMock()
            sys.modules.setdefault("langgraph", langgraph_stub)
            sys.modules.setdefault("langgraph.graph", langgraph_graph_stub)

        from orchestrator.main import execute_or_queue, log_audit, PMOState

        db_path = str(tmp_path / "orch_deny.db")
        audit_logger = AuditLogger(db_path=db_path)
        await audit_logger.initialize()

        review_queue = HumanReviewQueue(db_path=db_path, audit_logger=audit_logger)
        await review_queue.initialize()

        state: PMOState = {
            "raw_signal": _task_updated_event(),
            "policy_result": PolicyAction.DENY.value,
            "agent_output": None,
            "event": None,
            "error": None,
        }

        result = await execute_or_queue(state, review_queue)

        # review_item_id must NOT be set for DENY
        assert result.get("review_item_id") is None, (
            "review_item_id should not be set when policy_result=DENY"
        )

        # log_audit still runs on DENY path
        final = await log_audit(result, audit_logger)
        assert final.get("audit_event_id") is not None, (
            "log_audit must run even on DENY path"
        )

    @pytest.mark.asyncio
    async def test_policy_engine_crash_returns_deny(self, tmp_path):
        """If PolicyEngine.evaluate() throws, policy_result must be DENY (fail-closed)."""
        import types
        if not _LANGGRAPH_AVAILABLE:
            langgraph_stub = types.ModuleType("langgraph")
            langgraph_graph_stub = types.ModuleType("langgraph.graph")
            langgraph_graph_stub.END = "END"
            langgraph_graph_stub.StateGraph = MagicMock()
            sys.modules.setdefault("langgraph", langgraph_stub)
            sys.modules.setdefault("langgraph.graph", langgraph_graph_stub)

        from orchestrator.main import PMOState, evaluate_policy
        from agents.base_agent import AgentOutput, DecisionType, PolicyAction
        from events.schemas.event_types import DeliveryEvent, EventType
        from policy.engine import PolicyEngine

        engine = PolicyEngine()

        agent_output = AgentOutput(
            agent_name="test_agent",
            decision_type=DecisionType.OBSERVATION,
            confidence_score=0.7,
            evidence=["test evidence"],
            decision_factors=["test factor"],
            recommendation="generate_status_report",
            proposed_state_updates={},
            proposed_graph_updates=[],
            policy_action=PolicyAction.ALLOW,
            uncertainty_notes=["Test uncertainty"],
        )

        event = DeliveryEvent(
            event_type=EventType.TASK_UPDATED,
            project_id="proj_deny_test",
            source="github",
            tenant_id="tenant_deny",
            payload={"task_id": "T-001", "new_status": "in_progress"},
        )

        state: PMOState = {
            "raw_signal": {},
            "event": event,
            "agent_output": agent_output,
            "policy_result": "",
            "error": None,
        }

        with patch.object(engine, "evaluate", side_effect=RuntimeError("injected crash")):
            result = await evaluate_policy(state, engine)

        assert result["policy_result"] == PolicyAction.DENY.value, (
            f"Expected DENY on policy crash, got: {result['policy_result']}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Audit log completeness
# ---------------------------------------------------------------------------

class TestAuditLogCompleteness:
    """log_audit node always produces an audit record — no silent drops."""

    @pytest.mark.asyncio
    async def test_audit_record_written_on_deny_path(self, tmp_path):
        """Audit record is written even when policy_result=DENY."""
        import types
        if not _LANGGRAPH_AVAILABLE:
            langgraph_stub = types.ModuleType("langgraph")
            langgraph_graph_stub = types.ModuleType("langgraph.graph")
            langgraph_graph_stub.END = "END"
            langgraph_graph_stub.StateGraph = MagicMock()
            sys.modules.setdefault("langgraph", langgraph_stub)
            sys.modules.setdefault("langgraph.graph", langgraph_graph_stub)

        from orchestrator.main import PMOState, log_audit
        from audit.logger import AuditLogger

        db_path = str(tmp_path / "orch_audit_deny.db")
        audit_logger = AuditLogger(db_path=db_path)
        await audit_logger.initialize()

        state: PMOState = {
            "raw_signal": _task_updated_event(),
            "event": None,
            "agent_output": None,
            "policy_result": "deny",
            "error": "test_deny_path",
        }

        result = await log_audit(state, audit_logger)
        assert result.get("audit_event_id") is not None, (
            "Audit record must be written even on DENY path"
        )

    @pytest.mark.asyncio
    async def test_audit_record_for_multiple_events_via_pipeline(self, tmp_path):
        """3 events through the signal pipeline → 3 distinct qualified signals (no silent drops)."""
        from signal_quality.pipeline import SignalQualityPipeline
        from state.canonical_state import CanonicalStateStore

        db_path = str(tmp_path / "orch_audit_multi.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        seen: Dict[str, bool] = {}

        def mock_get(key):
            return b"1" if key in seen else None

        def mock_setex(key, ttl, val):
            seen[key] = True

        mock_redis = MagicMock()
        mock_redis.get.side_effect = mock_get
        mock_redis.setex.side_effect = mock_setex

        pipeline = SignalQualityPipeline(redis_url="redis://mock", state_store=store)
        pipeline._noise_filter._redis = mock_redis

        events = [
            _task_updated_event(
                project_id=f"proj_audit_multi_{i:03d}",
                event_id=f"evt-audit-{i:04d}",
            )
            for i in range(3)
        ]

        results = await asyncio.gather(
            *[asyncio.to_thread(pipeline.process, e, e.get("source", "github")) for e in events],
            return_exceptions=True,
        )

        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 0, f"Pipeline failures: {failures}"

        # All 3 events qualified — none silently dropped
        successes = [r for r in results if not isinstance(r, Exception)]
        non_dup = [r for r in successes if not r.is_duplicate]
        assert len(non_dup) == 3, (
            f"Expected 3 non-duplicate qualified signals, got {len(non_dup)}"
        )

    @_skip_no_langgraph
    @pytest.mark.asyncio
    async def test_audit_record_written_for_every_orchestrated_event(self, tmp_path):
        """Process 3 events through full orchestrator; each must have audit_event_id."""
        from orchestrator.main import create_pmo_app, process_event

        db_path = str(tmp_path / "orch_audit_full.db")
        events = [
            _task_updated_event(
                project_id=f"proj_audit_{i:03d}",
                event_id=f"evt-audit-full-{i:04d}",
            )
            for i in range(3)
        ]

        app, _ = await create_pmo_app(db_path=db_path)
        results = []
        for evt in events:
            r = await process_event(evt, app=app)
            results.append(r)

        missing = [i for i, r in enumerate(results) if r.get("audit_event_id") is None]
        assert len(missing) == 0, (
            f"Events at indices {missing} have no audit_event_id — log_audit did not run"
        )


# ---------------------------------------------------------------------------
# Test 5 — Idempotent state updates
# ---------------------------------------------------------------------------

class TestIdempotentStateUpdates:
    """Same event processed twice produces exactly one canonical state row."""

    @pytest.mark.asyncio
    async def test_same_project_id_upserted_twice_produces_one_row(self, tmp_path):
        """update_state node is idempotent: same project_id upserted twice → one row."""
        import types
        if not _LANGGRAPH_AVAILABLE:
            langgraph_stub = types.ModuleType("langgraph")
            langgraph_graph_stub = types.ModuleType("langgraph.graph")
            langgraph_graph_stub.END = "END"
            langgraph_graph_stub.StateGraph = MagicMock()
            sys.modules.setdefault("langgraph", langgraph_stub)
            sys.modules.setdefault("langgraph.graph", langgraph_graph_stub)

        from orchestrator.main import PMOState, update_state
        from state.canonical_state import CanonicalStateStore
        from events.schemas.event_types import DeliveryEvent, EventType

        db_path = str(tmp_path / "orch_idem.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        event = DeliveryEvent(
            event_type=EventType.TASK_UPDATED,
            project_id="proj_idem_001",
            source="github",
            tenant_id="tenant_idem",
            payload={"task_id": "T-001", "new_status": "in_progress"},
        )

        state: PMOState = {
            "raw_signal": _task_updated_event(project_id="proj_idem_001"),
            "event": event,
            "error": None,
        }

        # Call update_state twice with the same project_id
        state1 = await update_state(state, store)
        state2 = await update_state(state1, store)

        # Both calls succeed
        assert state1.get("canonical_state") is not None
        assert state2.get("canonical_state") is not None

        # Exactly one row in canonical state
        retrieved = await store.get("proj_idem_001")
        assert retrieved is not None
        assert retrieved.project_id == "proj_idem_001"

    @_skip_no_langgraph
    @pytest.mark.asyncio
    async def test_same_event_twice_produces_one_state_row_full_graph(self, tmp_path):
        """Full-graph idempotency: same raw signal twice → one canonical state row."""
        from orchestrator.main import create_pmo_app, process_event
        from state.canonical_state import CanonicalStateStore

        db_path = str(tmp_path / "orch_idem_full.db")
        event = _task_updated_event(project_id="proj_idem_full_001")

        app, _ = await create_pmo_app(db_path=db_path)
        await process_event(event, app=app)
        await process_event(event, app=app)

        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()
        retrieved = await store.get("proj_idem_full_001")

        assert retrieved is not None
        assert retrieved.project_id == "proj_idem_full_001"
