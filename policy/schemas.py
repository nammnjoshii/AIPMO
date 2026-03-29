"""Policy object definitions for the Autonomous PMO policy engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agents.base_agent import PolicyAction


class ActionPolicy(BaseModel):
    """Maps a single action name to its allowed policy outcome."""
    action: str
    outcome: PolicyAction
    requires_audit: bool = False
    notes: Optional[str] = None


class ThresholdPolicy(BaseModel):
    """Threshold-based auto-escalation rule."""
    metric: str
    escalate_if_greater_than: Optional[float] = None
    approval_required_if_greater_than: Optional[float] = None
    notify_if_greater_than: Optional[float] = None
    sparsity_alert_if_less_than: Optional[float] = None


class ProjectPolicy(BaseModel):
    """Full policy config for a project or tenant.

    Loaded from YAML at policy/policies/{project_id}.yaml.
    """
    version: str
    scope: Literal["global", "portfolio", "project"]
    project_id: Optional[str] = None
    tenant_id: str = "default"
    actions: Dict[str, PolicyAction] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    unknown_action_default: PolicyAction = PolicyAction.DENY


class PolicyEvaluationResult(BaseModel):
    """Result returned by the policy engine for every proposed action."""
    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    agent_name: str
    action: str
    proposed_by: str
    policy_action: PolicyAction
    justification: str
    requires_human_approval: bool = False
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
