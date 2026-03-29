"""BaseAgent contract — the foundation all 7 agents implement.

Hard rules enforced here:
- AgentOutput.uncertainty_notes empty → raises ValueError
- AgentOutput.confidence_score outside 0.0–1.0 → raises ValueError
- Agents propose actions; they do not call the policy engine directly.
- No agent imports from sibling agent folders.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionType(str, Enum):
    OBSERVATION = "observation"
    DECISION_PREPARATION = "decision_preparation"
    EXECUTION = "execution"


class PolicyAction(str, Enum):
    ALLOW = "allow"
    ALLOW_WITH_AUDIT = "allow_with_audit"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass
class AgentInput:
    project_id: str
    event_type: str
    canonical_state: Dict[str, Any]
    graph_context: Dict[str, Any]
    historical_cases: List[Any]
    policy_context: Dict[str, Any]
    signal_quality: Dict[str, Any]
    tenant_id: str = "default"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    agent_name: str
    decision_type: DecisionType
    confidence_score: float
    evidence: List[str]
    decision_factors: List[str]
    uncertainty_notes: List[str]
    policy_action: PolicyAction
    recommendation: Optional[str] = None
    proposed_state_updates: Dict[str, Any] = field(default_factory=dict)
    proposed_graph_updates: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.uncertainty_notes:
            raise ValueError(
                f"AgentOutput from '{self.agent_name}' has empty uncertainty_notes. "
                "Every agent output must document its uncertainties — empty notes are a bug."
            )
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError(
                f"AgentOutput from '{self.agent_name}' has confidence_score="
                f"{self.confidence_score} which is outside the valid range [0.0, 1.0]."
            )
        # Coerce enum strings to enum values
        if isinstance(self.decision_type, str):
            self.decision_type = DecisionType(self.decision_type)
        if isinstance(self.policy_action, str):
            self.policy_action = PolicyAction(self.policy_action)


class BaseAgent(abc.ABC):
    """Abstract base class for all Autonomous PMO agents.

    Subclasses must implement run(). They must not:
    - Import from sibling agent folders
    - Call the policy engine directly
    - Self-assemble context (use context_assembly/assembler.py)
    - Write ad hoc Cypher (use knowledge_graph/query_service.py)
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name

    @abc.abstractmethod
    def run(self, data: AgentInput) -> AgentOutput:
        """Execute the agent's core reasoning logic.

        Args:
            data: Scoped context assembled by context_assembly/assembler.py.

        Returns:
            AgentOutput with non-empty uncertainty_notes and valid confidence_score.
        """
        ...

    def _make_sparsity_note(self, confidence: float, threshold: float = 0.5) -> Optional[str]:
        """Return a sparsity alert string when confidence is below threshold."""
        if confidence < threshold:
            return (
                f"Signal confidence {confidence:.2f} is below threshold {threshold}. "
                "Output based on degraded or sparse data — treat as indicative only."
            )
        return None
