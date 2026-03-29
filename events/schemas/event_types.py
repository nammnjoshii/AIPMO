"""Event type definitions for the Autonomous PMO event bus."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    TASK_UPDATED = "task.updated"
    MILESTONE_UPDATED = "milestone.updated"
    RISK_DETECTED = "risk.detected"
    DEPENDENCY_BLOCKED = "dependency.blocked"
    STATUS_REPORTED = "status.reported"
    CAPACITY_CHANGED = "capacity.changed"
    SIGNAL_QUALIFIED = "signal.qualified"


class DeliveryEvent(BaseModel):
    """Base event model. All events on the bus extend this."""
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    project_id: str
    source: str  # github_issues | google_sheets | github | manual
    tenant_id: str = "default"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Dict[str, Any] = Field(default_factory=dict)
    signal_quality: Optional[Dict[str, Any]] = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = {"use_enum_values": True}


class TaskUpdatedPayload(BaseModel):
    task_id: str
    task_name: str
    previous_status: Optional[str] = None
    new_status: str
    assignee: Optional[str] = None
    blocked_by: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("updated_at", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class MilestoneUpdatedPayload(BaseModel):
    milestone_id: str
    milestone_name: str
    previous_status: Optional[str] = None
    new_status: str
    due_date: datetime
    completion_percentage: float = Field(ge=0.0, le=1.0, default=0.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("due_date", "updated_at", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class RiskDetectedPayload(BaseModel):
    risk_id: str
    risk_title: str
    risk_description: str
    probability: float = Field(ge=0.0, le=1.0)
    impact: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    affected_milestones: List[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("detected_at", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class DependencyBlockedPayload(BaseModel):
    blocking_task_id: str
    blocked_task_id: str
    blocking_project_id: Optional[str] = None
    severity: float = Field(ge=0.0, le=1.0, default=0.5)
    affected_milestones: List[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("detected_at", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
