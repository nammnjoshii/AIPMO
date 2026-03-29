"""Source reliability scorer.

Tracks accuracy of signals per source. Score degrades on inaccurate outcomes.
Default reliability levels match README.md: jira → medium, github → high, slack → low.
"""
from __future__ import annotations

import logging
from typing import Dict, Literal

from state.schemas import SourceReliabilityProfile

logger = logging.getLogger(__name__)

# Canonical source → default reliability tier
_DEFAULT_RELIABILITY: Dict[str, Literal["high", "medium", "low"]] = {
    "github_issues": "high",
    "github": "high",
    "jira": "medium",
    "google_sheets": "medium",
    "smartsheet": "medium",
    "slack": "low",
    "manual": "medium",
    "ms_project": "medium",
    "meeting_notes": "low",
}

# How many inaccuracy events before the score degrades a tier
_DEGRADATION_THRESHOLDS: Dict[str, int] = {
    "high": 2,    # 3rd inaccuracy degrades high → medium
    "medium": 2,  # 3rd inaccuracy degrades medium → low
    "low": 99,    # low is the floor
}

_TIER_ORDER = ["high", "medium", "low"]


class SourceReliabilityScorer:
    """Score and update reliability profiles for signal sources.

    Profiles are persisted externally (via CanonicalStateStore or SourceProfileManager).
    This class provides pure scoring logic only.
    """

    def get_default_profile(self, source: str) -> SourceReliabilityProfile:
        """Return the default reliability profile for a given source."""
        score = _DEFAULT_RELIABILITY.get(source.lower(), "medium")
        return SourceReliabilityProfile(source_name=source, reliability_score=score, inaccuracy_count=0)

    def record_inaccuracy(self, profile: SourceReliabilityProfile) -> SourceReliabilityProfile:
        """Increment the inaccuracy count and potentially degrade the reliability tier.

        Returns a new SourceReliabilityProfile (does not mutate the input).
        """
        new_count = profile.inaccuracy_count + 1
        current_tier = profile.reliability_score
        threshold = _DEGRADATION_THRESHOLDS.get(current_tier, 99)

        if new_count > threshold:
            new_tier = self._degrade_tier(current_tier)
            if new_tier != current_tier:
                logger.info(
                    "Source reliability degraded from '%s' to '%s' after %d inaccuracies.",
                    current_tier,
                    new_tier,
                    new_count,
                )
            return SourceReliabilityProfile(
                source_name=profile.source_name,
                reliability_score=new_tier,
                inaccuracy_count=new_count,
            )

        return SourceReliabilityProfile(
            source_name=profile.source_name,
            reliability_score=current_tier,
            inaccuracy_count=new_count,
        )

    def record_accuracy(self, profile: SourceReliabilityProfile) -> SourceReliabilityProfile:
        """Confirm a signal was accurate. Does not currently upgrade the tier
        (consistent with the README: scores degrade on inaccuracy, no recovery logic).
        Returns the same profile unchanged.
        """
        return profile

    def to_confidence_multiplier(self, profile: SourceReliabilityProfile) -> float:
        """Convert a reliability profile to a 0–1 confidence multiplier.

        high   → 1.0
        medium → 0.75
        low    → 0.50
        """
        return {
            "high": 1.0,
            "medium": 0.75,
            "low": 0.50,
        }.get(profile.reliability_score, 0.75)

    @staticmethod
    def _degrade_tier(tier: Literal["high", "medium", "low"]) -> Literal["high", "medium", "low"]:
        idx = _TIER_ORDER.index(tier)
        next_idx = min(idx + 1, len(_TIER_ORDER) - 1)
        return _TIER_ORDER[next_idx]  # type: ignore[return-value]
