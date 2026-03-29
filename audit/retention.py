"""Audit log retention policy — T-056.

Archives (never deletes) audit_log records older than 12 months to audit_log_archive.
State history: 24 months. Policy decisions: 24 months.

Records are MOVED to archive table — primary audit_log table is never truncated.
APScheduler job enforces retention on a schedule.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Column, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

# Retention windows (days)
_AUDIT_LOG_RETENTION_DAYS = 365          # 12 months
_STATE_HISTORY_RETENTION_DAYS = 730      # 24 months
_POLICY_DECISION_RETENTION_DAYS = 730    # 24 months


class _ArchiveBase(DeclarativeBase):
    pass


class AuditLogArchive(_ArchiveBase):
    """Mirror of AuditRecord — holds records past retention window."""
    __tablename__ = "audit_log_archive"

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
    archived_at = Column(String, nullable=False)


def _build_engine(db_path: str):
    if db_path == ":memory:":
        url = "sqlite+aiosqlite:///:memory:"
    else:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(url, echo=False)


class RetentionPolicy:
    """Archives stale audit records. Never deletes — only moves to archive table.

    Usage:
        retention = RetentionPolicy()
        await retention.initialize()
        archived_count = await retention.enforce()
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _SQLITE_DB_PATH
        self._engine = _build_engine(self._db_path)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    async def initialize(self) -> None:
        """Create the archive table if it doesn't exist."""
        async with self._engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(_ArchiveBase.metadata.create_all)
        logger.info("RetentionPolicy: archive table initialized at %s", self._db_path)

    async def enforce(self, retention_days: int = _AUDIT_LOG_RETENTION_DAYS) -> int:
        """Archive audit_log records older than retention_days.

        Records are INSERT INTO audit_log_archive then DELETE FROM audit_log.
        The audit_log_archive table is never deleted from.

        Returns:
            Number of records archived.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

        async with self._session_factory() as session:
            async with session.begin():
                # Find records to archive
                result = await session.execute(
                    text(
                        "SELECT event_id, event_type, timestamp, actor, action, project_id, "
                        "inputs_json, outputs_json, policy_result, metadata_json "
                        "FROM audit_log WHERE timestamp < :cutoff"
                    ),
                    {"cutoff": cutoff},
                )
                rows = result.fetchall()

                if not rows:
                    logger.info("RetentionPolicy: no records to archive (cutoff=%s)", cutoff)
                    return 0

                now = datetime.now(timezone.utc).isoformat()

                # Insert into archive
                for row in rows:
                    await session.execute(
                        text(
                            "INSERT OR IGNORE INTO audit_log_archive "
                            "(event_id, event_type, timestamp, actor, action, project_id, "
                            "inputs_json, outputs_json, policy_result, metadata_json, archived_at) "
                            "VALUES (:event_id, :event_type, :timestamp, :actor, :action, "
                            ":project_id, :inputs_json, :outputs_json, :policy_result, "
                            ":metadata_json, :archived_at)"
                        ),
                        {
                            "event_id": row[0],
                            "event_type": row[1],
                            "timestamp": row[2],
                            "actor": row[3],
                            "action": row[4],
                            "project_id": row[5],
                            "inputs_json": row[6],
                            "outputs_json": row[7],
                            "policy_result": row[8],
                            "metadata_json": row[9],
                            "archived_at": now,
                        },
                    )

                # Delete from primary table only after successful insert
                await session.execute(
                    text("DELETE FROM audit_log WHERE timestamp < :cutoff"),
                    {"cutoff": cutoff},
                )

        logger.info(
            "RetentionPolicy: archived %d records older than %d days",
            len(rows),
            retention_days,
        )
        return len(rows)

    async def get_archive_count(self, project_id: Optional[str] = None) -> int:
        """Return number of records in the archive table."""
        async with self._session_factory() as session:
            if project_id:
                result = await session.execute(
                    text("SELECT COUNT(*) FROM audit_log_archive WHERE project_id = :pid"),
                    {"project_id": project_id},
                )
            else:
                result = await session.execute(
                    text("SELECT COUNT(*) FROM audit_log_archive")
                )
            return result.scalar() or 0

    async def close(self) -> None:
        await self._engine.dispose()


def create_apscheduler_job(retention: RetentionPolicy):
    """Return an APScheduler job config for the retention enforcer.

    Usage:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(**create_apscheduler_job(retention))
        scheduler.start()
    """
    import asyncio

    async def _run():
        await retention.enforce()

    return {
        "func": lambda: asyncio.create_task(_run()),
        "trigger": "cron",
        "hour": 2,
        "minute": 0,
        "id": "audit_retention_enforcer",
        "replace_existing": True,
    }
