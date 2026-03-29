"""Signal Quality Pipeline — single entry point for all raw signal processing.

Sequence per signal:
  normalize → deduplicate → low-signal check → reliability score →
  confidence decay → gap detection → sparsity alert

Outputs a QualifiedSignal dataclass. Duplicates stop early with is_duplicate=True.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from events.schemas.event_types import DeliveryEvent
from signal_quality.confidence_decay import ConfidenceDecayCalculator
from signal_quality.missing_data import GapAlert, MissingDataDetector
from signal_quality.noise_filter import NoiseFilter
from signal_quality.source_profiles import SourceProfileManager
from state.normalization import SignalNormalizer
from state.schemas import CanonicalProjectState, SourceReliabilityProfile

logger = logging.getLogger(__name__)

# Confidence score below this threshold triggers a sparsity alert
_SPARSITY_THRESHOLD = 0.40


@dataclass
class QualifiedSignal:
    """Output of the Signal Quality Pipeline for one raw input signal."""

    event: DeliveryEvent
    is_duplicate: bool
    is_low_signal: bool
    reliability_profile: SourceReliabilityProfile
    confidence_score: float          # decay_score × reliability_multiplier, clamped [0,1]
    is_decayed: bool
    gap_alerts: List[GapAlert]
    sparsity_alert: Optional[str]    # human-readable message if confidence below threshold
    qualified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SignalQualityPipeline:
    """Orchestrates signal quality assessment from raw payload to QualifiedSignal.

    Dependencies:
        normalizer      — converts raw dict to DeliveryEvent
        noise_filter    — Redis-backed dedup + low-signal detection
        profile_manager — per-source reliability profiles
        decay_calc      — freshness-based confidence decay
        gap_detector    — 3-rule gap / missing-data detection
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        state_store: Optional[Any] = None,
    ) -> None:
        self._normalizer = SignalNormalizer()
        self._noise_filter = NoiseFilter(redis_url=redis_url)
        self._profile_manager = SourceProfileManager(state_store=state_store)
        self._decay_calc = ConfidenceDecayCalculator()
        self._gap_detector = MissingDataDetector()

    def process(
        self,
        raw_signal: Dict[str, Any],
        source: str,
        project_id: Optional[str] = None,
        tenant_id: str = "default",
        canonical_state: Optional[CanonicalProjectState] = None,
        last_signal_times: Optional[Dict[str, datetime]] = None,
        last_completion_time: Optional[datetime] = None,
        last_mitigation_time: Optional[datetime] = None,
    ) -> QualifiedSignal:
        """Process a raw signal through the full quality pipeline.

        Args:
            raw_signal: Arbitrary dict from source webhook or poller.
            source: Source name (jira, github, slack, etc.).
            project_id: Optional override; falls back to raw_signal["project_id"].
            tenant_id: Tenant scoping identifier.
            canonical_state: Current project state for gap detection.
                             If None, gap detection is skipped.
            last_signal_times: Dict mapping source → last signal datetime.
                               Passed to MissingDataDetector rule 1.
            last_completion_time: Last task completion UTC datetime (rule 2).
            last_mitigation_time: Last mitigation signal UTC datetime (rule 3).

        Returns:
            QualifiedSignal — always returned, never raises.
        """
        try:
            return self._process(
                raw_signal,
                source,
                project_id,
                tenant_id,
                canonical_state,
                last_signal_times or {},
                last_completion_time,
                last_mitigation_time,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SignalQualityPipeline.process: unexpected error for source=%s "
                "project_id=%s: %s. Returning degraded QualifiedSignal.",
                source,
                project_id,
                exc,
                exc_info=True,
            )
            # Return a minimal safe output so callers are never blocked
            fallback_event = self._normalizer.normalize(
                raw_signal, source, project_id, tenant_id
            )
            fallback_profile = self._profile_manager.get_profile(
                fallback_event.project_id, fallback_event.source
            )
            return QualifiedSignal(
                event=fallback_event,
                is_duplicate=False,
                is_low_signal=False,
                reliability_profile=fallback_profile,
                confidence_score=0.0,
                is_decayed=True,
                gap_alerts=[],
                sparsity_alert=(
                    f"[PIPELINE ERROR] project_id={fallback_event.project_id} "
                    f"at {datetime.now(timezone.utc).isoformat()} — "
                    f"signal quality assessment failed: {exc}"
                ),
            )

    # ---- Private orchestration ----

    def _process(
        self,
        raw_signal: Dict[str, Any],
        source: str,
        project_id: Optional[str],
        tenant_id: str,
        canonical_state: Optional[CanonicalProjectState],
        last_signal_times: Dict[str, datetime],
        last_completion_time: Optional[datetime],
        last_mitigation_time: Optional[datetime],
    ) -> QualifiedSignal:
        # Step 1 — Normalize
        event = self._normalizer.normalize(raw_signal, source, project_id, tenant_id)
        logger.debug(
            "Pipeline step 1 normalize: event_id=%s project=%s source=%s type=%s",
            event.event_id,
            event.project_id,
            event.source,
            event.event_type,
        )

        # Step 2 — Deduplicate (early exit on duplicate)
        if self._noise_filter.is_duplicate(
            event.project_id, raw_signal, event.source, str(event.event_type)
        ):
            logger.info(
                "Pipeline step 2 dedup: DUPLICATE event_id=%s project=%s source=%s — stopping.",
                event.event_id,
                event.project_id,
                event.source,
            )
            profile = self._profile_manager.get_profile(event.project_id, event.source)
            return QualifiedSignal(
                event=event,
                is_duplicate=True,
                is_low_signal=False,
                reliability_profile=profile,
                confidence_score=0.0,
                is_decayed=False,
                gap_alerts=[],
                sparsity_alert=None,
            )

        # Step 3 — Low-signal check
        actor = raw_signal.get("actor") or raw_signal.get("user") or raw_signal.get("author")
        is_low = self._noise_filter.is_low_signal(raw_signal, event.source, actor=actor)
        if is_low:
            logger.info(
                "Pipeline step 3 low-signal: event_id=%s project=%s source=%s flagged.",
                event.event_id,
                event.project_id,
                event.source,
            )

        # Step 4 — Reliability score
        profile = self._profile_manager.get_profile(event.project_id, event.source)
        reliability_multiplier = self._reliability_multiplier(profile.reliability_score)
        logger.debug(
            "Pipeline step 4 reliability: source=%s tier=%s multiplier=%.2f",
            event.source,
            profile.reliability_score,
            reliability_multiplier,
        )

        # Step 5 — Confidence decay
        decay_result = self._decay_calc.calculate(event.source, event.timestamp)
        raw_confidence = decay_result.confidence_score * reliability_multiplier
        confidence_score = max(0.0, min(1.0, raw_confidence))
        logger.debug(
            "Pipeline step 5 decay: source=%s age_hours=%.2f decay_score=%.3f "
            "combined=%.3f is_decayed=%s",
            event.source,
            decay_result.age_hours,
            decay_result.confidence_score,
            confidence_score,
            decay_result.is_decayed,
        )

        # Step 6 — Gap detection
        gap_alerts: List[GapAlert] = []
        if canonical_state is not None:
            gap_alerts = self._gap_detector.check_all(
                canonical_state,
                last_signal_times,
                last_completion_time,
                last_mitigation_time,
            )
            if gap_alerts:
                logger.info(
                    "Pipeline step 6 gap detection: %d alert(s) for project=%s",
                    len(gap_alerts),
                    event.project_id,
                )

        # Step 7 — Sparsity alert
        sparsity_alert = self._build_sparsity_alert(
            event.project_id, confidence_score, gap_alerts, decay_result.is_decayed
        )

        return QualifiedSignal(
            event=event,
            is_duplicate=False,
            is_low_signal=is_low,
            reliability_profile=profile,
            confidence_score=confidence_score,
            is_decayed=decay_result.is_decayed,
            gap_alerts=gap_alerts,
            sparsity_alert=sparsity_alert,
        )

    @staticmethod
    def _reliability_multiplier(tier: str) -> float:
        """Convert reliability tier to a confidence multiplier."""
        return {"high": 1.0, "medium": 0.75, "low": 0.50}.get(tier, 0.75)

    def _build_sparsity_alert(
        self,
        project_id: str,
        confidence_score: float,
        gap_alerts: List[GapAlert],
        is_decayed: bool,
    ) -> Optional[str]:
        """Return a sparsity alert string if confidence is below threshold or gaps exist."""
        reasons: List[str] = []

        if confidence_score < _SPARSITY_THRESHOLD:
            reasons.append(
                f"confidence_score={confidence_score:.3f} below threshold={_SPARSITY_THRESHOLD}"
            )

        if is_decayed:
            reasons.append("signal freshness has decayed beyond threshold")

        if gap_alerts:
            rule_ids = ", ".join(a.rule_id for a in gap_alerts)
            reasons.append(f"gap rules triggered: [{rule_ids}]")

        if not reasons:
            return None

        timestamp = datetime.now(timezone.utc).isoformat()
        return (
            f"[SPARSITY ALERT] project_id={project_id} at {timestamp} — "
            + "; ".join(reasons)
            + ". Agent output confidence is degraded — review before escalation."
        )
