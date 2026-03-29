"""Per-tenant token budget tracking — Enterprise Hardening.

Tracks LLM token usage per tenant and agent. Raises BudgetExceededError
when a tenant's monthly cap is breached. Emits WARNING at 80% utilization.

Tokens are counted locally (no API cost). Usage is persisted to SQLite in the
same autonomous_pmo.db file used by audit and canonical state.

OQ-003 context (DECISIONS.md): hard cap vs soft alert is still open. This
implementation defaults to HARD CAP (BudgetExceededError) but respects
TOKEN_BUDGET_MODE=soft to downgrade to WARNING-only for teams that prefer
availability over strict spend control.

Config via environment variables:
    TOKEN_BUDGET_MODE             (default: hard — raises on breach; set "soft" for alert-only)
    TOKEN_BUDGET_DEFAULT_MONTHLY  (default: 1_000_000 — tokens/month per tenant)
    SQLITE_DB_PATH                (default: ./data/autonomous_pmo.db)

Usage:
    budget = TokenBudgetTracker()
    await budget.initialize()

    # Before each LLM call:
    await budget.check(tenant_id="fh_001", agent_name="risk_intelligence_agent", estimated_tokens=2000)

    # After each LLM call (actual usage from API response):
    await budget.record(tenant_id="fh_001", agent_name="risk_intelligence_agent",
                        prompt_tokens=1200, completion_tokens=800)

    # Report:
    report = await budget.get_report(tenant_id="fh_001")
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_MONTHLY_CAP = int(os.environ.get("TOKEN_BUDGET_DEFAULT_MONTHLY", "1000000"))
_BUDGET_MODE = os.environ.get("TOKEN_BUDGET_MODE", "hard").lower()
_WARN_THRESHOLD = 0.80   # warn at 80% of monthly cap
_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")


class BudgetExceededError(Exception):
    """Raised when a tenant's monthly token budget is exhausted (hard mode only)."""

    def __init__(self, tenant_id: str, used: int, cap: int) -> None:
        self.tenant_id = tenant_id
        self.used = used
        self.cap = cap
        super().__init__(
            f"Token budget exceeded for tenant={tenant_id}: "
            f"used={used:,} cap={cap:,} ({used / cap:.1%})"
        )


