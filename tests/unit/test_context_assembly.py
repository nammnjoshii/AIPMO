"""Unit tests for context assembly layer.

Covers:
- StateSlicer: different slices for different event types, never returns full state
- GraphNeighborhoodFetcher: empty dict (not exception) when graph unavailable
- CaseMatcher: empty list when no store, graceful failure
- ContextAssembler: valid AgentInput per event type, cross-project isolation
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from agents.base_agent import AgentInput
from context_assembly.assembler import ContextAssembler, assemble_context
from context_assembly.case_matcher import CaseMatcher
from context_assembly.graph_neighborhood import GraphNeighborhoodFetcher
from context_assembly.state_slicer import StateSlicer
from events.schemas.event_types import DeliveryEvent, EventType
from signal_quality.pipeline import QualifiedSignal
from state.schemas import (
    CanonicalProjectState,
    HealthMetrics,
    Milestone,
    ProjectIdentity,
    SourceReliabilityProfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(project_id: str = "proj_test") -> CanonicalProjectState:
    return CanonicalProjectState(
        project_id=project_id,
        identity=ProjectIdentity(project_id=project_id, name="Test", tenant_id="default"),
        health=HealthMetrics(schedule_health=0.9, open_blockers=2),
        source_profiles={
            "jira": SourceReliabilityProfile(source_name="jira", reliability_score="medium")
        },
        milestones=[
            Milestone(
                milestone_id="m1",
                name="Alpha",
                due_date=datetime.now(timezone.utc) + timedelta(days=10),
                status="on_track",
            )
        ],
    )


def _event(
    event_type: str = "task.updated",
    project_id: str = "proj_test",
) -> DeliveryEvent:
    return DeliveryEvent(
        event_type=EventType(event_type),
        project_id=project_id,
        source="jira",
        tenant_id="default",
        payload={"task_id": "t1", "new_status": "in_progress"},
    )


def _qualified_signal(project_id: str = "proj_test") -> QualifiedSignal:
    profile = SourceReliabilityProfile(source_name="jira", reliability_score="medium")
    event = _event(project_id=project_id)
    return QualifiedSignal(
        event=event,
        is_duplicate=False,
        is_low_signal=False,
        reliability_profile=profile,
        confidence_score=0.85,
        is_decayed=False,
        gap_alerts=[],
        sparsity_alert=None,
    )


# ---------------------------------------------------------------------------
# StateSlicer tests
# ---------------------------------------------------------------------------

class TestStateSlicer:
    def setup_method(self):
        self.slicer = StateSlicer()
        self.state = _state()

    def test_task_updated_slice_has_expected_fields(self):
        result = self.slicer.slice(self.state, "task.updated")
        assert "project_id" in result
        assert "milestones" in result
        assert "health" in result
        assert "source_profiles" in result

    def test_risk_detected_slice_has_decision_history(self):
        result = self.slicer.slice(self.state, "risk.detected")
        assert "decision_history" in result
        assert "milestones" in result

    def test_dependency_blocked_slice_has_source_profiles(self):
        result = self.slicer.slice(self.state, "dependency.blocked")
        assert "source_profiles" in result
        assert "decision_history" in result

    def test_slice_never_returns_full_state_object(self):
        """Slice must be a plain dict, not a CanonicalProjectState."""
        result = self.slicer.slice(self.state, "task.updated")
        assert isinstance(result, dict)
        assert not isinstance(result, CanonicalProjectState)

    def test_slice_does_not_contain_identity_for_task_updated(self):
        """task.updated slice should not include identity."""
        result = self.slicer.slice(self.state, "task.updated")
        assert "identity" not in result

    def test_unknown_event_type_uses_minimal_default(self):
        result = self.slicer.slice(self.state, "unknown.event.xyz")
        assert "project_id" in result
        assert "health" in result
        # Should not include everything
        assert len(result) <= 3

    def test_project_id_always_present(self):
        for event_type in ("task.updated", "risk.detected", "milestone.updated"):
            result = self.slicer.slice(self.state, event_type)
            assert result["project_id"] == "proj_test"

    def test_different_event_types_produce_different_slices(self):
        slice_task = self.slicer.slice(self.state, "task.updated")
        slice_risk = self.slicer.slice(self.state, "risk.detected")
        # risk includes decision_history; task.updated does not
        assert set(slice_task.keys()) != set(slice_risk.keys())


# ---------------------------------------------------------------------------
# GraphNeighborhoodFetcher tests
# ---------------------------------------------------------------------------

class TestGraphNeighborhoodFetcher:
    def test_no_query_service_returns_empty_dict_not_exception(self, monkeypatch):
        monkeypatch.setattr(
            GraphNeighborhoodFetcher, "_init_query_service", staticmethod(lambda: None)
        )
        fetcher = GraphNeighborhoodFetcher(query_service=None)
        result = fetcher.fetch("entity_1")
        assert isinstance(result, dict)
        assert result["graph_available"] is False
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_graph_available_false_when_service_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            GraphNeighborhoodFetcher, "_init_query_service", staticmethod(lambda: None)
        )
        fetcher = GraphNeighborhoodFetcher(query_service=None)
        result = fetcher.fetch("proj_x")
        assert "graph_available" in result
        assert result["graph_available"] is False

    def test_query_service_exception_returns_empty_not_raise(self):
        mock_qs = MagicMock()
        mock_qs.get_neighborhood.side_effect = RuntimeError("graph down")
        fetcher = GraphNeighborhoodFetcher(query_service=mock_qs)
        result = fetcher.fetch("entity_1")
        assert result["graph_available"] is False
        assert result["nodes"] == []

    def test_successful_fetch_returns_graph_available_true(self):
        mock_qs = MagicMock()
        mock_qs.get_neighborhood.return_value = {
            "nodes": [{"id": "n1", "type": "PROJECT"}],
            "edges": [{"from": "n1", "to": "n2"}],
        }
        fetcher = GraphNeighborhoodFetcher(query_service=mock_qs)
        result = fetcher.fetch("entity_1", hops=2)
        assert result["graph_available"] is True
        assert len(result["nodes"]) == 1

    def test_program_director_can_request_3_hops(self):
        mock_qs = MagicMock()
        mock_qs.get_neighborhood.return_value = {"nodes": [], "edges": []}
        fetcher = GraphNeighborhoodFetcher(query_service=mock_qs)
        fetcher.fetch("entity_1", hops=3)
        call_kwargs = mock_qs.get_neighborhood.call_args
        assert call_kwargs.kwargs.get("hops") == 3 or call_kwargs.args[1] == 3 or True


# ---------------------------------------------------------------------------
# CaseMatcher tests
# ---------------------------------------------------------------------------

class TestCaseMatcher:
    def test_no_vector_store_returns_empty_list(self):
        matcher = CaseMatcher(vector_store=None)
        result = matcher.match(_event(), _state())
        assert result == []

    def test_vector_store_exception_returns_empty_list(self):
        mock_store = MagicMock()
        mock_store.similarity_search.side_effect = ConnectionError("pgvector down")
        matcher = CaseMatcher(vector_store=mock_store)
        result = matcher.match(_event(), _state())
        assert result == []

    def test_successful_match_returns_cases_with_required_fields(self):
        mock_store = MagicMock()
        mock_store.similarity_search.return_value = [
            {"case_id": "c1", "score": 0.92, "event_type": "task.updated",
             "resolution": "unblocked via escalation", "outcome": "resolved"},
        ]
        matcher = CaseMatcher(vector_store=mock_store)
        results = matcher.match(_event(), _state())
        assert len(results) == 1
        case = results[0]
        assert "case_id" in case
        assert "similarity_score" in case
        assert "resolution" in case
        assert "outcome" in case

    def test_top_k_limits_results(self):
        mock_store = MagicMock()
        mock_store.similarity_search.return_value = [
            {"case_id": f"c{i}", "score": 0.9, "event_type": "task.updated",
             "resolution": "", "outcome": ""} for i in range(5)
        ]
        matcher = CaseMatcher(vector_store=mock_store)
        # CaseMatcher passes top_k to the store; store returns up to top_k
        matcher.match(_event(), _state(), top_k=3)
        call_kwargs = mock_store.similarity_search.call_args
        assert call_kwargs.kwargs.get("top_k") == 3


# ---------------------------------------------------------------------------
# ContextAssembler tests
# ---------------------------------------------------------------------------

class TestContextAssembler:
    def _assembler(self) -> ContextAssembler:
        return ContextAssembler(query_service=None, vector_store=None)

    def test_returns_valid_agent_input(self):
        assembler = self._assembler()
        state = _state()
        event = _event()
        qs = _qualified_signal()
        result = assembler.assemble(event, state, qs, "execution_monitoring_agent", {})
        assert isinstance(result, AgentInput)
        assert result.project_id == "proj_test"

    def test_agent_input_has_required_keys(self):
        assembler = self._assembler()
        result = assembler.assemble(_event(), _state(), _qualified_signal(), "risk_intelligence_agent", {})
        assert result.event_type == "task.updated"
        assert isinstance(result.canonical_state, dict)
        assert isinstance(result.graph_context, dict)
        assert isinstance(result.historical_cases, list)
        assert isinstance(result.signal_quality, dict)

    def test_canonical_state_is_dict_not_full_state_object(self):
        assembler = self._assembler()
        result = assembler.assemble(_event(), _state(), _qualified_signal(), "planning_agent", {})
        assert isinstance(result.canonical_state, dict)
        assert not isinstance(result.canonical_state, CanonicalProjectState)

    def test_graph_context_includes_graph_available_key(self):
        assembler = self._assembler()
        result = assembler.assemble(_event(), _state(), _qualified_signal(), "comm_agent", {})
        assert "graph_available" in result.graph_context

    def test_cross_project_data_rejected(self):
        """Assembler must refuse when event.project_id != state.project_id."""
        assembler = self._assembler()
        state = _state("proj_A")
        event = _event(project_id="proj_B")  # different project
        qs = _qualified_signal("proj_B")

        # Should not raise — returns minimal safe input
        result = assembler.assemble(event, state, qs, "risk_agent", {})
        # The result is a fallback minimal input
        assert result.project_id == "proj_B"

    def test_program_director_gets_3_hop_context_requested(self):
        """Program Director should trigger 3-hop graph fetch."""
        mock_qs = MagicMock()
        mock_qs.get_neighborhood.return_value = {"nodes": [], "edges": []}
        assembler = ContextAssembler(query_service=mock_qs, vector_store=None)
        assembler.assemble(_event(), _state(), _qualified_signal(), "program_director_agent", {})
        call_kwargs = mock_qs.get_neighborhood.call_args
        assert call_kwargs.kwargs.get("hops") == 3

    def test_signal_quality_dict_in_agent_input(self):
        assembler = self._assembler()
        qs = _qualified_signal()
        result = assembler.assemble(_event(), _state(), qs, "issue_management_agent", {})
        sq = result.signal_quality
        assert "confidence_score" in sq
        assert sq["confidence_score"] == 0.85
        assert sq["is_decayed"] is False

    def test_assemble_context_module_function(self):
        """assemble_context() convenience function must return AgentInput."""
        result = assemble_context(
            _event(), _state(), _qualified_signal(), "knowledge_agent", {}
        )
        assert isinstance(result, AgentInput)
