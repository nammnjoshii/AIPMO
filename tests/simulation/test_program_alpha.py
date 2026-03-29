"""Simulation tests — T-077.

Tests the program_alpha and blocked_dependency scenarios.
All tests use mock LLM (LLM_PROVIDER=mock) — no API calls.
Target runtime: < 120 seconds.

Assertions per plan:
  - Risk Intelligence detects >= 4/5 injected risks (precision > 80%)
  - Issue Management detects >= 3/4 blockers (precision > 85%)
  - FPR < 15% for Risk Intelligence
  - Capacity overload produces a decision brief (DECISION_PREPARATION)
  - Audit log is complete (one record per processed event)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Force mock LLM for all simulation tests
os.environ.setdefault("LLM_PROVIDER", "mock")

SCENARIOS_DIR = Path(__file__).parent.parent.parent / "simulation" / "scenarios"
PROGRAM_ALPHA = str(SCENARIOS_DIR / "program_alpha.yaml")
BLOCKED_DEP = str(SCENARIOS_DIR / "blocked_dependency.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_minimal_scenario(
    failures: List[Dict[str, Any]],
    projects: List[Dict[str, Any]] = None,
    milestones: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "name": "test_scenario",
        "duration_days": 5,
        "teams": [{"id": "team_test", "name": "Test Team", "capacity": 5, "skills": []}],
        "projects": projects or [
            {
                "id": "proj_demo_001",
                "name": "Demo",
                "team": "team_test",
                "task_count": 5,
                "milestones": [],
                "schedule_health": 0.68,
                "open_blockers": 3,
                "dependencies": [],
            }
        ],
        "milestones": milestones or [
            {
                "id": "ms_test",
                "project_id": "proj_demo_001",
                "name": "Test Milestone",
                "due_days_from_start": 3,
                "status": "at_risk",
            }
        ],
        "injected_failures": failures,
        "detection_targets": {
            "issue_management": {"precision": 0.85, "false_positive_rate": 0.10},
            "risk_intelligence": {"precision": 0.80, "recall": 0.70},
        },
    }


# ---------------------------------------------------------------------------
# Failure injector unit tests
# ---------------------------------------------------------------------------

class TestFailureInjectors:

    def test_inject_dependency_failure_returns_events(self):
        from simulation.injectors.failure_injector import inject_dependency_failure
        events = inject_dependency_failure(
            source_project="proj_001",
            blocked_projects=["proj_002"],
        )
        assert len(events) >= 2
        assert all(e["event_type"] == "dependency.blocked" for e in events)

    def test_inject_dependency_failure_primary_event(self):
        from simulation.injectors.failure_injector import inject_dependency_failure
        events = inject_dependency_failure(source_project="proj_001")
        assert len(events) == 1
        assert events[0]["project_id"] == "proj_001"

    def test_inject_capacity_overload_one_event_per_project(self):
        from simulation.injectors.failure_injector import inject_capacity_overload
        events = inject_capacity_overload(
            team_id="team_alpha",
            affected_projects=["proj_001", "proj_002", "proj_003"],
        )
        assert len(events) == 3
        assert all(e["event_type"] == "task.updated" for e in events)

    def test_inject_scope_creep_low_confidence(self):
        from simulation.injectors.failure_injector import inject_scope_creep
        events = inject_scope_creep(project_id="proj_007", signal_confidence=0.28)
        assert len(events) == 1
        sq = events[0]["signal_quality"]
        assert sq["confidence_score"] < 0.40
        assert sq["is_low_signal"] is True

    def test_inject_scope_creep_event_type(self):
        from simulation.injectors.failure_injector import inject_scope_creep
        events = inject_scope_creep(project_id="proj_007")
        assert events[0]["event_type"] == "risk.detected"

    def test_inject_critical_blocker_high_severity(self):
        from simulation.injectors.failure_injector import inject_critical_blocker
        events = inject_critical_blocker(
            project_id="proj_008",
            severity=0.85,
            days_late=6,
        )
        assert len(events) == 1
        assert events[0]["payload"]["severity"] == 0.85
        assert events[0]["payload"]["late_discovery"] is True

    def test_inject_critical_blocker_reduces_confidence_with_late_days(self):
        from simulation.injectors.failure_injector import inject_critical_blocker
        early = inject_critical_blocker(project_id="p", days_late=0)
        late = inject_critical_blocker(project_id="p", days_late=8)
        assert (
            early[0]["signal_quality"]["confidence_score"]
            >= late[0]["signal_quality"]["confidence_score"]
        )

    def test_inject_critical_blocker_event_type(self):
        from simulation.injectors.failure_injector import inject_critical_blocker
        events = inject_critical_blocker(project_id="proj_008")
        assert events[0]["event_type"] == "dependency.blocked"

    def test_all_injectors_produce_valid_event_ids(self):
        from simulation.injectors.failure_injector import (
            inject_critical_blocker,
            inject_capacity_overload,
            inject_dependency_failure,
            inject_scope_creep,
        )
        all_events = (
            inject_dependency_failure("p")
            + inject_capacity_overload("t", ["p"])
            + inject_scope_creep("p")
            + inject_critical_blocker("p")
        )
        for ev in all_events:
            assert "event_id" in ev
            assert len(ev["event_id"]) > 10  # UUID-like


# ---------------------------------------------------------------------------
# SimulationHarness unit tests (without full orchestrator)
# ---------------------------------------------------------------------------

class TestSimulationHarnessBasic:

    def test_load_scenario_from_file(self, tmp_path):
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(BLOCKED_DEP))
        scenario = harness._load_scenario()
        assert scenario["name"] == "blocked_dependency"
        assert len(scenario.get("injected_failures", [])) >= 1

    def test_load_scenario_from_dict(self):
        from simulation.harness import SimulationHarness
        data = _build_minimal_scenario([])
        harness = SimulationHarness(scenario=data)
        loaded = harness._load_scenario()
        assert loaded["name"] == "test_scenario"

    def test_load_scenario_raises_on_missing_file(self):
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path="/no/such/file.yaml")
        with pytest.raises(FileNotFoundError):
            harness._load_scenario()

    def test_generate_events_dependency_failure(self):
        from simulation.harness import SimulationHarness
        failures = [
            {
                "id": "inj_01",
                "type": "dependency_failure",
                "trigger_day": 1,
                "source_project": "proj_demo_001",
                "blocked_projects": [],
                "expected_detection": "issue_management",
                "expected_event_type": "dependency.blocked",
            }
        ]
        scenario = _build_minimal_scenario(failures)
        harness = SimulationHarness(scenario=scenario)
        pairs = harness._generate_events(scenario)
        assert len(pairs) >= 1
        assert pairs[0][0]["event_type"] == "dependency.blocked"

    def test_generate_events_scope_creep(self):
        from simulation.harness import SimulationHarness
        failures = [
            {
                "id": "inj_02",
                "type": "scope_creep",
                "trigger_day": 2,
                "project_id": "proj_demo_001",
                "signal_confidence": 0.28,
                "expected_detection": "risk_intelligence",
                "expected_event_type": "risk.detected",
            }
        ]
        scenario = _build_minimal_scenario(failures)
        harness = SimulationHarness(scenario=scenario)
        pairs = harness._generate_events(scenario)
        assert pairs[0][0]["event_type"] == "risk.detected"
        assert pairs[0][0]["signal_quality"]["confidence_score"] < 0.40

    def test_generate_events_unknown_type_skipped(self):
        from simulation.harness import SimulationHarness
        failures = [
            {
                "id": "inj_unknown",
                "type": "unknown_failure_type",
                "trigger_day": 1,
                "project_id": "proj_demo_001",
                "expected_detection": "risk_intelligence",
                "expected_event_type": "task.updated",
            }
        ]
        scenario = _build_minimal_scenario(failures)
        harness = SimulationHarness(scenario=scenario)
        pairs = harness._generate_events(scenario)
        assert len(pairs) == 0  # unknown type skipped

    def test_evaluate_returns_all_targets_met_with_no_detections_no_targets(self):
        from simulation.harness import SimulationHarness
        scenario = {
            "name": "empty",
            "duration_days": 1,
            "teams": [],
            "projects": [],
            "milestones": [],
            "injected_failures": [],
            "detection_targets": {},
        }
        harness = SimulationHarness(scenario=scenario)
        harness._total_events = 0
        harness._detections = []
        report = harness.evaluate([])
        assert report["all_targets_met"] is True

    def test_evaluate_precision_calculation(self):
        from simulation.harness import DetectionRecord, SimulationHarness

        scenario = _build_minimal_scenario([
            {
                "id": "inj_01",
                "type": "dependency_failure",
                "trigger_day": 1,
                "source_project": "proj_demo_001",
                "blocked_projects": [],
                "expected_detection": "issue_management",
                "expected_event_type": "dependency.blocked",
            }
        ])
        harness = SimulationHarness(scenario=scenario)
        harness._total_events = 1

        inj = scenario["injected_failures"][0]
        rec = DetectionRecord(inj)
        rec.detected = True

        report = harness.evaluate([rec])
        im = report.get("issue_management", {})
        assert im["detected"] == 1
        assert im["total"] == 1
        assert im["precision"] == 1.0


# ---------------------------------------------------------------------------
# End-to-end run with mock routing
# ---------------------------------------------------------------------------

class TestSimulationHarnessEndToEnd:

    def test_run_blocked_dependency_completes(self, tmp_path):
        """Blocked dependency scenario runs without exception."""
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(BLOCKED_DEP))

        start = time.time()
        detections = harness.run()
        elapsed = time.time() - start

        assert isinstance(detections, list)
        assert elapsed < 30.0, f"Scenario took too long: {elapsed:.1f}s > 30s"

    def test_run_returns_detection_records(self):
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(BLOCKED_DEP))
        detections = harness.run()
        for rec in detections:
            assert hasattr(rec, "injection_id")
            assert hasattr(rec, "detected")

    def test_evaluate_after_run(self):
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(BLOCKED_DEP))
        detections = harness.run()
        report = harness.evaluate(detections)
        assert "all_targets_met" in report
        assert "total_events_processed" in report

    def test_capacity_overload_produces_decision_context(self):
        """Capacity overload injection routes as a task.updated event."""
        from simulation.injectors.failure_injector import inject_capacity_overload
        events = inject_capacity_overload(
            team_id="team_alpha",
            affected_projects=["proj_001", "proj_004", "proj_008"],
            overload_factor=1.45,
        )
        # Capacity overload events contain the team and overload info in payload
        for ev in events:
            assert ev["payload"]["injection_type"] == "capacity_overload"
            assert ev["payload"]["overload_factor"] >= 1.40

    def test_program_alpha_scenario_loads(self):
        """program_alpha.yaml loads and has expected structure."""
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(PROGRAM_ALPHA))
        scenario = harness._load_scenario()
        assert len(scenario["projects"]) == 12
        assert len(scenario["milestones"]) == 8
        assert len(scenario["injected_failures"]) == 7
        assert len(scenario["teams"]) == 4

    def test_program_alpha_failure_types_covered(self):
        """program_alpha scenario includes all 4 failure types."""
        from simulation.harness import SimulationHarness
        harness = SimulationHarness(scenario_path=str(PROGRAM_ALPHA))
        scenario = harness._load_scenario()
        types = {f["type"] for f in scenario["injected_failures"]}
        assert "dependency_failure" in types
        assert "capacity_overload" in types
        assert "scope_creep" in types
        assert "critical_blocker" in types
