"""Demo runner — T-064.

Loads task_blocked.json + canonical_state_demo.json, processes through
the full agent pipeline, and prints a formatted summary.

Usage:
    python -m examples.run_sample_event
    LLM_PROVIDER=mock python -m examples.run_sample_event

Must complete in < 120 seconds.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure we can import from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")

from agents.base_agent import AgentInput, PolicyAction
from context_assembly.assembler import assemble_context
from events.schemas.event_types import DeliveryEvent, EventType
from orchestrator.event_router import EventRouter
from signal_quality.pipeline import QualifiedSignal
from state.schemas import (
    CanonicalProjectState,
    HealthMetrics,
    Milestone,
    ProjectIdentity,
    SourceReliabilityProfile,
)


def load_sample_event() -> dict:
    path = Path(__file__).parent / "sample_events" / "task_blocked.json"
    with open(path) as f:
        return json.load(f)


def load_sample_state() -> dict:
    path = Path(__file__).parent / "sample_events" / "canonical_state_demo.json"
    with open(path) as f:
        return json.load(f)


def build_delivery_event(raw: dict) -> DeliveryEvent:
    return DeliveryEvent(
        event_type=EventType(raw["event_type"]),
        project_id=raw["project_id"],
        source=raw.get("source", "github_issues"),
        tenant_id=raw.get("tenant_id", "default"),
        payload=raw.get("payload", {}),
    )


def build_canonical_state(state_dict: dict) -> CanonicalProjectState:
    identity_d = state_dict.get("identity", {})
    health_d = state_dict.get("health", {})
    milestones_raw = state_dict.get("milestones", [])
    profiles_raw = state_dict.get("source_profiles", {})

    from datetime import datetime, timezone

    milestones = []
    for m in milestones_raw:
        due_str = m.get("due_date")
        if due_str:
            due = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        else:
            due = datetime.now(timezone.utc)
        milestones.append(Milestone(
            milestone_id=m["milestone_id"],
            name=m["name"],
            due_date=due,
            status=m.get("status", "on_track"),
            completion_percentage=m.get("completion_percentage", 0.0),
        ))

    source_profiles = {
        k: SourceReliabilityProfile(
            source_name=v["source_name"],
            reliability_score=v.get("reliability_score", "medium"),
            inaccuracy_count=v.get("inaccuracy_count", 0),
        )
        for k, v in profiles_raw.items()
    }

    return CanonicalProjectState(
        project_id=state_dict["project_id"],
        identity=ProjectIdentity(
            project_id=identity_d.get("project_id", state_dict["project_id"]),
            name=identity_d.get("name", state_dict["project_id"]),
            tenant_id=identity_d.get("tenant_id", "default"),
            owner=identity_d.get("owner"),
            program_id=identity_d.get("program_id"),
        ),
        health=HealthMetrics(
            schedule_health=health_d.get("schedule_health", 0.7),
            resource_health=health_d.get("resource_health", 0.8),
            scope_health=health_d.get("scope_health", 0.9),
            dependency_health=health_d.get("dependency_health", 0.6),
            overall_health=health_d.get("overall_health", 0.7),
            open_blockers=health_d.get("open_blockers", 0),
            tasks_completed=health_d.get("tasks_completed", 0),
            tasks_total=health_d.get("tasks_total", 0),
        ),
        milestones=milestones,
        source_profiles=source_profiles,
    )


def build_qualified_signal(event: DeliveryEvent, state: CanonicalProjectState) -> QualifiedSignal:
    profile = SourceReliabilityProfile(source_name=event.source, reliability_score="high")
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


def _sep(title: str = "") -> None:
    width = 60
    if title:
        print(f"\n{'─' * 3} {title} {'─' * (width - len(title) - 5)}")
    else:
        print("─" * width)


def run() -> None:
    start = time.monotonic()

    print("\n" + "=" * 60)
    print("  AUTONOMOUS PMO — Sample Event Demo")
    print("=" * 60)

    # 1. Load inputs
    _sep("1. Signal Quality")
    raw = load_sample_event()
    state_dict = load_sample_state()
    event = build_delivery_event(raw)
    canonical = build_canonical_state(state_dict)
    qs = build_qualified_signal(event, canonical)

    print(f"  Event:       {event.event_type} on {event.project_id}")
    print(f"  Source:      {event.source}")
    print(f"  Confidence:  {qs.confidence_score:.2f}")
    print(f"  Is decayed:  {qs.is_decayed}")
    print(f"  Duplicate:   {qs.is_duplicate}")

    # 2. Assemble context
    _sep("2. Context Assembly")
    agent_input = assemble_context(
        event, canonical, qs, "program_director_agent",
        {"generate_status_report": "allow", "escalate_issue": "approval_required"}
    )
    print(f"  project_id:  {agent_input.project_id}")
    print(f"  event_type:  {agent_input.event_type}")
    graph_avail = agent_input.graph_context.get("graph_available", False)
    print(f"  graph:       {'available' if graph_avail else 'unavailable (stub)'}")

    # 3. Route through agents
    _sep("3. Agent Pipeline")
    router = EventRouter()
    output = router.route(agent_input)
    print(f"  Final agent:       {output.agent_name}")
    print(f"  Decision type:     {output.decision_type.value}")
    print(f"  Policy action:     {output.policy_action.value}")
    print(f"  Confidence:        {output.confidence_score:.3f}")
    for ev in output.evidence[:3]:
        print(f"  Evidence:          {ev}")

    # 4. Communication brief
    _sep("4. Decision Brief")
    brief_title = output.extra.get("brief_title", "(no brief title)")
    brief_body = output.extra.get("body", "(no body)")
    bullets = output.extra.get("bullets", [])
    print(f"  Title: {brief_title}")
    print(f"  Body:  {brief_body[:120]}{'...' if len(brief_body) > 120 else ''}")
    for b in bullets[:3]:
        print(f"  • {b}")
    disclosure = output.extra.get("confidence_disclosure")
    if disclosure:
        print(f"  [DISCLOSURE] {disclosure}")

    # 5. Policy / escalation
    _sep("5. Policy Outcome")
    print(f"  Policy action: {output.policy_action.value}")
    if output.policy_action == PolicyAction.ESCALATE:
        print("  → Routed to human review queue")
    elif output.policy_action == PolicyAction.APPROVAL_REQUIRED:
        print("  → Awaiting PM approval")
    elif output.policy_action in (PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT):
        print("  → Executed (audit record would be written)")

    # 6. Timing
    elapsed = time.monotonic() - start
    _sep()
    print(f"  Completed in {elapsed:.2f}s (target < 120s)")
    if elapsed > 120:
        print("  WARNING: exceeded 120s target")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run()
