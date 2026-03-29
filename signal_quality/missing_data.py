"""Missing data detector — gap rules for signal sparsity.

Three gap rules (from README.md / plan T-020):
1. No signal from any source in the last 48 hours.
2. Milestone due within 7 days, but no task completion in the last 72 hours.
3. At-risk milestone with no mitigation signal in the last 24 hours.

Each rule returns a distinct GapAlert with a description. Rules trigger independently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from state.schemas import CanonicalProjectState

logger = logging.getLogger(__name__)

# ---- Rule thresholds ----
_NO_SIGNAL_HOURS = 48.0
_MILESTONE_PROXIMITY_DAYS = 7
_TASK_COMPLETION_SILENCE_HOURS = 72.0
_MITIGATION_SILENCE_HOURS = 24.0


@dataclass
class GapAlert:
    rule_id: str
    project_id: str
    description: str
    severity: str = "medium"  # low | medium | high
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MissingDataDetector:
    """Detects signal gaps in canonical project state.

    Each check method evaluates one gap rule and returns a GapAlert if triggered,
    or None if the condition is not met. All methods are pure (no side effects).
    """

    def check_all(
        self,
        state: CanonicalProjectState,
        last_signal_times: Dict[str, datetime],
        last_completion_time: Optional[datetime] = None,
        last_mitigation_time: Optional[datetime] = None,
    ) -> List[GapAlert]:
        """Run all three gap rules and return any triggered alerts.

        Args:
            state: Current canonical project state.
            last_signal_times: Dict mapping source name → last signal datetime.
                               Used for rule 1.
            last_completion_time: Most recent task completion UTC datetime.
                                  Used for rule 2.
            last_mitigation_time: Most recent mitigation signal UTC datetime.
                                  Used for rule 3.

        Returns:
            List of GapAlert (empty if no gaps detected).
        """
        alerts: List[GapAlert] = []

        alert1 = self.check_no_recent_signal(state.project_id, last_signal_times)
        if alert1:
            alerts.append(alert1)

        for milestone in state.milestones:
            alert2 = self.check_milestone_without_completion(
                state.project_id, milestone, last_completion_time
            )
            if alert2:
                alerts.append(alert2)
                break  # One alert per project for this rule

        for milestone in state.milestones:
            if milestone.status == "at_risk":
                alert3 = self.check_at_risk_without_mitigation(
                    state.project_id, milestone, last_mitigation_time
                )
                if alert3:
                    alerts.append(alert3)
                    break  # One alert per project for this rule

        return alerts

    def check_no_recent_signal(
        self,
        project_id: str,
        last_signal_times: Dict[str, datetime],
    ) -> Optional[GapAlert]:
        """Rule 1: No signal from any source in the last 48 hours."""
        if not last_signal_times:
            return GapAlert(
                rule_id="gap_rule_1_no_signal",
                project_id=project_id,
                description=(
                    f"No signal received from any source for project {project_id}. "
                    f"No signal history available — data may be stale."
                ),
                severity="high",
            )

        now = datetime.now(timezone.utc)
        most_recent = max(
            (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))
            for ts in last_signal_times.values()
        )
        age_hours = (now - most_recent).total_seconds() / 3600.0

        if age_hours >= _NO_SIGNAL_HOURS:
            return GapAlert(
                rule_id="gap_rule_1_no_signal",
                project_id=project_id,
                description=(
                    f"No signal received from any source for project {project_id} "
                    f"in the last {age_hours:.1f} hours "
                    f"(threshold: {_NO_SIGNAL_HOURS}h). "
                    f"Confidence in current state is degraded."
                ),
                severity="high",
            )
        return None

    def check_milestone_without_completion(
        self,
        project_id: str,
        milestone: Any,
        last_completion_time: Optional[datetime],
    ) -> Optional[GapAlert]:
        """Rule 2: Milestone due within 7 days but no task completion in 72 hours."""
        now = datetime.now(timezone.utc)
        due = milestone.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)

        days_until_due = (due - now).total_seconds() / 86400.0
        if days_until_due > _MILESTONE_PROXIMITY_DAYS or days_until_due < 0:
            return None

        if last_completion_time is None:
            return GapAlert(
                rule_id="gap_rule_2_milestone_no_completion",
                project_id=project_id,
                description=(
                    f"Milestone '{milestone.name}' (id={milestone.milestone_id}) "
                    f"is due in {days_until_due:.1f} days but no task completion "
                    f"has been recorded. Progress may be stalled."
                ),
                severity="high",
            )

        lct = last_completion_time
        if lct.tzinfo is None:
            lct = lct.replace(tzinfo=timezone.utc)

        completion_age_hours = (now - lct).total_seconds() / 3600.0
        if completion_age_hours >= _TASK_COMPLETION_SILENCE_HOURS:
            return GapAlert(
                rule_id="gap_rule_2_milestone_no_completion",
                project_id=project_id,
                description=(
                    f"Milestone '{milestone.name}' (id={milestone.milestone_id}) "
                    f"is due in {days_until_due:.1f} days but no task completion "
                    f"has been recorded in the last {completion_age_hours:.1f} hours "
                    f"(threshold: {_TASK_COMPLETION_SILENCE_HOURS}h). "
                    f"Delivery progress may be stalled."
                ),
                severity="high",
            )
        return None

    def check_at_risk_without_mitigation(
        self,
        project_id: str,
        milestone: Any,
        last_mitigation_time: Optional[datetime],
    ) -> Optional[GapAlert]:
        """Rule 3: At-risk milestone with no mitigation signal in the last 24 hours."""
        if milestone.status != "at_risk":
            return None

        now = datetime.now(timezone.utc)

        if last_mitigation_time is None:
            return GapAlert(
                rule_id="gap_rule_3_at_risk_no_mitigation",
                project_id=project_id,
                description=(
                    f"Milestone '{milestone.name}' (id={milestone.milestone_id}) "
                    f"is marked at_risk but no mitigation signal has been recorded. "
                    f"Risk is unaddressed."
                ),
                severity="medium",
            )

        lmt = last_mitigation_time
        if lmt.tzinfo is None:
            lmt = lmt.replace(tzinfo=timezone.utc)

        mitigation_age_hours = (now - lmt).total_seconds() / 3600.0
        if mitigation_age_hours >= _MITIGATION_SILENCE_HOURS:
            return GapAlert(
                rule_id="gap_rule_3_at_risk_no_mitigation",
                project_id=project_id,
                description=(
                    f"Milestone '{milestone.name}' (id={milestone.milestone_id}) "
                    f"is marked at_risk but no mitigation signal received in the last "
                    f"{mitigation_age_hours:.1f} hours "
                    f"(threshold: {_MITIGATION_SILENCE_HOURS}h). "
                    f"Risk response may be absent."
                ),
                severity="medium",
            )
        return None
