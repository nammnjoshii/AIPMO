"""Confidence decay calculator.

Each source has a high-confidence window and a decay trigger point.
Between those two boundaries, confidence decays linearly from 1.0 to 0.0.
Beyond the decay trigger, confidence is 0.0 and the signal is marked as decayed.

Decay windows from README.md:
| Source               | High Confidence Window | Decay Trigger        |
|----------------------|------------------------|----------------------|
| jira                 | 24 hours               | No update in 48h     |
| github (velocity)    | 72 hours               | No commit in 5 days  |
| github_issues        | 24 hours               | No update in 48h     |
| slack                | 4 hours                | Thread older than 24h|
| meeting_notes        | 48 hours               | No follow-up in 72h  |
| manual               | 7 days                 | Next cycle overdue   |
| google_sheets        | 24 hours               | No update in 48h     |
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Tuple


@dataclass
class DecayResult:
    confidence_score: float    # 0.0 – 1.0 after applying decay
    is_decayed: bool           # True when signal age exceeds decay_trigger_hours
    age_hours: float           # Elapsed hours since signal_timestamp
    source: str


# (high_confidence_hours, decay_trigger_hours)
_DECAY_WINDOWS: Dict[str, Tuple[float, float]] = {
    "jira":          (24.0, 48.0),
    "github_issues": (24.0, 48.0),
    "github":        (72.0, 120.0),   # 5 days = 120h
    "slack":         (4.0,  24.0),
    "meeting_notes": (48.0, 72.0),
    "manual":        (168.0, 336.0),  # 7 days / 14 days
    "google_sheets": (24.0, 48.0),
    "smartsheet":    (24.0, 48.0),
    "ms_project":    (48.0, 72.0),
}

_DEFAULT_WINDOW: Tuple[float, float] = (24.0, 48.0)


class ConfidenceDecayCalculator:
    """Compute confidence score and decay status for a signal based on its age."""

    HIGH_CONFIDENCE_WINDOWS: Dict[str, Tuple[float, float]] = _DECAY_WINDOWS

    def calculate(self, source: str, signal_timestamp: datetime) -> DecayResult:
        """Return decay result for a signal from the given source.

        Args:
            source: Canonical source name (e.g. "jira", "github_issues", "slack").
            signal_timestamp: UTC-aware datetime when the signal was emitted.

        Returns:
            DecayResult with confidence_score, is_decayed, and age_hours.
        """
        now = datetime.now(timezone.utc)
        ts = signal_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        age_hours = (now - ts).total_seconds() / 3600.0
        high_conf_hours, decay_trigger_hours = _DECAY_WINDOWS.get(
            source.lower(), _DEFAULT_WINDOW
        )

        if age_hours <= high_conf_hours:
            # Within the high-confidence window
            return DecayResult(
                confidence_score=1.0,
                is_decayed=False,
                age_hours=age_hours,
                source=source,
            )

        if age_hours >= decay_trigger_hours:
            # Past the decay trigger — fully decayed
            return DecayResult(
                confidence_score=0.0,
                is_decayed=True,
                age_hours=age_hours,
                source=source,
            )

        # Linear interpolation between high_conf_hours (1.0) and decay_trigger_hours (0.0)
        decay_range = decay_trigger_hours - high_conf_hours
        elapsed_in_decay = age_hours - high_conf_hours
        confidence = 1.0 - (elapsed_in_decay / decay_range)
        confidence = max(0.0, min(1.0, confidence))

        return DecayResult(
            confidence_score=confidence,
            is_decayed=False,
            age_hours=age_hours,
            source=source,
        )

    def get_high_confidence_hours(self, source: str) -> float:
        """Return the high-confidence window in hours for a source."""
        return _DECAY_WINDOWS.get(source.lower(), _DEFAULT_WINDOW)[0]

    def get_decay_trigger_hours(self, source: str) -> float:
        """Return the decay trigger threshold in hours for a source."""
        return _DECAY_WINDOWS.get(source.lower(), _DEFAULT_WINDOW)[1]
