"""Failure injector — T-075.

4 injection methods:
  inject_dependency_failure()  — blocked task chain
  inject_capacity_overload()   — team over-allocated
  inject_scope_creep()         — silent low-confidence risk signal
  inject_critical_blocker()    — late-surfacing high-severity blocker

Each returns a list of timed DeliveryEvents.
Scope creep produces a silent signal with low confidence (< 0.40).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid


def _now(offset_days: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=offset_days)


def _make_event(
    event_type: str,
    project_id: str,
    payload: Dict[str, Any],
    source: str = "simulation",
    tenant_id: str = "default",
    signal_quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a raw event dict matching DeliveryEvent schema."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "project_id": project_id,
        "source": source,
        "tenant_id": tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "signal_quality": signal_quality or {
            "confidence_score": 0.85,
            "is_duplicate": False,
            "is_low_signal": False,
            "is_decayed": False,
            "sparsity_alert": None,
        },
    }


def inject_dependency_failure(
    source_project: str,
    blocked_by_task: str = "task_dependency",
    blocked_task: str = "task_blocked",
    blocked_projects: Optional[List[str]] = None,
    delay_days: int = 2,
    trigger_day: int = 0,
) -> List[Dict[str, Any]]:
    """Inject a dependency failure event chain.

    Produces one event per blocked project plus one for the source.

    Args:
        source_project: Project where the blocking task lives.
        blocked_by_task: Task ID that is blocking others.
        blocked_task: Task ID being blocked.
        blocked_projects: Additional projects also affected.
        delay_days: Expected delay in days.
        trigger_day: Offset from simulation start (for ordering).

    Returns:
        List of DeliveryEvent dicts.
    """
    events = []

    # Primary blocked event
    events.append(_make_event(
        event_type="dependency.blocked",
        project_id=source_project,
        payload={
            "task_id": blocked_task,
            "blocked_by": blocked_by_task,
            "new_status": "blocked",
            "delay_days": delay_days,
            "trigger_day": trigger_day,
            "injection_type": "dependency_failure",
        },
    ))

    # Secondary events for each additionally blocked project
    for proj in (blocked_projects or []):
        events.append(_make_event(
            event_type="dependency.blocked",
            project_id=proj,
            payload={
                "task_id": f"downstream_task_{proj}",
                "blocked_by": f"{source_project}:{blocked_by_task}",
                "new_status": "blocked",
                "delay_days": delay_days,
                "trigger_day": trigger_day,
                "injection_type": "dependency_failure",
                "upstream_project": source_project,
            },
        ))

    return events


def inject_capacity_overload(
    team_id: str,
    affected_projects: List[str],
    overload_factor: float = 1.45,
    trigger_day: int = 0,
) -> List[Dict[str, Any]]:
    """Inject a capacity overload signal across multiple projects sharing a team.

    Args:
        team_id: Team identifier that is over-allocated.
        affected_projects: Projects competing for the team's capacity.
        overload_factor: Ratio of demanded to available capacity (e.g. 1.45 = 145%).
        trigger_day: Offset from simulation start.

    Returns:
        List of DeliveryEvent dicts — one per affected project.
    """
    events = []
    for proj in affected_projects:
        events.append(_make_event(
            event_type="task.updated",
            project_id=proj,
            payload={
                "task_id": f"capacity_check_{proj}",
                "team_id": team_id,
                "new_status": "at_risk",
                "overload_factor": overload_factor,
                "trigger_day": trigger_day,
                "injection_type": "capacity_overload",
                "message": (
                    f"Team {team_id} is at {int(overload_factor * 100)}% capacity "
                    f"across {len(affected_projects)} projects"
                ),
            },
        ))
    return events


def inject_scope_creep(
    project_id: str,
    description: str = "Untracked requirement added without formal change request",
    signal_confidence: float = 0.32,
    trigger_day: int = 0,
) -> List[Dict[str, Any]]:
    """Inject a silent scope creep signal with deliberately low confidence.

    Scope creep is designed to be difficult to detect: confidence is low (<0.40),
    the signal comes from a low-reliability source (slack/meeting notes),
    and there is no formal acknowledgement.

    Args:
        project_id: Project where scope creep is occurring.
        description: Human-readable description of the change.
        signal_confidence: Confidence score (intentionally low, default 0.32).
        trigger_day: Offset from simulation start.

    Returns:
        List with one low-confidence DeliveryEvent.
    """
    # Scope creep always uses low signal quality
    signal_quality = {
        "confidence_score": signal_confidence,
        "is_duplicate": False,
        "is_low_signal": True,  # explicitly flagged as low signal
        "is_decayed": False,
        "sparsity_alert": (
            f"Signal confidence {signal_confidence:.2f} is below threshold 0.40. "
            f"Single unverified source. Scope change not formally acknowledged."
        ),
    }

    return [_make_event(
        event_type="risk.detected",
        project_id=project_id,
        source="slack",  # low-reliability source by default
        payload={
            "task_id": f"scope_creep_{project_id}",
            "new_status": "at_risk",
            "risk_type": "scope_creep",
            "description": description,
            "confidence": signal_confidence,
            "trigger_day": trigger_day,
            "injection_type": "scope_creep",
            "formally_acknowledged": False,
        },
        signal_quality=signal_quality,
    )]


def inject_critical_blocker(
    project_id: str,
    task_id: str = "critical_blocker_task",
    severity: float = 0.85,
    days_late: int = 6,
    trigger_day: int = 0,
) -> List[Dict[str, Any]]:
    """Inject a late-surfacing critical blocker.

    The blocker was present for `days_late` days before surfacing.
    Severity is high (>= 0.85 by default), making this an immediate escalation candidate.

    Args:
        project_id: Project where the blocker lives.
        task_id: Task that is blocked.
        severity: Blocker severity score (0.0–1.0).
        days_late: How many days the blocker was hidden before surfacing.
        trigger_day: Offset from simulation start when this surfaces.

    Returns:
        List with one high-severity DeliveryEvent.
    """
    # Late discovery degrades confidence slightly — information was stale
    adjusted_confidence = max(0.50, 0.92 - (days_late * 0.05))

    signal_quality = {
        "confidence_score": adjusted_confidence,
        "is_duplicate": False,
        "is_low_signal": False,
        "is_decayed": adjusted_confidence < 0.70,
        "sparsity_alert": (
            f"Blocker surfaced {days_late} days late — "
            f"confidence reduced to {adjusted_confidence:.2f}"
        ) if days_late > 3 else None,
    }

    return [_make_event(
        event_type="dependency.blocked",
        project_id=project_id,
        payload={
            "task_id": task_id,
            "new_status": "blocked",
            "severity": severity,
            "days_hidden": days_late,
            "trigger_day": trigger_day,
            "injection_type": "critical_blocker",
            "late_discovery": True,
            "message": (
                f"Critical blocker on {task_id} surfaced {days_late} days late "
                f"(severity={severity:.2f})"
            ),
        },
        signal_quality=signal_quality,
    )]
