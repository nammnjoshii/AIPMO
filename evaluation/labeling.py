"""Human feedback labeling — T-086.

Over-trust detection: user acceptance rate > 95% over any 30-day window
→ WARNING log + down_weighted=True flag.

Reference: OQ-001 in DECISIONS.md.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_OVER_TRUST_THRESHOLD = 0.95  # Acceptance rate above this → warning
_WINDOW_DAYS = 30


class HumanFeedbackLabel:
    """Represents a single human feedback record."""

    def __init__(
        self,
        agent: str,
        accepted: bool,
        edited: bool = False,
        project_id: Optional[str] = None,
        recorded_at: Optional[datetime] = None,
        feedback_text: Optional[str] = None,
    ) -> None:
        self.agent = agent
        self.accepted = accepted
        self.edited = edited
        self.project_id = project_id
        self.recorded_at = recorded_at or datetime.now(timezone.utc)
        self.feedback_text = feedback_text
        self.down_weighted: bool = False  # set True by over-trust detection


class FeedbackLabeler:
    """Ingests human feedback and detects over-trust patterns.

    Over-trust definition (OQ-001):
        Acceptance rate > 95% over any 30-day rolling window.
        This indicates humans may be rubber-stamping outputs rather
        than genuinely reviewing them — a calibration risk.

    When detected:
        - WARNING logged with acceptance rate and agent name
        - All labels in the window are flagged with down_weighted=True
        - Downstream calibration loop reads this flag to discount
          the data quality from that window

    Labels are stored in-memory for the current session.
    For persistence, use MetricsTracker (evaluation/metrics.py).
    """

    def __init__(self) -> None:
        self._labels: List[HumanFeedbackLabel] = []

    def add_label(self, label: HumanFeedbackLabel) -> HumanFeedbackLabel:
        """Add a feedback label and check for over-trust.

        Args:
            label: HumanFeedbackLabel to record.

        Returns:
            The label with down_weighted set if over-trust detected.
        """
        self._labels.append(label)
        self._check_over_trust(label.agent)
        return label

    def add(
        self,
        agent: str,
        accepted: bool,
        edited: bool = False,
        project_id: Optional[str] = None,
        recorded_at: Optional[datetime] = None,
        feedback_text: Optional[str] = None,
    ) -> HumanFeedbackLabel:
        """Convenience method to create and add a label in one call."""
        label = HumanFeedbackLabel(
            agent=agent,
            accepted=accepted,
            edited=edited,
            project_id=project_id,
            recorded_at=recorded_at,
            feedback_text=feedback_text,
        )
        return self.add_label(label)

    def _check_over_trust(self, agent: str) -> bool:
        """Check for over-trust in the 30-day window for the given agent.

        Returns:
            True if over-trust was detected (triggers warning + down_weight).
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=_WINDOW_DAYS)

        window_labels = [
            lbl for lbl in self._labels
            if lbl.agent == agent
            and lbl.recorded_at >= window_start
        ]

        if not window_labels:
            return False

        total = len(window_labels)
        accepted_count = sum(1 for lbl in window_labels if lbl.accepted)
        acceptance_rate = accepted_count / total

        if acceptance_rate > _OVER_TRUST_THRESHOLD:
            logger.warning(
                "Over-trust detected for agent '%s': acceptance rate %.1f%% "
                "over last %d days (%d/%d accepted). "
                "Labels in window will be down-weighted. "
                "Reviewers may be rubber-stamping — manual calibration recommended. "
                "[OQ-001]",
                agent,
                acceptance_rate * 100,
                _WINDOW_DAYS,
                accepted_count,
                total,
            )
            # Mark all labels in window as down-weighted
            for lbl in window_labels:
                lbl.down_weighted = True
            return True

        return False

    def get_labels(
        self,
        agent: Optional[str] = None,
        window_days: Optional[int] = None,
    ) -> List[HumanFeedbackLabel]:
        """Return labels, optionally filtered by agent and time window.

        Args:
            agent: Filter by agent name.
            window_days: Return only labels within this many days.

        Returns:
            Filtered list of labels.
        """
        labels = self._labels

        if agent:
            labels = [lbl for lbl in labels if lbl.agent == agent]

        if window_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            labels = [lbl for lbl in labels if lbl.recorded_at >= cutoff]

        return labels

    def get_acceptance_rate(
        self,
        agent: Optional[str] = None,
        window_days: Optional[int] = _WINDOW_DAYS,
    ) -> Optional[float]:
        """Compute acceptance rate for an agent over a time window.

        Returns:
            Acceptance rate (0.0–1.0) or None if no data.
        """
        labels = self.get_labels(agent=agent, window_days=window_days)
        if not labels:
            return None
        return sum(1 for lbl in labels if lbl.accepted) / len(labels)

    def get_over_trust_summary(self) -> Dict[str, Any]:
        """Return a summary of over-trust status per agent.

        Returns:
            Dict keyed by agent name → {rate, down_weighted_count, is_over_trust}.
        """
        agents = {lbl.agent for lbl in self._labels}
        summary: Dict[str, Any] = {}
        for agent in agents:
            rate = self.get_acceptance_rate(agent=agent)
            labels_30d = self.get_labels(agent=agent, window_days=_WINDOW_DAYS)
            down_weighted = sum(1 for lbl in labels_30d if lbl.down_weighted)
            summary[agent] = {
                "acceptance_rate_30d": rate,
                "total_30d": len(labels_30d),
                "down_weighted_count": down_weighted,
                "is_over_trust": rate is not None and rate > _OVER_TRUST_THRESHOLD,
            }
        return summary
