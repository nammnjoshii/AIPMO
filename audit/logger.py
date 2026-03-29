"""Audit logger — append-only structured audit records.

Records are written to SQLite (same store as canonical state) via an async
interface. The table has no UPDATE or DELETE operations — enforce append-only
at the application layer.

Logged event types (from README.md):
  signal_ingested, state_update_proposed, graph_update_proposed,
  policy_evaluated, recommendation_generated, human_override,
  automated_action_executed, evaluation_metric_updated

Usage:
    logger = AuditLogger(db_path=":memory:")
    await logger.initialize()
    await logger.log(
        event_type="policy_evaluated",
        actor="risk_intelligence_agent",
        action="escalate_issue",
        project_id="proj_123",
        inputs=["risk_score=0.55"],
        outputs=["ESCALATE"],
        policy_result="escalate",
    )
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

# Allowed audit event types (from README.md)
AUDIT_EVENT_TYPES = frozenset({
    "signal_ingested",
    "state_update_proposed",
    "graph_update_proposed",
    "policy_evaluated",
    "recommendation_generated",
    "human_override",
    "automated_action_executed",
    "evaluation_metric_updated",
})


class _AuditBase(DeclarativeBase):
    pass


class AuditRecord(_AuditBase):
    __tablename__ = "audit_log"

    event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    timestamp = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    project_id = Column(String, nullable=False)
    inputs_json = Column(Text, nullable=False, default="[]")
    outputs_json = Column(Text, nullable=False, default="[]")
    policy_result = Column(String, nullable=False)
    metadata_json = Column(Text, nullable=False, default="{}")


def _build_engine(db_path: str):
    if db_path == ":memory:":
        url = "sqlite+aiosqlite:///:memory:"
    else:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(url, echo=False)


class AuditLogger:
    """Append-only async audit logger backed by SQLite.

    Records are INSERT-only. Never call UPDATE or DELETE on audit_log.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _SQLITE_DB_PATH
        self._engine = _build_engine(self._db_path)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Create the audit_log table. Call once at startup."""
        async with self._engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(_AuditBase.metadata.create_all)
        self._initialized = True
        logger.info("AuditLogger initialized at %s", self._db_path)

    async def log(
        self,
        event_type: str,
        actor: str,
        action: str,
        project_id: str,
        inputs: List[str],
        outputs: List[str],
        policy_result: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append an audit record. Returns the event_id.

        Args:
            event_type: One of AUDIT_EVENT_TYPES.
            actor: Agent name or human user identifier.
            action: The action being audited.
            project_id: Scoping project.
            inputs: List of input descriptions.
            outputs: List of output descriptions.
            policy_result: The PolicyAction outcome string.
            metadata: Optional extra context dict.

        Returns:
            event_id (UUID string).
        """
        if event_type not in AUDIT_EVENT_TYPES:
            logger.warning(
                "AuditLogger: unknown event_type='%s' — logging anyway but "
                "check against AUDIT_EVENT_TYPES.",
                event_type,
            )

        event_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO audit_log "
                        "(event_id, event_type, timestamp, actor, action, project_id, "
                        "inputs_json, outputs_json, policy_result, metadata_json) "
                        "VALUES (:event_id, :event_type, :timestamp, :actor, :action, "
                        ":project_id, :inputs_json, :outputs_json, :policy_result, :metadata_json)"
                    ),
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "timestamp": now,
                        "actor": actor,
                        "action": action,
                        "project_id": project_id,
                        "inputs_json": json.dumps(inputs),
                        "outputs_json": json.dumps(outputs),
                        "policy_result": policy_result,
                        "metadata_json": json.dumps(metadata or {}),
                    },
                )

        logger.debug(
            "AuditLogger: logged event_id=%s event_type=%s actor=%s action=%s project=%s",
            event_id,
            event_type,
            actor,
            action,
            project_id,
        )
        return event_id

    async def get_recent(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent audit records for a project."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT event_id, event_type, timestamp, actor, action, "
                    "policy_result, inputs_json, outputs_json, metadata_json "
                    "FROM audit_log WHERE project_id = :project_id "
                    "ORDER BY timestamp DESC LIMIT :limit"
                ),
                {"project_id": project_id, "limit": limit},
            )
            rows = result.fetchall()

        records = []
        for row in rows:
            records.append({
                "event_id": row[0],
                "event_type": row[1],
                "timestamp": row[2],
                "actor": row[3],
                "action": row[4],
                "policy_result": row[5],
                "inputs": json.loads(row[6]),
                "outputs": json.loads(row[7]),
                "metadata": json.loads(row[8]),
            })
        return records

    async def close(self) -> None:
        await self._engine.dispose()
