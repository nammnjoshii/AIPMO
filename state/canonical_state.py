"""Canonical state store — SQLite + aiosqlite via SQLAlchemy async.

WAL mode enabled for concurrent reads alongside writes.
INSERT OR REPLACE idempotency pattern throughout.
Database auto-created at SQLITE_DB_PATH on first run.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Integer, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from state.schemas import CanonicalProjectState, DecisionRecord, HealthMetrics

logger = logging.getLogger(__name__)

_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")


def _build_engine(db_path: str):
    """Create async SQLAlchemy engine for the given SQLite path."""
    if db_path == ":memory:":
        url = "sqlite+aiosqlite:///:memory:"
    else:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(url, echo=False)


class Base(DeclarativeBase):
    pass


class ProjectStateRow(Base):
    __tablename__ = "canonical_project_state"

    project_id = Column(String, primary_key=True)
    state_json = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=0)
    updated_at = Column(String, nullable=False)


class CanonicalStateStore:
    """Async SQLite-backed store for CanonicalProjectState.

    All writes use INSERT OR REPLACE for idempotency.
    WAL journal mode is enabled on every connection for concurrent reads.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _SQLITE_DB_PATH
        self._engine = _build_engine(self._db_path)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables and enable WAL mode. Call once at startup."""
        async with self._engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.run_sync(Base.metadata.create_all)
        self._initialized = True
        logger.info("CanonicalStateStore initialized at %s", self._db_path)

    async def get(self, project_id: str) -> Optional[CanonicalProjectState]:
        """Return the CanonicalProjectState for project_id, or None if not found."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectStateRow).where(ProjectStateRow.project_id == project_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return CanonicalProjectState.model_validate_json(row.state_json)

    async def upsert(self, state: CanonicalProjectState) -> None:
        """Insert or replace the full project state. Idempotent."""
        state_json = state.model_dump_json()
        now = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT OR REPLACE INTO canonical_project_state "
                        "(project_id, state_json, version, updated_at) "
                        "VALUES (:project_id, :state_json, :version, :updated_at)"
                    ),
                    {
                        "project_id": state.project_id,
                        "state_json": state_json,
                        "version": state.version,
                        "updated_at": now,
                    },
                )
        logger.debug("Upserted state for project %s (version=%d)", state.project_id, state.version)

    async def update_health(self, project_id: str, health_updates: Dict[str, Any]) -> bool:
        """Patch only the health fields without overwriting other state.

        Returns True if the project was found and updated, False if not found.
        """
        state = await self.get(project_id)
        if state is None:
            logger.warning("update_health: project %s not found", project_id)
            return False

        current_health = state.health.model_dump()
        current_health.update(
            {k: v for k, v in health_updates.items() if k in current_health}
        )
        state.health = HealthMetrics(**current_health)
        state.version += 1
        await self.upsert(state)
        return True

    async def append_decision(self, project_id: str, decision: DecisionRecord) -> bool:
        """Append a decision record to the project's decision_history.

        Returns True if successful, False if project not found.
        """
        state = await self.get(project_id)
        if state is None:
            logger.warning("append_decision: project %s not found", project_id)
            return False

        state.decision_history.append(decision)
        state.version += 1
        await self.upsert(state)
        return True

    async def list_projects(self) -> List[str]:
        """Return all project IDs in the store."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectStateRow.project_id)
            )
            return [row[0] for row in result.fetchall()]

    async def close(self) -> None:
        """Dispose the engine and release connections."""
        await self._engine.dispose()
