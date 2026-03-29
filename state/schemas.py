"""Canonical state Pydantic v2 models for Autonomous PMO."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectIdentity(BaseModel):
    project_id: str
    name: str
    tenant_id: str = "default"
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": False}


class Milestone(BaseModel):
    milestone_id: str
    name: str
    due_date: datetime
    status: Literal["on_track", "at_risk", "delayed", "complete"]
    completion_percentage: float = Field(ge=0.0, le=1.0, default=0.0)
    dependencies: List[str] = Field(default_factory=list)

    @field_validator("due_date", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class HealthMetrics(BaseModel):
    schedule_health: float = Field(ge=0.0, le=1.0, default=1.0)
    resource_health: float = Field(ge=0.0, le=1.0, default=1.0)
    scope_health: float = Field(ge=0.0, le=1.0, default=1.0)
    dependency_health: float = Field(ge=0.0, le=1.0, default=1.0)
    overall_health: float = Field(ge=0.0, le=1.0, default=1.0)
    open_blockers: int = Field(ge=0, default=0)
    tasks_completed: int = Field(ge=0, default=0)
    tasks_total: int = Field(ge=0, default=0)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceReliabilityProfile(BaseModel):
    source_name: str
    reliability_score: Literal["high", "medium", "low"] = "medium"
    last_accurate_signal: Optional[datetime] = None
    inaccuracy_count: int = Field(ge=0, default=0)
    total_signals: int = Field(ge=0, default=0)

    @field_validator("last_accurate_signal", mode="before")
    @classmethod
    def ensure_timezone_optional(cls, v: Any) -> Optional[datetime]:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class DecisionRecord(BaseModel):
    decision_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str
    decision_type: str
    summary: str
    policy_action: str
    approved_by: Optional[str] = None


class CanonicalProjectState(BaseModel):
    project_id: str
    identity: ProjectIdentity
    milestones: List[Milestone] = Field(default_factory=list)
    health: HealthMetrics = Field(default_factory=HealthMetrics)
    source_profiles: Dict[str, SourceReliabilityProfile] = Field(default_factory=dict)
    decision_history: List[DecisionRecord] = Field(default_factory=list)
    last_signal_at: Optional[datetime] = None
    version: int = Field(ge=0, default=0)

    @field_validator("last_signal_at", mode="before")
    @classmethod
    def ensure_timezone_last_signal(cls, v: Any) -> Optional[datetime]:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def model_dump_json_safe(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return self.model_dump(mode="json")
