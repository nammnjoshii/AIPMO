"""Orchestrator conflict resolver — T-057.

Detects and resolves conflicting agent outputs.
Conflict: two agents produce opposing assessments with both confidence > 0.5.

Used by EventRouter and ProgramDirectorAgent.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from agents.base_agent import AgentOutput, DecisionType, PolicyAction
from agents.program_director.agent import ProgramDirectorAgent

logger = logging.getLogger(__name__)

# Policy action restrictiveness order
_POLICY_RANK = {
    PolicyAction.ALLOW: 0,
    PolicyAction.ALLOW_WITH_AUDIT: 1,
    PolicyAction.APPROVAL_REQUIRED: 2,
    PolicyAction.DENY: 3,
    PolicyAction.ESCALATE: 4,
}

_HIGH_CONFIDENCE_THRESHOLD = 0.50


def detect_conflict(outputs: List[AgentOutput]) -> bool:
    """Return True if two or more agents have high-confidence opposing assessments.

    Conflict criteria:
    - At least two outputs with confidence > 0.5
    - One recommends allow/allow_with_audit while another recommends approval_required/escalate
    """
    if len(outputs) < 2:
        return False

    allow_group = {PolicyAction.ALLOW, PolicyAction.ALLOW_WITH_AUDIT}
    escalate_group = {PolicyAction.APPROVAL_REQUIRED, PolicyAction.ESCALATE}

    high_conf_allow = [
        o for o in outputs
        if o.policy_action in allow_group and o.confidence_score > _HIGH_CONFIDENCE_THRESHOLD
    ]
    high_conf_escalate = [
        o for o in outputs
        if o.policy_action in escalate_group and o.confidence_score > _HIGH_CONFIDENCE_THRESHOLD
    ]

    return bool(high_conf_allow and high_conf_escalate)


def resolve(outputs: List[AgentOutput]) -> AgentOutput:
    """Resolve a conflict using ProgramDirectorAgent.resolve().

    Args:
        outputs: List of conflicting AgentOutput instances.

    Returns:
        Single resolved AgentOutput from ProgramDirectorAgent.
    """
    director = ProgramDirectorAgent()
    resolved = director.resolve(outputs)
    logger.info(
        "ConflictResolver: resolved conflict across %d agents → policy=%s confidence=%.2f conflict=%s",
        len(outputs),
        resolved.policy_action.value,
        resolved.confidence_score,
        resolved.extra.get("conflict_detected", False),
    )
    return resolved
