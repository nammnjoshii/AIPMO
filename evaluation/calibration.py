"""Calibration loop — T-087.

CalibrationLoop.run() reads labels, computes threshold drift,
and writes recommendations.

IMPORTANT: Recommendations are suggestions ONLY.
This module NEVER auto-applies changes to configs/policies.yaml.
Reference: OQ-001 in DECISIONS.md.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_POLICIES_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "policies.yaml",
)

# Drift threshold: if computed metric deviates > this from target,
# flag a calibration recommendation
_DRIFT_THRESHOLD = 0.10


class CalibrationRecommendation:
    """A calibration recommendation produced by the loop.

    NEVER auto-applied. Human must review and decide.
    """

    def __init__(
        self,
        agent: str,
        metric: str,
        current_value: float,
        target_value: float,
        direction: str,  # "increase" or "decrease"
        suggestion: str,
        priority: str = "medium",  # low | medium | high
    ) -> None:
        self.agent = agent
        self.metric = metric
        self.current_value = current_value
        self.target_value = target_value
        self.direction = direction
        self.suggestion = suggestion
        self.priority = priority
        self.created_at = datetime.now(timezone.utc).isoformat()


class CalibrationLoop:
    """Reads human feedback labels, computes drift, produces recommendations.

    Design constraints (OQ-001):
    - Recommendations are SUGGESTIONS ONLY — never auto-applied.
    - This module does not write to configs/policies.yaml.
    - If auto-application is desired in future, it requires a separate
      human review gate (approval_required policy outcome).
    """

    def __init__(
        self,
        labeler=None,
        metrics_tracker=None,
    ) -> None:
        self._labeler = labeler
        self._metrics_tracker = metrics_tracker

    def run(self) -> List[CalibrationRecommendation]:
        """Run one calibration cycle.

        Reads labels from the labeler, computes drift per metric,
        and generates recommendations.

        Returns:
            List of CalibrationRecommendation objects (may be empty).
            NEVER modifies configs/policies.yaml.
        """
        recommendations: List[CalibrationRecommendation] = []

        if self._labeler is None:
            logger.info("CalibrationLoop: no labeler configured — skipping label analysis")
            return recommendations

        summary = self._labeler.get_over_trust_summary()

        for agent, status in summary.items():
            rate = status.get("acceptance_rate_30d")
            total = status.get("total_30d", 0)

            if rate is None or total < 5:
                # Not enough data — OQ-001: small sample warnings
                logger.info(
                    "CalibrationLoop: insufficient data for agent '%s' "
                    "(n=%d) — skipping drift check. [OQ-001]",
                    agent,
                    total,
                )
                continue

            if status.get("is_over_trust"):
                # Acceptance rate is too high — may indicate rubber-stamping
                recommendations.append(CalibrationRecommendation(
                    agent=agent,
                    metric="acceptance_rate",
                    current_value=rate,
                    target_value=0.90,
                    direction="decrease",
                    suggestion=(
                        f"Agent '{agent}' acceptance rate is {rate:.1%} over 30 days "
                        f"(n={total}) — above the 95% over-trust threshold. "
                        "Consider adding diversity to prompts, injecting harder "
                        "test cases, or scheduling a human calibration review. "
                        "Do NOT reduce thresholds automatically. [OQ-001]"
                    ),
                    priority="high" if rate > 0.98 else "medium",
                ))

        # If metrics tracker is available, check metric-level drift
        if self._metrics_tracker is not None:
            import asyncio
            try:
                if asyncio.get_event_loop().is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, self._check_metric_drift())
                        metric_recs = future.result(timeout=10)
                else:
                    metric_recs = asyncio.run(self._check_metric_drift())
                recommendations.extend(metric_recs)
            except Exception as e:
                logger.warning("CalibrationLoop: metric drift check failed: %s", e)

        if recommendations:
            logger.info(
                "CalibrationLoop: %d recommendation(s) generated. "
                "Review manually before applying. [OQ-001]",
                len(recommendations),
            )
        else:
            logger.info("CalibrationLoop: no calibration recommendations at this time.")

        return recommendations

    async def _check_metric_drift(self) -> List[CalibrationRecommendation]:
        """Check for drift against targets using MetricsTracker."""
        recs = []
        try:
            report = await self._metrics_tracker.get_report()
        except Exception as e:
            logger.warning("CalibrationLoop: failed to fetch metrics report: %s", e)
            return recs

        for agent, metrics in report.items():
            for metric_name, data in metrics.items():
                current = data.get("current")
                target = data.get("target")
                status = data.get("status")
                direction_str = data.get("direction", "gte")

                if current is None or target is None or status == "PASS":
                    continue

                drift = abs(current - target)
                if drift < _DRIFT_THRESHOLD:
                    continue

                if direction_str == "gte":
                    rec_direction = "increase"
                    suggestion = (
                        f"{agent}.{metric_name} is {current:.3f} (target ≥ {target:.3f}, "
                        f"drift={drift:.3f}). Consider reviewing agent prompts or "
                        f"detection thresholds to improve {metric_name}. "
                        "Do NOT auto-apply policy changes. [OQ-001]"
                    )
                else:
                    rec_direction = "decrease"
                    suggestion = (
                        f"{agent}.{metric_name} is {current:.3f} (target ≤ {target:.3f}, "
                        f"drift={drift:.3f}). Consider tuning thresholds to reduce "
                        f"{metric_name}. Do NOT auto-apply policy changes. [OQ-001]"
                    )

                recs.append(CalibrationRecommendation(
                    agent=agent,
                    metric=metric_name,
                    current_value=current,
                    target_value=target,
                    direction=rec_direction,
                    suggestion=suggestion,
                    priority="high" if drift > 0.20 else "medium",
                ))

        return recs

    def print_recommendations(self, recommendations: List[CalibrationRecommendation]) -> None:
        """Print recommendations to stdout. Does NOT apply them."""
        if not recommendations:
            print("\n[CalibrationLoop] No recommendations at this time.\n")
            return

        print(f"\n{'=' * 70}")
        print(f"{'CALIBRATION RECOMMENDATIONS (REVIEW ONLY — NOT AUTO-APPLIED)':^70}")
        print(f"{'=' * 70}")
        print(f"NOTE: These are suggestions. DO NOT apply without human review. [OQ-001]")
        print(f"{'=' * 70}\n")

        for i, rec in enumerate(recommendations, 1):
            print(f"[{rec.priority.upper()}] #{i}: {rec.agent}.{rec.metric}")
            print(f"  Current: {rec.current_value:.3f}  →  Target: {rec.target_value:.3f}")
            print(f"  Direction: {rec.direction}")
            print(f"  Suggestion: {rec.suggestion}")
            print()

        print(f"{'=' * 70}")
        print(f"Total: {len(recommendations)} recommendation(s)")
        print(f"{'=' * 70}\n")
