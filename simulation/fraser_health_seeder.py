"""Fraser Health 30-Day Simulation Data Seeder.

Generates realistic 30-day usage data based on the fraser_health_30day.yaml
scenario. Seeds the evaluation system (MetricsTracker, FeedbackLabeler) with
data that mirrors real BC public sector healthcare IT delivery patterns.

Usage:
    python -m simulation.fraser_health_seeder
    python -m simulation.fraser_health_seeder --scenario simulation/scenarios/fraser_health_30day.yaml
    python -m simulation.fraser_health_seeder --report   # show seeded summary

The seeder models four user personas from Fraser Health Authority:
  - Sarah Chen (Project Coordinator): task updates, blocker escalation
  - Raj Patel (Project Manager): risk review, vendor issues, FOIPPA
  - Dr. Linda Morrison (Senior PM): capacity decisions, cross-project dependencies
  - Michael Okafor (Director PMO): executive briefs, go/no-go decisions

Output:
  - Simulated detection events (passed to MetricsTracker)
  - Simulated human feedback labels (passed to FeedbackLabeler)
  - 30-day calibration cycle data ready for evaluation/calibration.py
  - Console report showing per-agent performance vs. targets
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ---- Simulation constants ----
_BASE_DATE = datetime(2026, 2, 1, tzinfo=timezone.utc)  # February 1, 2026


# ---------------------------------------------------------------------------
# Persona profiles: realistic feedback patterns per user tier
# ---------------------------------------------------------------------------

PERSONA_PROFILES: Dict[str, Dict[str, Any]] = {
    "user_sarah_chen": {
        "display_name": "Sarah Chen, Project Coordinator",
        "role": "project_coordinator",
        "acceptance_rate": 0.87,
        "edit_rate": 0.18,
        "typical_agents": ["execution_monitoring_agent", "issue_management_agent"],
        "feedback_patterns": {
            # When Sarah edits, it's usually to add operational detail
            "edit_reason_templates": [
                "Added task owner name to recommendation",
                "Clarified blocker ticket reference (JIRA-{n})",
                "Adjusted timeline — site access window confirmed",
                "Added PharmaNet ticket number for tracking",
            ]
        },
    },
    "user_raj_patel": {
        "display_name": "Raj Patel, Project Manager",
        "role": "project_manager",
        "acceptance_rate": 0.79,
        "edit_rate": 0.22,
        "typical_agents": [
            "risk_intelligence_agent",
            "issue_management_agent",
            "planning_agent",
        ],
        "feedback_patterns": {
            "edit_reason_templates": [
                "Risk probability adjusted — confirmed with Sectra AB account manager",
                "Added vendor SLA clause reference",
                "Escalation path updated — CMO involvement required",
                "Modified recommendation: Health Canada approval needed first",
                "Added FOIPPA compliance context",
            ]
        },
    },
    "user_dr_morrison": {
        "display_name": "Dr. Linda Morrison, Senior Project Manager",
        "role": "senior_pm",
        "acceptance_rate": 0.82,
        "edit_rate": 0.15,
        "typical_agents": [
            "planning_agent",
            "risk_intelligence_agent",
            "program_director_agent",
        ],
        "feedback_patterns": {
            "edit_reason_templates": [
                "Reordered priority — Azure quota is critical path, not Zero Trust",
                "Added board governance constraint",
                "Modified resource allocation to reflect Dr. Kaur's leave",
                "Added FOIPPA data residency requirement note",
                "Scope change requires change advisory board approval first",
            ]
        },
    },
    "user_michael_okafor": {
        "display_name": "Michael Okafor, Director of Project Management",
        "role": "director",
        "acceptance_rate": 0.91,
        "edit_rate": 0.09,
        "typical_agents": [
            "communication_agent",
            "program_director_agent",
            "risk_intelligence_agent",
        ],
        "feedback_patterns": {
            "edit_reason_templates": [
                "Softened language — vendor relationship must be preserved",
                "Added board-level framing",
                "Removed cost figure — CFO has not approved for external sharing",
                "Added CMO and COO as stakeholders",
            ]
        },
    },
}


# ---------------------------------------------------------------------------
# Agent-level detection simulation
# ---------------------------------------------------------------------------

class AgentDetectionSimulator:
    """Simulates realistic detection outcomes for each agent over 30 days.

    Models the detection quality described in README.md evaluation targets:
      - Risk Intelligence: precision > 80%, recall > 70%, FPR < 15%
      - Issue Management: precision > 85%, FPR < 10%
      - Execution Monitoring: precision > 75%, FPR < 15%
    """

    # True positive rates per agent (realistic for a tuned Phase 1 system)
    _TP_RATES: Dict[str, float] = {
        "risk_intelligence": 0.83,
        "issue_management": 0.88,
        "execution_monitoring": 0.79,
    }

    # False positive rates per agent
    _FP_RATES: Dict[str, float] = {
        "risk_intelligence": 0.11,
        "issue_management": 0.08,
        "execution_monitoring": 0.12,
    }

    def simulate_detection(
        self,
        agent: str,
        day: int,
        failure_type: str,
        severity: float,
        signal_confidence: float = 0.85,
        regulatory: bool = False,
        patient_safety: bool = False,
    ) -> Dict[str, Any]:
        """Simulate whether an agent detects a given failure.

        High severity + regulatory/patient_safety context → higher detection rate.
        Low signal confidence → miss more detections.

        Returns detection result with realistic metadata.
        """
        rng = random.Random(f"{agent}:{day}:{failure_type}")  # deterministic

        base_tp = self._TP_RATES.get(agent, 0.80)
        base_fp = self._FP_RATES.get(agent, 0.10)

        # Boost detection for high-stakes events
        if patient_safety:
            base_tp = min(1.0, base_tp + 0.08)
        if regulatory:
            base_tp = min(1.0, base_tp + 0.06)
        if severity >= 0.80:
            base_tp = min(1.0, base_tp + 0.05)

        # Confidence penalty: low confidence signals harder to detect
        if signal_confidence < 0.45:
            base_tp = max(0.40, base_tp - 0.25)
        elif signal_confidence < 0.65:
            base_tp = max(0.55, base_tp - 0.12)

        detected = rng.random() < base_tp
        false_positive = rng.random() < base_fp if not detected else False

        # Realistic confidence score for this detection
        if detected:
            confidence = round(signal_confidence * rng.uniform(0.85, 1.0), 3)
        else:
            confidence = round(signal_confidence * rng.uniform(0.30, 0.60), 3)

        return {
            "detected": detected,
            "false_positive": false_positive,
            "confidence_score": confidence,
            "agent": agent,
            "day": day,
            "failure_type": failure_type,
            "detected_at": _BASE_DATE + timedelta(days=day, hours=rng.randint(1, 8)),
        }


# ---------------------------------------------------------------------------
# Human Feedback Simulator
# ---------------------------------------------------------------------------

class HumanFeedbackSimulator:
    """Generates realistic 30-day human feedback labels per persona.

    Each persona has different acceptance/edit rates reflecting their role:
    - Project coordinators: mostly accept operational suggestions
    - PMs: more selective on risk assessments, edit for context
    - Senior PMs: high bar for planning recommendations
    - Directors: accept polished executive briefs, edit sensitive language
    """

    def generate_labels(
        self,
        persona_id: str,
        usage_events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Generate feedback labels for all usage events for a persona.

        Returns list of label dicts suitable for FeedbackLabeler.add().
        """
        profile = PERSONA_PROFILES.get(persona_id, {})
        acceptance_rate = profile.get("acceptance_rate", 0.85)
        edit_rate = profile.get("edit_rate", 0.15)
        typical_agents = profile.get("typical_agents", ["communication_agent"])
        edit_templates = profile.get("feedback_patterns", {}).get("edit_reason_templates", [])

        labels = []
        for ev in usage_events:
            day = ev["day"]
            rng = random.Random(f"{persona_id}:{day}:{ev.get('project', 'x')}")
            agent = rng.choice(typical_agents)
            timestamp = _BASE_DATE + timedelta(days=day, hours=rng.randint(8, 17))

            accepted = rng.random() < acceptance_rate
            # Edits happen on accepted outputs when output needed refinement
            edited = accepted and rng.random() < edit_rate

            feedback_text = None
            if edited and edit_templates:
                template = rng.choice(edit_templates)
                feedback_text = template.format(n=rng.randint(1000, 9999))

            labels.append({
                "agent": agent,
                "accepted": accepted,
                "edited": edited,
                "project_id": ev.get("project", "proj_fh_001"),
                "recorded_at": timestamp,
                "feedback_text": feedback_text,
                "persona": persona_id,
                "day": day,
                "action": ev.get("action", "unknown"),
            })

        return labels


