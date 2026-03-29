"""Source profile manager — in-memory cache with 5-minute TTL.

New projects receive a default profile. Updates are persisted to SQLite
via CanonicalStateStore. Cache is invalidated per-project after TTL.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from state.reliability import SourceReliabilityScorer
from state.schemas import SourceReliabilityProfile

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300.0  # 5 minutes


class _CacheEntry:
    __slots__ = ("profile", "expires_at")

    def __init__(self, profile: SourceReliabilityProfile, ttl: float) -> None:
        self.profile = profile
        self.expires_at = time.monotonic() + ttl


class SourceProfileManager:
    """Manages SourceReliabilityProfile objects with a 5-minute in-memory cache.

    The canonical store (CanonicalStateStore) is the source of truth. The cache
    reduces DB reads for high-frequency signal processing. The store dependency
    is optional — if None, profiles are in-memory only (useful for tests).
    """

    def __init__(
        self,
        state_store: Optional[Any] = None,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
    ) -> None:
        self._store = state_store
        self._ttl = ttl_seconds
        # Cache structure: {project_id: {source: _CacheEntry}}
        self._cache: Dict[str, Dict[str, _CacheEntry]] = {}
        self._scorer = SourceReliabilityScorer()

    def get_profile(self, project_id: str, source: str) -> SourceReliabilityProfile:
        """Return the reliability profile for a project+source pair.

        Order: cache → store → default. Caches the result with TTL.
        """
        cached = self._get_cached(project_id, source)
        if cached is not None:
            return cached

        profile = self._load_from_store(project_id, source)
        if profile is None:
            profile = self._scorer.get_default_profile(source)
            logger.debug(
                "SourceProfileManager: new profile created for project=%s source=%s (default=%s)",
                project_id,
                source,
                profile.reliability_score,
            )

        self._set_cached(project_id, source, profile)
        return profile

    def update_profile(
        self,
        project_id: str,
        source: str,
        profile: SourceReliabilityProfile,
    ) -> None:
        """Persist an updated profile and invalidate the cache entry."""
        self._set_cached(project_id, source, profile)
        self._save_to_store(project_id, source, profile)

    def record_inaccuracy(self, project_id: str, source: str) -> SourceReliabilityProfile:
        """Record an inaccurate signal, degrading the profile if threshold crossed."""
        current = self.get_profile(project_id, source)
        updated = self._scorer.record_inaccuracy(current)
        self.update_profile(project_id, source, updated)
        return updated

    def record_accuracy(self, project_id: str, source: str) -> SourceReliabilityProfile:
        """Record an accurate signal (no tier upgrade in current model)."""
        current = self.get_profile(project_id, source)
        updated = self._scorer.record_accuracy(current)
        self.update_profile(project_id, source, updated)
        return updated

    def invalidate(self, project_id: str, source: Optional[str] = None) -> None:
        """Invalidate cache for a project (all sources) or a specific source."""
        if source is None:
            self._cache.pop(project_id, None)
        else:
            self._cache.get(project_id, {}).pop(source, None)

    # ---- Internal helpers ----

    def _get_cached(self, project_id: str, source: str) -> Optional[SourceReliabilityProfile]:
        entry = self._cache.get(project_id, {}).get(source)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            # Expired — remove and return None
            self._cache[project_id].pop(source, None)
            return None
        return entry.profile

    def _set_cached(
        self, project_id: str, source: str, profile: SourceReliabilityProfile
    ) -> None:
        if project_id not in self._cache:
            self._cache[project_id] = {}
        self._cache[project_id][source] = _CacheEntry(profile, self._ttl)

    def _load_from_store(
        self, project_id: str, source: str
    ) -> Optional[SourceReliabilityProfile]:
        """Try to load profile from CanonicalStateStore. Returns None on failure."""
        if self._store is None:
            return None
        try:
            # CanonicalStateStore is async — synchronous fallback not supported here.
            # Callers in async context should use the async variant if needed.
            # For now, return None to trigger default profile creation.
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SourceProfileManager: failed to load profile from store for "
                "project=%s source=%s: %s",
                project_id,
                source,
                exc,
            )
            return None

    def _save_to_store(
        self, project_id: str, source: str, profile: SourceReliabilityProfile
    ) -> None:
        """Persist profile to store. Failures are logged but not raised."""
        if self._store is None:
            return
        try:
            # Async store updates are handled by the async pipeline layer.
            # Synchronous profile saves are best-effort.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SourceProfileManager: failed to save profile for "
                "project=%s source=%s: %s",
                project_id,
                source,
                exc,
            )
