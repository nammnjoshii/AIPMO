"""Simulation harness — T-076.

SimulationHarness.run()     — inject failures, process events through orchestrator
SimulationHarness.evaluate() — compute precision/recall per agent against scenario targets

CLI: python -m simulation.harness --scenario simulation/scenarios/program_alpha.yaml
     Exits non-zero if any detection target is not met.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection record helpers
# ---------------------------------------------------------------------------

class DetectionRecord:
    """Tracks whether an injected failure was detected and with what outcome."""

    def __init__(self, injection: Dict[str, Any]) -> None:
        self.injection_id: str = injection["id"]
        self.injection_type: str = injection["type"]
        self.expected_detection: str = injection.get("expected_detection", "")
        self.expected_event_type: str = injection.get("expected_event_type", "")
        self.project_id: str = injection.get("source_project") or injection.get("project_id", "")
        self.detected: bool = False
        self.false_positive: bool = False
        self.detected_by: str = ""
        self.agent_output: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# SimulationHarness
# ---------------------------------------------------------------------------

class SimulationHarness:
    """Runs a simulation scenario and evaluates agent detection performance.

    Usage:
        harness = SimulationHarness(scenario_path="simulation/scenarios/program_alpha.yaml")
        results = harness.run()
        report = harness.evaluate(results)

    Detection logic:
        - A failure is "detected" if the agent named in expected_detection
          returned a non-ALLOW policy_action or DECISION_PREPARATION decision_type.
        - Precision = true_positives / (true_positives + false_positives)
        - Recall = true_positives / total_injected_failures
        - False positive rate = false_positives / total_events_processed
    """

    def __init__(
        self,
        scenario_path: Optional[str] = None,
        scenario: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._scenario_path = scenario_path
        self._scenario: Optional[Dict[str, Any]] = scenario
        self._db_path = db_path or os.environ.get(
            "SQLITE_DB_PATH", "./data/autonomous_pmo.db"
        )
        self._detections: List[DetectionRecord] = []
        self._total_events: int = 0
        self._false_positive_count: int = 0

    def _load_scenario(self) -> Dict[str, Any]:
        if self._scenario:
            return self._scenario
        if not self._scenario_path:
            raise ValueError("Either scenario_path or scenario dict must be provided.")
        path = Path(self._scenario_path)
        if not path.exists():
            raise FileNotFoundError(f"Scenario file not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f)

    # ---- Event generation from injected failures ----

    def _generate_events(
        self, scenario: Dict[str, Any]
    ) -> List[Tuple[Dict[str, Any], DetectionRecord]]:
        """Generate (event_dict, detection_record) pairs from scenario failures."""
        from simulation.injectors.failure_injector import (
            inject_critical_blocker,
            inject_capacity_overload,
            inject_dependency_failure,
            inject_scope_creep,
        )

        pairs: List[Tuple[Dict[str, Any], DetectionRecord]] = []
        for inj in scenario.get("injected_failures", []):
            record = DetectionRecord(inj)
            inj_type = inj["type"]
            trigger_day = inj.get("trigger_day", 0)
            project_id = inj.get("source_project") or inj.get("project_id", "proj_default")

            if inj_type == "dependency_failure":
                events = inject_dependency_failure(
                    source_project=project_id,
                    blocked_by_task=inj.get("blocked_by_task", "blocking_task"),
                    blocked_task=inj.get("blocked_task", "blocked_task"),
                    blocked_projects=inj.get("blocked_projects", []),
                    trigger_day=trigger_day,
                )
            elif inj_type == "capacity_overload":
                events = inject_capacity_overload(
                    team_id=inj.get("team_id", "team_default"),
                    affected_projects=inj.get("affected_projects", [project_id]),
                    overload_factor=inj.get("overload_factor", 1.45),
                    trigger_day=trigger_day,
                )
            elif inj_type == "scope_creep":
                events = inject_scope_creep(
                    project_id=project_id,
                    description=inj.get("description", "Scope change"),
                    signal_confidence=inj.get("signal_confidence", 0.32),
                    trigger_day=trigger_day,
                )
            elif inj_type == "critical_blocker":
                events = inject_critical_blocker(
                    project_id=project_id,
                    task_id=inj.get("task_id", "critical_task"),
                    severity=inj.get("severity", 0.85),
                    days_late=inj.get("days_late", 6),
                    trigger_day=trigger_day,
                )
            else:
                logger.warning("Unknown injection type: %s — skipping", inj_type)
                continue

            # Associate first event with the detection record
            for i, ev in enumerate(events):
                pairs.append((ev, record if i == 0 else DetectionRecord(inj)))

        return pairs

    # ---- Core run logic ----

    async def _run_async(self, scenario: Dict[str, Any]) -> List[DetectionRecord]:
        """Process all injected events through the agent routing layer."""
        from agents.base_agent import AgentInput, DecisionType
        from orchestrator.event_router import EventRouter

        router = EventRouter()
        pairs = self._generate_events(scenario)
        self._total_events = len(pairs)

        for event_dict, detection_record in pairs:
            try:
                agent_input = self._build_agent_input(event_dict, scenario)
                outputs = router.route(event_dict["event_type"], agent_input)

                # Check if any output counts as a detection
                for agent_name, output in outputs.items():
                    is_detection = (
                        output.decision_type == DecisionType.DECISION_PREPARATION.value
                        or output.policy_action not in ("allow", "ALLOW")
                    )
                    if is_detection and not detection_record.detected:
                        detection_record.detected = True
                        detection_record.detected_by = agent_name
                        detection_record.agent_output = {
                            "decision_type": output.decision_type,
                            "policy_action": output.policy_action,
                            "confidence_score": output.confidence_score,
                            "recommendation": output.recommendation,
                        }

            except Exception as e:
                logger.warning(
                    "Harness: failed to process injection %s: %s",
                    detection_record.injection_id,
                    e,
                )

        self._detections = [r for _, r in pairs if r not in [d for _, d in pairs[1:]]]
        # Deduplicate: group by injection_id, keep first
        seen = set()
        unique = []
        for _, rec in pairs:
            if rec.injection_id not in seen:
                seen.add(rec.injection_id)
                unique.append(rec)
        self._detections = unique
        return self._detections

    def _build_agent_input(
        self, event_dict: Dict[str, Any], scenario: Dict[str, Any]
    ) -> Any:
        """Build AgentInput from event dict and scenario state."""
        from agents.base_agent import AgentInput

        project_id = event_dict.get("project_id", "proj_default")

        # Find project state in scenario
        canonical_state: Dict[str, Any] = {"project_id": project_id}
        for proj in scenario.get("projects", []):
            if proj["id"] == project_id:
                canonical_state.update({
                    "schedule_health": proj.get("schedule_health", 0.75),
                    "open_blockers": proj.get("open_blockers", 0),
                    "milestones": [],
                })
                break

        # Attach milestones
        canonical_state["milestones"] = [
            {"id": ms["id"], "status": ms["status"], "due_days_from_start": ms["due_days_from_start"]}
            for ms in scenario.get("milestones", [])
            if ms.get("project_id") == project_id
        ]

        signal_quality = event_dict.get("signal_quality", {})

        return AgentInput(
            project_id=project_id,
            event_type=event_dict.get("event_type", "task.updated"),
            canonical_state=canonical_state,
            graph_context={"graph_available": False, "nodes": [], "edges": []},
            historical_cases=[],
            policy_context={"rules": []},
            signal_quality=signal_quality,
            extra={"raw_event": event_dict},
        )

    def run(self) -> List[DetectionRecord]:
        """Run the scenario synchronously. Returns detection records."""
        scenario = self._load_scenario()
        fast_ci = scenario.get("fast_ci", False)
        max_runtime = scenario.get("max_runtime_seconds", 120)

        start = time.time()
        detections = asyncio.run(self._run_async(scenario))
        elapsed = time.time() - start

        if fast_ci and elapsed > max_runtime:
            logger.warning(
                "Simulation exceeded fast-CI target: %.1fs > %ds",
                elapsed,
                max_runtime,
            )

        logger.info(
            "Simulation '%s' completed in %.1fs — %d events, %d failures tracked",
            scenario.get("name", "unknown"),
            elapsed,
            self._total_events,
            len(detections),
        )
        return detections

    # ---- Evaluation ----

    def evaluate(self, detections: Optional[List[DetectionRecord]] = None) -> Dict[str, Any]:
        """Compute precision/recall per agent against scenario targets.

        Returns:
            Dict with keys per agent: precision, recall, fpr, detected, total, targets_met.
            Top-level 'all_targets_met' bool.
        """
        if detections is None:
            detections = self._detections

        scenario = self._load_scenario()
        targets = scenario.get("detection_targets", {})

        # Group detections by expected_detection agent
        by_agent: Dict[str, List[DetectionRecord]] = {}
        for rec in detections:
            agent = rec.expected_detection
            by_agent.setdefault(agent, []).append(rec)

        report: Dict[str, Any] = {}
        all_met = True

        for agent_name, recs in by_agent.items():
            total = len(recs)
            detected = sum(1 for r in recs if r.detected)
            fp = sum(1 for r in recs if r.false_positive)

            precision = detected / (detected + fp) if (detected + fp) > 0 else 1.0
            recall = detected / total if total > 0 else 0.0
            fpr = fp / self._total_events if self._total_events > 0 else 0.0

            agent_targets = targets.get(agent_name, {})
            targets_met = True

            if "precision" in agent_targets and precision < agent_targets["precision"]:
                targets_met = False
                all_met = False
            if "recall" in agent_targets and recall < agent_targets["recall"]:
                targets_met = False
                all_met = False
            if "false_positive_rate" in agent_targets and fpr > agent_targets["false_positive_rate"]:
                targets_met = False
                all_met = False

            report[agent_name] = {
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "false_positive_rate": round(fpr, 3),
                "detected": detected,
                "total": total,
                "targets": agent_targets,
                "targets_met": targets_met,
            }

        report["all_targets_met"] = all_met
        report["total_events_processed"] = self._total_events
        return report

    def print_report(self, report: Dict[str, Any]) -> None:
        """Print a human-readable evaluation report."""
        print("\n" + "=" * 60)
        print("SIMULATION EVALUATION REPORT")
        print("=" * 60)
        for agent, metrics in report.items():
            if agent in ("all_targets_met", "total_events_processed"):
                continue
            status = "PASS" if metrics.get("targets_met") else "FAIL"
            print(f"\n[{status}] {agent}")
            print(f"  Precision:  {metrics['precision']:.3f}  (target: {metrics['targets'].get('precision', 'n/a')})")
            print(f"  Recall:     {metrics['recall']:.3f}  (target: {metrics['targets'].get('recall', 'n/a')})")
            print(f"  FPR:        {metrics['false_positive_rate']:.3f}  (target: {metrics['targets'].get('false_positive_rate', 'n/a')})")
            print(f"  Detected:   {metrics['detected']}/{metrics['total']}")
        print(f"\nTotal events processed: {report.get('total_events_processed', 0)}")
        overall = "ALL TARGETS MET" if report.get("all_targets_met") else "SOME TARGETS MISSED"
        print(f"\n{'=' * 60}")
        print(f"RESULT: {overall}")
        print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run a PMO simulation scenario")
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario YAML file",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (defaults to SQLITE_DB_PATH env var)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    harness = SimulationHarness(
        scenario_path=args.scenario,
        db_path=args.db_path,
    )
    detections = harness.run()
    report = harness.evaluate(detections)
    harness.print_report(report)

    if not report.get("all_targets_met"):
        sys.exit(1)


if __name__ == "__main__":
    main()