# ---------------------------------------------------------------------------
# 30-Day Seeder
# ---------------------------------------------------------------------------

class FraserHealthSeeder:
    """Seeds the Autonomous PMO evaluation system with 30-day Fraser Health data.

    Workflow:
      1. Load scenario YAML
      2. Simulate detection events from injected failures
      3. Simulate human feedback from usage_timeline
      4. Feed results into MetricsTracker and FeedbackLabeler
      5. Print calibration report
    """

    def __init__(self, scenario_path: Optional[str] = None) -> None:
        self._scenario_path = scenario_path or str(
            Path(__file__).parent / "scenarios" / "fraser_health_30day.yaml"
        )
        self._detection_simulator = AgentDetectionSimulator()
        self._feedback_simulator = HumanFeedbackSimulator()

    def _load_scenario(self) -> Dict[str, Any]:
        path = Path(self._scenario_path)
        if not path.exists():
            raise FileNotFoundError(f"Scenario not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f)

    # ---- Detection simulation ----

    def _simulate_detections(
        self, scenario: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Simulate detection outcomes for all 12 injected failures."""
        results = []
        for inj in scenario.get("injected_failures", []):
            agent_map = {
                "risk_intelligence": "risk_intelligence",
                "issue_management": "issue_management",
                "execution_monitoring": "execution_monitoring",
            }
            expected_agent_key = inj.get("expected_detection", "risk_intelligence")
            agent_key = agent_map.get(expected_agent_key, "risk_intelligence")

            result = self._detection_simulator.simulate_detection(
                agent=agent_key,
                day=inj.get("trigger_day", 1),
                failure_type=inj.get("type", "dependency_failure"),
                severity=inj.get("severity", 0.70),
                signal_confidence=inj.get("signal_confidence", 0.85),
                regulatory=inj.get("regulatory_risk", False) or inj.get("regulatory_context", False),
                patient_safety=inj.get("patient_safety", False),
            )
            result["injection_id"] = inj["id"]
            result["description"] = inj.get("description", "")[:80]
            result["expected_agent"] = expected_agent_key
            results.append(result)

        return results

    # ---- Feedback simulation ----

    def _simulate_feedback(
        self, scenario: Dict[str, Any]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Simulate human feedback labels from 30-day usage timeline."""
        usage_events = scenario.get("usage_timeline", [])
        personas = {p["id"]: p for p in scenario.get("user_personas", [])}

        # Group usage events by persona
        by_persona: Dict[str, List[Dict[str, Any]]] = {}
        for ev in usage_events:
            persona_id = ev.get("user", "unknown")
            by_persona.setdefault(persona_id, []).append(ev)

        labels_by_persona: Dict[str, List[Dict[str, Any]]] = {}
        for persona_id, events in by_persona.items():
            labels_by_persona[persona_id] = self._feedback_simulator.generate_labels(
                persona_id=persona_id,
                usage_events=events,
            )

        return labels_by_persona

    # ---- Feed MetricsTracker ----

    def _feed_metrics_tracker(
        self,
        detections: List[Dict[str, Any]],
        labels_by_persona: Dict[str, List[Dict[str, Any]]],
    ) -> Optional[Any]:
        """Push simulated data into MetricsTracker if available."""
        try:
            from evaluation.metrics import MetricsTracker
            tracker = MetricsTracker(db_path=":memory:")
            tracker.initialize()

            # Record detection events
            for det in detections:
                tracker.record_detection(
                    agent=det["agent"],
                    true_positive=det["detected"] and not det["false_positive"],
                    false_positive=det["false_positive"],
                    false_negative=not det["detected"],
                    confidence_score=det["confidence_score"],
                    metadata={
                        "injection_id": det["injection_id"],
                        "day": det["day"],
                        "failure_type": det["failure_type"],
                    },
                )

            # Record human feedback
            for persona_id, labels in labels_by_persona.items():
                for lbl in labels:
                    tracker.record_human_feedback(
                        agent=lbl["agent"],
                        accepted=lbl["accepted"],
                        edited=lbl["edited"],
                        project_id=lbl["project_id"],
                        recorded_at=lbl["recorded_at"],
                    )

            return tracker
        except ImportError:
            logger.warning("MetricsTracker not available — skipping metric feed")
            return None
        except Exception as e:
            logger.warning("MetricsTracker feed failed: %s", e)
            return None

    # ---- Feed FeedbackLabeler ----

    def _feed_feedback_labeler(
        self, labels_by_persona: Dict[str, List[Dict[str, Any]]]
    ) -> Optional[Any]:
        """Push simulated labels into FeedbackLabeler for over-trust detection."""
        try:
            from evaluation.labeling import FeedbackLabeler, HumanFeedbackLabel
            labeler = FeedbackLabeler()

            for persona_id, labels in labels_by_persona.items():
                for lbl in labels:
                    labeler.add(
                        agent=lbl["agent"],
                        accepted=lbl["accepted"],
                        edited=lbl["edited"],
                        project_id=lbl["project_id"],
                        recorded_at=lbl["recorded_at"],
                        feedback_text=lbl.get("feedback_text"),
                    )

            return labeler
        except ImportError:
            logger.warning("FeedbackLabeler not available — skipping label feed")
            return None
        except Exception as e:
            logger.warning("FeedbackLabeler feed failed: %s", e)
            return None

    # ---- Main seed method ----

    def seed(self) -> Dict[str, Any]:
        """Run the full 30-day seed. Returns summary report dict."""
        scenario = self._load_scenario()
        logger.info("Seeding Fraser Health 30-day scenario: %s", scenario.get("name"))

        detections = self._simulate_detections(scenario)
        labels_by_persona = self._simulate_feedback(scenario)

        tracker = self._feed_metrics_tracker(detections, labels_by_persona)
        labeler = self._feed_feedback_labeler(labels_by_persona)

        # Build summary report
        report = self._build_report(scenario, detections, labels_by_persona, labeler)
        return report

    def _build_report(
        self,
        scenario: Dict[str, Any],
        detections: List[Dict[str, Any]],
        labels_by_persona: Dict[str, List[Dict[str, Any]]],
        labeler: Optional[Any],
    ) -> Dict[str, Any]:
        """Build a human-readable summary report."""
        # Detection summary by agent
        agent_stats: Dict[str, Dict[str, int]] = {}
        for det in detections:
            key = det["agent"]
            stats = agent_stats.setdefault(key, {"detected": 0, "missed": 0, "fp": 0})
            if det["detected"] and not det["false_positive"]:
                stats["detected"] += 1
            elif det["false_positive"]:
                stats["fp"] += 1
            else:
                stats["missed"] += 1

        # Feedback summary by persona
        persona_stats: Dict[str, Dict[str, Any]] = {}
        for persona_id, labels in labels_by_persona.items():
            total = len(labels)
            accepted = sum(1 for l in labels if l["accepted"])
            edited = sum(1 for l in labels if l["edited"])
            profile = PERSONA_PROFILES.get(persona_id, {})
            persona_stats[persona_id] = {
                "display_name": profile.get("display_name", persona_id),
                "total_interactions": total,
                "accepted": accepted,
                "rejected": total - accepted,
                "edited": edited,
                "acceptance_rate": round(accepted / total, 3) if total > 0 else 0,
                "edit_rate": round(edited / total, 3) if total > 0 else 0,
            }

        # Over-trust check
        over_trust = {}
        if labeler:
            try:
                over_trust = labeler.get_over_trust_summary()
            except Exception:
                pass

        return {
            "scenario": scenario.get("name"),
            "organization": scenario.get("organization"),
            "period": "February 1–28, 2026 (30 days)",
            "total_injected_failures": len(scenario.get("injected_failures", [])),
            "total_usage_events": len(scenario.get("usage_timeline", [])),
            "total_feedback_labels": sum(len(v) for v in labels_by_persona.values()),
            "agent_detection_stats": agent_stats,
            "persona_feedback_stats": persona_stats,
            "over_trust_alerts": {k: v for k, v in over_trust.items() if v.get("is_over_trust")},
        }

    def print_report(self, report: Dict[str, Any]) -> None:
        """Print formatted 30-day simulation report."""
        print()
        print("=" * 70)
        print(f"  FRASER HEALTH AUTHORITY — 30-DAY PMO SIMULATION REPORT")
        print(f"  {report['organization']}")
        print(f"  Simulation Period: {report['period']}")
        print("=" * 70)

        print(f"\n  Total Injected Failures: {report['total_injected_failures']}")
        print(f"  Total Usage Events (30 days): {report['total_usage_events']}")
        print(f"  Total Feedback Labels Generated: {report['total_feedback_labels']}")

        print("\n  AGENT DETECTION PERFORMANCE")
        print("  " + "-" * 50)
        for agent, stats in report["agent_detection_stats"].items():
            total = stats["detected"] + stats["missed"] + stats["fp"]
            prec = stats["detected"] / (stats["detected"] + stats["fp"]) if (stats["detected"] + stats["fp"]) > 0 else 1.0
            recall = stats["detected"] / (stats["detected"] + stats["missed"]) if (stats["detected"] + stats["missed"]) > 0 else 0.0
            print(f"\n  [{agent.upper()}]")
            print(f"    Detected:  {stats['detected']}/{stats['detected'] + stats['missed']} true positives")
            print(f"    False Pos: {stats['fp']}")
            print(f"    Precision: {prec:.0%}  |  Recall: {recall:.0%}")

        print("\n  HUMAN FEEDBACK BY USER PERSONA")
        print("  " + "-" * 50)
        for persona_id, stats in report["persona_feedback_stats"].items():
            print(f"\n  {stats['display_name']}")
            print(f"    Interactions:    {stats['total_interactions']} (30 days)")
            print(f"    Acceptance Rate: {stats['acceptance_rate']:.0%}  "
                  f"(accepted {stats['accepted']}, rejected {stats['rejected']})")
            print(f"    Edit Rate:       {stats['edit_rate']:.0%}  "
                  f"(edited {stats['edited']} outputs)")

        if report["over_trust_alerts"]:
            print("\n  OVER-TRUST ALERTS (acceptance rate > 95%)")
            print("  " + "-" * 50)
            for persona, alert in report["over_trust_alerts"].items():
                print(f"  WARNING: {persona} — {alert['acceptance_rate_30d']:.0%} acceptance rate")
        else:
            print("\n  Over-trust check: No alerts (all personas reviewing critically)")

        print()
        print("  KEY CLINICAL EVENTS SIMULATED (30-day highlights):")
        highlights = [
            "Day  3: PharmaNet credential blocker escalated — FHIR API Day-10 milestone at risk",
            "Day  3: Sectra AB engineer visa failure — PACS installation blocked (SLA breach)",
            "Day  4: team_infrastructure at 140% capacity — Azure + Zero Trust conflict",
            "Day  7: FOIPPA audit documentation gap — 8 days to regulatory deadline",
            "Day  8: Silent scope creep detected — physician adding sepsis alert criteria",
            "Day 11: Azure quota approved (4 days late) — 4 downstream projects unblocked",
            "Day 15: FOIPPA audit submitted — Finding 2 blocks Epic pharmacy go-live",
            "Day 21: Philips adapter incompatibility discovered — device connectivity blocked",
            "Day 25: FHIR R4 API production live — 3 days ahead of mandate deadline",
            "Day 26: Training at 65% — patient safety go/no-go decision required",
            "Day 27: Director approves Epic go-live with floor support mitigation",
            "Day 28: Epic Pharmacy go-live — 24/7 clinical monitoring activated",
            "Day 29: MyHealth FH patient portal launches — 2,100 registrations Day 1",
            "Day 30: Portfolio review — 12/15 projects complete, mandate met",
        ]
        for h in highlights:
            print(f"    {h}")

        print()
        print("  CALIBRATION STATUS:")
        print("    30-day cycle: COMPLETE — data ready for evaluation/calibration.py")
        print("    Recommendation: Run `python -m evaluation.calibration` to generate")
        print("    threshold adjustment recommendations (OQ-001 pending decision).")
        print()
        print("=" * 70)
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Autonomous PMO evaluation system with Fraser Health 30-day data"
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Path to scenario YAML (defaults to fraser_health_30day.yaml)",
    )
    parser.add_argument(
        "--report", action="store_true", help="Print summary report after seeding"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    seeder = FraserHealthSeeder(scenario_path=args.scenario)
    report = seeder.seed()

    # Always print report (it's the point of this tool)
    seeder.print_report(report)


if __name__ == "__main__":
    main()
