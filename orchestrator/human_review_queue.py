"""Human review queue — T-063.

SQLite-backed queue for APPROVAL_REQUIRED and ESCALATE actions.
All approve/reject writes produce audit records.
Includes SLA tracking with enqueued_at and sla_hours fields.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import Column, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")
_DEFAULT_SLA_HOURS = 24


class _QueueBase(DeclarativeBase):
    pass


class ReviewItem(_QueueBase):
    __tablename__ = "human_review_queue"

    item_id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False)
    policy_action = Column(String, nullable=False)
    enqueued_at = Column(String, nullable=False)
    sla_hours = Column(Integer, nullable=False, default=_DEFAULT_SLA_HOURS)
    status = Column(String, nullable=False, default="pending")  # pending | approved | rejected
    agent_name = Column(String, nullable=False)
    recommendation = Column(Text, nullable=False, default="")
    context_json = Column(Text, nullable=False, default="{}")
    resolved_at = Column(String, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolution_note = Column(Text, nullable=True)


def _build_engine(db_path: str):
    if db_path == ":memory:":
        url = "sqlite+aiosqlite:///:memory:"
    else:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(url, echo=False)


class HumanReviewQueue:
    """Manages the queue of decisions requiring human approval.

    Approve/reject actions write audit records via the provided audit_logger.
    SLA tracking: items older than sla_hours trigger escalation alerts.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        self._db_path = db_path or _SQLITE_DB_PATH
        self._engine = _build_engine(self._db_path)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._audit_logger = audit_logger

    async def initialize(self) -> None:
        """Create queue table. Call once at startup."""
        async with self._engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(_QueueBase.metadata.create_all)
        logger.info("HumanReviewQueue initialized at %s", self._db_path)

    async def enqueue(
        self,
        project_id: str,
        policy_action: str,
        agent_name: str,
        recommendation: str = "",
        context: Optional[Dict[str, Any]] = None,
        sla_hours: int = _DEFAULT_SLA_HOURS,
    ) -> str:
        """Add a decision item to the human review queue.

        Returns:
            item_id (UUID string).
        """
        item_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO human_review_queue "
                        "(item_id, project_id, policy_action, enqueued_at, sla_hours, "
                        "status, agent_name, recommendation, context_json) "
                        "VALUES (:item_id, :project_id, :policy_action, :enqueued_at, "
                        ":sla_hours, :status, :agent_name, :recommendation, :context_json)"
                    ),
                    {
                        "item_id": item_id,
                        "project_id": project_id,
                        "policy_action": policy_action,
                        "enqueued_at": now,
                        "sla_hours": sla_hours,
                        "status": "pending",
                        "agent_name": agent_name,
                        "recommendation": recommendation or "",
                        "context_json": json.dumps(context or {}),
                    },
                )

        logger.info(
            "HumanReviewQueue: enqueued item_id=%s project=%s policy=%s",
            item_id, project_id, policy_action,
        )
        return item_id

    async def dequeue(self, project_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Return pending review items, optionally filtered by project_id."""
        async with self._session_factory() as session:
            if project_id:
                result = await session.execute(
                    text(
                        "SELECT item_id, project_id, policy_action, enqueued_at, sla_hours, "
                        "status, agent_name, recommendation, context_json "
                        "FROM human_review_queue WHERE status='pending' AND project_id=:pid "
                        "ORDER BY enqueued_at ASC LIMIT :limit"
                    ),
                    {"pid": project_id, "limit": limit},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT item_id, project_id, policy_action, enqueued_at, sla_hours, "
                        "status, agent_name, recommendation, context_json "
                        "FROM human_review_queue WHERE status='pending' "
                        "ORDER BY enqueued_at ASC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            rows = result.fetchall()

        return [
            {
                "item_id": r[0],
                "project_id": r[1],
                "policy_action": r[2],
                "enqueued_at": r[3],
                "sla_hours": r[4],
                "status": r[5],
                "agent_name": r[6],
                "recommendation": r[7],
                "context": json.loads(r[8]),
            }
            for r in rows
        ]

    async def approve(self, item_id: str, approver: str, note: str = "") -> bool:
        """Approve a review item and write an audit record.

        Returns:
            True if item was found and updated, False if not found.
        """
        return await self._resolve(item_id, "approved", approver, note)

    async def reject(self, item_id: str, approver: str, note: str = "") -> bool:
        """Reject a review item and write an audit record.

        Returns:
            True if item was found and updated, False if not found.
        """
        return await self._resolve(item_id, "rejected", approver, note)

    async def get_sla_breached(self) -> List[Dict[str, Any]]:
        """Return items that have exceeded their SLA."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT item_id, project_id, policy_action, enqueued_at, sla_hours, "
                    "agent_name, recommendation "
                    "FROM human_review_queue WHERE status='pending'"
                )
            )
            rows = result.fetchall()

        breached = []
        for r in rows:
            enqueued = datetime.fromisoformat(r[3])
            elapsed_hours = (now - enqueued).total_seconds() / 3600
            if elapsed_hours > r[4]:
                breached.append({
                    "item_id": r[0],
                    "project_id": r[1],
                    "policy_action": r[2],
                    "enqueued_at": r[3],
                    "sla_hours": r[4],
                    "elapsed_hours": round(elapsed_hours, 1),
                    "agent_name": r[5],
                    "recommendation": r[6],
                })
        return breached

    async def close(self) -> None:
        await self._engine.dispose()

    # ---- Internal ----

    async def _resolve(self, item_id: str, status: str, resolver: str, note: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT item_id, project_id, policy_action, agent_name "
                        "FROM human_review_queue WHERE item_id=:item_id AND status='pending'"
                    ),
                    {"item_id": item_id},
                )
                row = result.fetchone()
                if not row:
                    return False

                await session.execute(
                    text(
                        "UPDATE human_review_queue SET status=:status, resolved_at=:resolved_at, "
                        "resolved_by=:resolved_by, resolution_note=:note "
                        "WHERE item_id=:item_id"
                    ),
                    {
                        "status": status,
                        "resolved_at": now,
                        "resolved_by": resolver,
                        "note": note,
                        "item_id": item_id,
                    },
                )

        # Write audit record
        if self._audit_logger:
            try:
                await self._audit_logger.log(
                    event_type="human_override",
                    actor=resolver,
                    action=f"review_{status}",
                    project_id=row[1],
                    inputs=[f"item_id={item_id}", f"policy_action={row[2]}"],
                    outputs=[f"status={status}", f"note={note}"],
                    policy_result=status,
                    metadata={"item_id": item_id, "agent_name": row[3]},
                )
            except Exception as e:
                logger.warning("HumanReviewQueue: audit log failed for item %s: %s", item_id, e)

        logger.info(
            "HumanReviewQueue: item_id=%s %s by %s",
            item_id, status, resolver,
        )
        return True
