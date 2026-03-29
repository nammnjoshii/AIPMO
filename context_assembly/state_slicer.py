"""State slicer — extracts only the canonical state fields relevant to an event type.

Agents must never receive the full CanonicalProjectState — always a scoped slice.
Slice shapes are defined per event type to enforce least-privilege context.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from state.schemas import CanonicalProjectState

logger = logging.getLogger(__name__)

# Which fields to include per event type
# Defined as sets of top-level field names on CanonicalProjectState
_SLICE_FIELDS: Dict[str, set] = {
    "task.updated": {
        "project_id", "milestones", "health", "source_profiles",
    },
    "milestone.updated": {
        "project_id", "milestones", "health", "source_profiles", "decision_history",
    },
    "risk.detected": {
        "project_id", "milestones", "health", "decision_history",
    },
    "dependency.blocked": {
        "project_id", "milestones", "health", "source_profiles", "decision_history",
    },
    "status.reported": {
        "project_id", "milestones", "health",
    },
    "capacity.changed": {
        "project_id", "health", "milestones",
    },
    "signal.qualified": {
        "project_id", "health", "source_profiles",
    },
}

# Fallback for unknown event types — minimal safe set
_DEFAULT_FIELDS: set = {"project_id", "health"}


class StateSlicer:
    """Extracts a scoped slice of CanonicalProjectState for a given event type.

    Never returns the full state object. The slice is a plain dict so agents
    cannot accidentally traverse into fields they shouldn't see.
    """

    def slice(
        self,
        state: CanonicalProjectState,
        event_type: str,
    ) -> Dict[str, Any]:
        """Return a scoped dict of state fields relevant to event_type.

        Args:
            state: Full canonical project state (never passed to agents raw).
            event_type: The event type string (e.g. "task.updated").

        Returns:
            Dict containing only the allowed fields for this event type.
            Always includes project_id. Never includes the full state object.
        """
        fields = _SLICE_FIELDS.get(event_type, _DEFAULT_FIELDS)
        if event_type not in _SLICE_FIELDS:
            logger.warning(
                "StateSlicer: unknown event_type='%s' — using minimal default slice.",
                event_type,
            )

        state_dict = state.model_dump(mode="json")
        sliced: Dict[str, Any] = {}

        for field in fields:
            if field in state_dict:
                sliced[field] = state_dict[field]

        # project_id is always included regardless of slice config
        sliced.setdefault("project_id", state.project_id)

        logger.debug(
            "StateSlicer: sliced event_type=%s → fields=%s project_id=%s",
            event_type,
            sorted(sliced.keys()),
            state.project_id,
        )
        return sliced
