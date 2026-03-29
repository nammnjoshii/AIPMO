"""Noise filter — Redis-backed deduplication and low-signal detection.

Deduplication uses Redis hash key `dedup:{project_id}:{event_hash}` with TTL=300s.
Events from different projects with the same payload are NOT deduplicated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEDUP_TTL_SECONDS = 300

# Bot/automation account patterns that indicate low-signal events
_BOT_PATTERNS: List[str] = [
    "[bot]", "github-actions", "dependabot", "renovate", "codecov",
    "snyk-bot", "imgbot", "automated", "ci-bot",
]

# Labels that indicate low-signal status changes
_LOW_SIGNAL_LABELS: List[str] = [
    "duplicate", "invalid", "wontfix", "spam",
]


class NoiseFilter:
    """Deduplicates events via Redis and detects low-signal payloads.

    Redis client is lazy-initialized on first use. If Redis is unavailable,
    deduplication is skipped (not raised), and a WARNING is logged.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._redis: Optional[Any] = None

    def _get_redis(self) -> Optional[Any]:
        """Lazy-initialize Redis client."""
        if self._redis is not None:
            return self._redis
        try:
            import redis as redis_lib
            self._redis = redis_lib.from_url(self._redis_url, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception as exc:  # noqa: BLE001
            logger.warning("NoiseFilter: Redis unavailable (%s). Deduplication skipped.", exc)
            return None

    def _event_hash(self, event_payload: Dict[str, Any], source: str, event_type: str) -> str:
        """Compute a deterministic hash of the event for deduplication."""
        # Sort keys for determinism; exclude volatile fields like timestamps
        stable = {
            "source": source,
            "event_type": event_type,
            "payload": self._stable_payload(event_payload),
        }
        raw = json.dumps(stable, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _stable_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Strip timestamp fields from payload before hashing."""
        _VOLATILE = {"timestamp", "updated_at", "created_at", "event_id", "received_at"}
        return {k: v for k, v in payload.items() if k not in _VOLATILE}

    def is_duplicate(
        self,
        project_id: str,
        event_payload: Dict[str, Any],
        source: str,
        event_type: str,
        ttl: int = _DEDUP_TTL_SECONDS,
    ) -> bool:
        """Return True if this event was already seen within the TTL window.

        Uses Redis SETNX (set if not exists) with TTL for atomic dedup.
        Different project_ids are never deduplicated even with identical payloads.
        Falls back to False (allow through) if Redis is unavailable.
        """
        r = self._get_redis()
        if r is None:
            return False

        event_hash = self._event_hash(event_payload, source, event_type)
        dedup_key = f"dedup:{project_id}:{event_hash}"

        try:
            was_set = r.set(dedup_key, "1", nx=True, ex=ttl)
            # was_set is True if key was newly created (not a duplicate)
            # was_set is None if key already existed (duplicate)
            return was_set is None
        except Exception as exc:  # noqa: BLE001
            logger.warning("NoiseFilter.is_duplicate: Redis error (%s). Allowing event through.", exc)
            return False

    def is_low_signal(
        self,
        event_payload: Dict[str, Any],
        source: str,
        actor: Optional[str] = None,
    ) -> bool:
        """Return True for events that carry no actionable information.

        Low-signal conditions:
        1. Actor is a known bot account
        2. Payload is empty or contains only whitespace
        3. Status transition moves to the same status (no change)
        4. Event carries low-signal labels
        """
        # 1. Bot actor check
        if actor and self._is_bot_actor(actor):
            logger.debug("NoiseFilter: bot actor detected (%s) from source=%s", actor, source)
            return True

        # 2. Empty payload
        if not event_payload or all(
            v is None or (isinstance(v, str) and not v.strip())
            for v in event_payload.values()
        ):
            logger.debug("NoiseFilter: empty payload from source=%s", source)
            return True

        # 3. Same-status transition
        old_status = event_payload.get("old_status") or event_payload.get("previous_status")
        new_status = event_payload.get("new_status") or event_payload.get("status")
        if old_status and new_status and old_status == new_status:
            logger.debug(
                "NoiseFilter: same-status transition (%s → %s) from source=%s",
                old_status,
                new_status,
                source,
            )
            return True

        # 4. Low-signal labels
        labels = event_payload.get("labels", [])
        if isinstance(labels, list):
            label_values = [
                (lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)).lower()
                for lbl in labels
            ]
            if any(lv in _LOW_SIGNAL_LABELS for lv in label_values):
                logger.debug(
                    "NoiseFilter: low-signal label detected in payload from source=%s", source
                )
                return True

        return False

    @staticmethod
    def _is_bot_actor(actor: str) -> bool:
        actor_lower = actor.lower()
        return any(pattern in actor_lower for pattern in _BOT_PATTERNS)