class TokenBudgetTracker:
    """Tracks LLM token usage per tenant and emits budget alerts.

    Thread-safe for async concurrent use — each operation opens a short-lived
    aiosqlite connection (same WAL-mode DB used by the rest of the platform).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        monthly_cap: int = _DEFAULT_MONTHLY_CAP,
        mode: str = _BUDGET_MODE,
    ) -> None:
        self._db_path = db_path or _SQLITE_DB_PATH
        self._monthly_cap = monthly_cap
        self._mode = mode  # "hard" | "soft"

    async def initialize(self) -> None:
        """Create the token_usage table if it does not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id   TEXT    NOT NULL,
                    agent_name  TEXT    NOT NULL,
                    year_month  TEXT    NOT NULL,   -- YYYY-MM
                    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
                    completion_tokens  INTEGER NOT NULL DEFAULT 0,
                    total_tokens       INTEGER NOT NULL DEFAULT 0,
                    call_count         INTEGER NOT NULL DEFAULT 0,
                    recorded_at        TEXT    NOT NULL,
                    UNIQUE (tenant_id, agent_name, year_month)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_month
                ON token_usage (tenant_id, year_month)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tenant_token_caps (
                    tenant_id       TEXT PRIMARY KEY,
                    monthly_cap     INTEGER NOT NULL,
                    updated_at      TEXT NOT NULL
                )
            """)
            await db.commit()
        logger.debug("TokenBudgetTracker: tables initialized at %s", self._db_path)

    async def set_cap(self, tenant_id: str, monthly_cap: int) -> None:
        """Override the monthly token cap for a specific tenant."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                INSERT INTO tenant_token_caps (tenant_id, monthly_cap, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET monthly_cap=excluded.monthly_cap,
                                                     updated_at=excluded.updated_at
            """, (tenant_id, monthly_cap, now))
            await db.commit()
        logger.info(
            "TokenBudgetTracker: set cap for tenant=%s → %s tokens/month",
            tenant_id,
            f"{monthly_cap:,}",
        )

    async def check(
        self,
        tenant_id: str,
        agent_name: str,
        estimated_tokens: int = 0,
    ) -> None:
        """Check whether a tenant has budget remaining for an LLM call.

        Args:
            tenant_id: Tenant making the call.
            agent_name: Agent initiating the LLM call.
            estimated_tokens: Optional pre-call estimate (for early rejection).

        Raises:
            BudgetExceededError: In hard mode if used + estimated > cap.
        """
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        used = await self._get_monthly_total(tenant_id, year_month)
        cap = await self._get_cap(tenant_id)

        projected = used + estimated_tokens
        utilization = projected / cap if cap > 0 else 1.0

        if utilization >= 1.0:
            if self._mode == "hard":
                raise BudgetExceededError(tenant_id=tenant_id, used=projected, cap=cap)
            else:
                logger.warning(
                    "TokenBudgetTracker [SOFT]: tenant=%s has exceeded monthly cap "
                    "used=%s cap=%s (%.1f%%) — agent=%s proceeding (soft mode)",
                    tenant_id,
                    f"{projected:,}",
                    f"{cap:,}",
                    utilization * 100,
                    agent_name,
                )
        elif utilization >= _WARN_THRESHOLD:
            logger.warning(
                "TokenBudgetTracker: tenant=%s at %.1f%% of monthly cap "
                "used=%s cap=%s — agent=%s",
                tenant_id,
                utilization * 100,
                f"{used:,}",
                f"{cap:,}",
                agent_name,
            )

    async def record(
        self,
        tenant_id: str,
        agent_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Dict[str, Any]:
        """Record actual token usage after an LLM call completes.

        Uses UPSERT to accumulate tokens across calls within the same month.
        Never raises — recording failure should never block the caller.

        Returns:
            Dict with current monthly totals for the tenant.
        """
        total = prompt_tokens + completion_tokens
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        now = datetime.now(timezone.utc).isoformat()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("""
                    INSERT INTO token_usage
                        (tenant_id, agent_name, year_month,
                         prompt_tokens, completion_tokens, total_tokens,
                         call_count, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(tenant_id, agent_name, year_month) DO UPDATE SET
                        prompt_tokens     = prompt_tokens     + excluded.prompt_tokens,
                        completion_tokens = completion_tokens + excluded.completion_tokens,
                        total_tokens      = total_tokens      + excluded.total_tokens,
                        call_count        = call_count        + 1,
                        recorded_at       = excluded.recorded_at
                """, (tenant_id, agent_name, year_month,
                      prompt_tokens, completion_tokens, total, now))
                await db.commit()

            logger.debug(
                "TokenBudgetTracker: recorded tenant=%s agent=%s "
                "prompt=%s completion=%s total=%s",
                tenant_id, agent_name, prompt_tokens, completion_tokens, total,
            )

            monthly_total = await self._get_monthly_total(tenant_id, year_month)
            cap = await self._get_cap(tenant_id)
            return {
                "tenant_id": tenant_id,
                "year_month": year_month,
                "monthly_total": monthly_total,
                "monthly_cap": cap,
                "utilization": monthly_total / cap if cap > 0 else 0.0,
            }

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "TokenBudgetTracker: failed to record usage tenant=%s agent=%s: %s",
                tenant_id,
                agent_name,
                exc,
            )
            return {}

    async def get_report(self, tenant_id: str) -> Dict[str, Any]:
        """Return full token usage report for a tenant this calendar month.

        Returns:
            Dict with per-agent breakdown, total, cap, and utilization %.
        """
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        cap = await self._get_cap(tenant_id)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT agent_name,
                       SUM(prompt_tokens)     AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(total_tokens)      AS total_tokens,
                       SUM(call_count)        AS call_count
                FROM   token_usage
                WHERE  tenant_id = ? AND year_month = ?
                GROUP  BY agent_name
                ORDER  BY total_tokens DESC
            """, (tenant_id, year_month))
            rows = await cursor.fetchall()

        agents = []
        grand_total = 0
        for row in rows:
            agents.append({
                "agent_name": row["agent_name"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "call_count": row["call_count"],
            })
            grand_total += row["total_tokens"]

        utilization = grand_total / cap if cap > 0 else 0.0
        status = (
            "EXCEEDED" if utilization >= 1.0
            else "WARNING" if utilization >= _WARN_THRESHOLD
            else "OK"
        )

        return {
            "tenant_id": tenant_id,
            "year_month": year_month,
            "monthly_cap": cap,
            "total_tokens_used": grand_total,
            "utilization_pct": round(utilization * 100, 1),
            "status": status,
            "mode": self._mode,
            "agents": agents,
        }

    # ---- Internal ----

    async def _get_monthly_total(self, tenant_id: str, year_month: str) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("""
                SELECT COALESCE(SUM(total_tokens), 0)
                FROM   token_usage
                WHERE  tenant_id = ? AND year_month = ?
            """, (tenant_id, year_month))
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def _get_cap(self, tenant_id: str) -> int:
        """Return the per-tenant cap, falling back to the default."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT monthly_cap FROM tenant_token_caps WHERE tenant_id = ?",
                (tenant_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else self._monthly_cap
