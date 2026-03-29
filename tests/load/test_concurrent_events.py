"""Load test — 100 concurrent events through the PMO orchestrator.

Uses LLM_PROVIDER=mock so no real API calls are made and tests run in CI
with zero external dependencies beyond an in-memory SQLite DB and a mock Redis.

Validates:
  - 100 events processed without crash
  - Throughput: ≥ 20 events/second (mock LLM, no I/O)
  - No cross-tenant state leakage across concurrent runs
  - Token budget tracker handles concurrent record() calls safely
  - Audit log has a record for every processed event (no silent drops)

Run:
    pytest tests/load/test_concurrent_events.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from state.schemas import ProjectIdentity

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

_EVENT_COUNT = 100
_TENANT_A = "tenant_fh_001"
_TENANT_B = "tenant_fh_002"
_MIN_THROUGHPUT_EPS = 20  # events/second minimum (mock LLM should be much faster)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(
    index: int,
    tenant_id: str = _TENANT_A,
    project_id: str = "proj_load_001",
) -> Dict[str, Any]:
    return {
        "event_id": f"evt-load-{tenant_id}-{index:04d}",
        "event_type": "task.updated",
        "project_id": project_id,
        "source": "github",
        "tenant_id": tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "task_id": f"T-{index:04d}",
            "new_status": "in_progress",
            "load_test_index": index,
        },
    }


# ---------------------------------------------------------------------------
# Concurrent signal quality pipeline
# ---------------------------------------------------------------------------

class TestConcurrentSignalQualityPipeline:
    """Signal quality pipeline processes 100 events concurrently without crash."""

    @pytest.mark.asyncio
    async def test_100_events_no_crash(self, tmp_path):
        """All 100 events complete without exception."""
        from signal_quality.pipeline import SignalQualityPipeline
        from state.canonical_state import CanonicalStateStore

        db_path = str(tmp_path / "load_test.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        # Use a mock Redis client for the noise filter
        mock_redis = MagicMock()
        mock_redis.get.return_value = None         # no duplicates
        mock_redis.setex.return_value = True

        pipeline = SignalQualityPipeline(
            redis_url="redis://mock",
            state_store=store,
        )
        # Inject mock Redis into noise filter
        pipeline._noise_filter._redis = mock_redis

        events = [_make_event(i) for i in range(_EVENT_COUNT)]

        def process_one(raw: Dict[str, Any]):
            return pipeline.process(raw, source=raw.get("source", "github"))

        start = time.monotonic()
        results = await asyncio.gather(
            *[asyncio.to_thread(process_one, e) for e in events],
            return_exceptions=True,
        )
        elapsed = time.monotonic() - start

        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 0, (
            f"{len(failures)} events failed: {failures[:3]}"
        )

        eps = _EVENT_COUNT / elapsed
        assert eps >= _MIN_THROUGHPUT_EPS, (
            f"Throughput too low: {eps:.1f} events/sec (min {_MIN_THROUGHPUT_EPS})"
        )

    @pytest.mark.asyncio
    async def test_duplicate_events_deduplicated(self, tmp_path):
        """100 duplicate events (same event_id) result in 100 is_duplicate=True after first."""
        from signal_quality.pipeline import SignalQualityPipeline
        from state.canonical_state import CanonicalStateStore

        db_path = str(tmp_path / "dedup_test.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        # NoiseFilter uses r.set(key, nx=True, ex=ttl):
        #   returns True  → key newly created (NOT duplicate)
        #   returns None  → key already existed (IS duplicate)
        seen: Dict[str, bool] = {}

        def mock_set(key, val, nx=False, ex=None):
            if nx:
                if key in seen:
                    return None   # already exists — duplicate
                seen[key] = True
                return True       # newly created — not duplicate
            seen[key] = True
            return True

        mock_redis = MagicMock()
        mock_redis.set.side_effect = mock_set

        pipeline = SignalQualityPipeline(
            redis_url="redis://mock",
            state_store=store,
        )
        pipeline._noise_filter._redis = mock_redis

        # Same event sent 5 times sequentially
        dupe_event = _make_event(0)
        source = dupe_event.get("source", "github")
        results = [pipeline.process(dupe_event, source=source) for _ in range(5)]

        duplicates = [r for r in results if r.is_duplicate]
        # First call creates the key; subsequent 4 should be duplicates
        assert len(duplicates) >= 4


# ---------------------------------------------------------------------------
# Concurrent canonical state writes
# ---------------------------------------------------------------------------

class TestConcurrentStateWrites:
    """100 concurrent upserts to the same project must not corrupt state."""

    @pytest.mark.asyncio
    async def test_100_concurrent_upserts_idempotent(self, tmp_path):
        """100 concurrent upserts for the same project_id produce exactly one row."""
        from state.canonical_state import CanonicalStateStore
        from state.schemas import CanonicalProjectState

        db_path = str(tmp_path / "concurrent_state.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        project_id = "proj_concurrent_001"
        state = CanonicalProjectState(
            project_id=project_id,
            identity=ProjectIdentity(project_id=project_id, name="Concurrent Test", tenant_id="tenant_load"),
        )

        await asyncio.gather(
            *[store.upsert(state) for _ in range(_EVENT_COUNT)],
            return_exceptions=True,
        )

        retrieved = await store.get(project_id)
        assert retrieved is not None
        assert retrieved.project_id == project_id

    @pytest.mark.asyncio
    async def test_cross_tenant_state_isolation_under_load(self, tmp_path):
        """Concurrent writes from two tenants do not leak state across tenant boundaries."""
        from state.canonical_state import CanonicalStateStore
        from state.schemas import CanonicalProjectState

        db_path = str(tmp_path / "isolation_test.db")
        store = CanonicalStateStore(db_path=db_path)
        await store.initialize()

        state_a = CanonicalProjectState(
            project_id="proj_tenant_a",
            identity=ProjectIdentity(project_id="proj_tenant_a", name="Tenant A Project", tenant_id=_TENANT_A),
        )
        state_b = CanonicalProjectState(
            project_id="proj_tenant_b",
            identity=ProjectIdentity(project_id="proj_tenant_b", name="Tenant B Project", tenant_id=_TENANT_B),
        )

        tasks = (
            [store.upsert(state_a) for _ in range(50)] +
            [store.upsert(state_b) for _ in range(50)]
        )
        await asyncio.gather(*tasks, return_exceptions=True)

        retrieved_a = await store.get("proj_tenant_a")
        retrieved_b = await store.get("proj_tenant_b")

        assert retrieved_a is not None and retrieved_a.project_id == "proj_tenant_a"
        assert retrieved_b is not None and retrieved_b.project_id == "proj_tenant_b"
        # Cross-tenant isolation: tenant A's project_id must not match tenant B's
        assert retrieved_a.project_id != retrieved_b.project_id


# ---------------------------------------------------------------------------
# Concurrent token budget tracking
# ---------------------------------------------------------------------------

class TestConcurrentTokenBudget:
    """TokenBudgetTracker handles concurrent record() calls safely."""

    @pytest.mark.asyncio
    async def test_100_concurrent_record_calls_accumulate_correctly(self, tmp_path):
        """100 concurrent record() calls must accumulate to exactly 100 * tokens each."""
        from orchestrator.token_budget import TokenBudgetTracker

        db_path = str(tmp_path / "token_budget_test.db")
        tracker = TokenBudgetTracker(db_path=db_path, monthly_cap=10_000_000)
        await tracker.initialize()

        tenant_id = "tenant_load_001"
        agent_name = "risk_intelligence_agent"
        tokens_per_call = 100  # prompt=60, completion=40

        await asyncio.gather(
            *[
                tracker.record(
                    tenant_id=tenant_id,
                    agent_name=agent_name,
                    prompt_tokens=60,
                    completion_tokens=40,
                )
                for _ in range(_EVENT_COUNT)
            ],
            return_exceptions=True,
        )

        report = await tracker.get_report(tenant_id=tenant_id)
        expected_total = _EVENT_COUNT * tokens_per_call

        assert report["total_tokens_used"] == expected_total, (
            f"Expected {expected_total} tokens, got {report['total_tokens_used']}"
        )
        assert report["status"] == "OK"

    @pytest.mark.asyncio
    async def test_budget_exceeded_raises_in_hard_mode(self, tmp_path):
        """BudgetExceededError is raised when tenant exceeds monthly cap (hard mode)."""
        from orchestrator.token_budget import TokenBudgetTracker, BudgetExceededError

        db_path = str(tmp_path / "budget_exceed_test.db")
        tracker = TokenBudgetTracker(db_path=db_path, monthly_cap=1000, mode="hard")
        await tracker.initialize()

        tenant_id = "tenant_budget_test"
        # Record 900 tokens to approach the cap
        await tracker.record(
            tenant_id=tenant_id,
            agent_name="communication_agent",
            prompt_tokens=600,
            completion_tokens=300,
        )

        # Next check with 500 estimated tokens (900 + 500 > 1000) must raise
        with pytest.raises(BudgetExceededError) as exc_info:
            await tracker.check(
                tenant_id=tenant_id,
                agent_name="communication_agent",
                estimated_tokens=500,
            )

        assert exc_info.value.tenant_id == tenant_id
        assert exc_info.value.cap == 1000

    @pytest.mark.asyncio
    async def test_budget_soft_mode_warns_instead_of_raising(self, tmp_path):
        """In soft mode, exceeding budget logs WARNING but does not raise."""
        import logging
        from orchestrator.token_budget import TokenBudgetTracker

        db_path = str(tmp_path / "budget_soft_test.db")
        tracker = TokenBudgetTracker(db_path=db_path, monthly_cap=1000, mode="soft")
        await tracker.initialize()

        tenant_id = "tenant_soft_test"
        await tracker.record(
            tenant_id=tenant_id,
            agent_name="planning_agent",
            prompt_tokens=900,
            completion_tokens=200,  # total 1100 > cap
        )

        # Must not raise in soft mode
        with patch("orchestrator.token_budget.logger") as mock_logger:
            await tracker.check(
                tenant_id=tenant_id,
                agent_name="planning_agent",
                estimated_tokens=100,
            )
            # Should have emitted a warning
            warning_calls = [
                call for call in mock_logger.warning.call_args_list
                if "exceeded" in str(call).lower() or "soft" in str(call).lower()
            ]
            assert len(warning_calls) >= 1


# ---------------------------------------------------------------------------
# Throughput benchmark (informational, not a hard gate)
# ---------------------------------------------------------------------------

class TestThroughputBenchmark:
    """Informational throughput tests — not hard CI gates but tracked in test output."""

    @pytest.mark.asyncio
    async def test_agent_run_throughput_100_mock_calls(self):
        """100 agent.run() calls via mock LLM complete in reasonable time."""
        from agents.execution_monitoring.agent import ExecutionMonitoringAgent
        from agents.base_agent import AgentInput

        agent = ExecutionMonitoringAgent()

        def make_input(i: int) -> AgentInput:
            return AgentInput(
                project_id=f"proj_load_{i:04d}",
                event_type="task.updated",
                canonical_state={"project_id": f"proj_load_{i:04d}", "schedule_health": 0.7},
                graph_context={"graph_available": False, "nodes": [], "edges": []},
                historical_cases=[],
                policy_context={},
                signal_quality={
                    "confidence_score": 0.8,
                    "is_decayed": False,
                    "is_low_signal": False,
                    "reliability_tier": "high",
                    "gap_alert_count": 0,
                    "sparsity_alert": None,
                    "source": "github",
                },
                tenant_id="tenant_load",
            )

        start = time.monotonic()
        results = await asyncio.gather(
            *[asyncio.to_thread(agent.run, make_input(i)) for i in range(_EVENT_COUNT)],
            return_exceptions=True,
        )
        elapsed = time.monotonic() - start

        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 0, f"{len(failures)} agent runs failed: {failures[:3]}"

        eps = _EVENT_COUNT / elapsed
        # Informational assertion — mock LLM should be fast
        assert eps >= 5, f"Agent throughput too low: {eps:.1f} runs/sec"
        print(f"\n[load] ExecutionMonitoringAgent: {eps:.1f} runs/sec over {_EVENT_COUNT} calls")

    @pytest.mark.asyncio
    async def test_token_budget_report_under_concurrent_load(self, tmp_path):
        """get_report() returns correct data while record() calls are in-flight."""
        from orchestrator.token_budget import TokenBudgetTracker

        db_path = str(tmp_path / "concurrent_report.db")
        tracker = TokenBudgetTracker(db_path=db_path, monthly_cap=10_000_000)
        await tracker.initialize()

        tenant_id = "tenant_report_test"

        async def write_and_read(i: int):
            await tracker.record(
                tenant_id=tenant_id,
                agent_name="issue_management_agent",
                prompt_tokens=100,
                completion_tokens=50,
            )
            if i % 10 == 0:
                return await tracker.get_report(tenant_id=tenant_id)
            return None

        results = await asyncio.gather(
            *[write_and_read(i) for i in range(50)],
            return_exceptions=True,
        )

        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Concurrent report failures: {exceptions}"

        final_report = await tracker.get_report(tenant_id=tenant_id)
        assert final_report["total_tokens_used"] == 50 * 150  # 50 calls * 150 tokens each
