"""Streamlit demo UI — T-089 / T-090 / T-091.

4 views:
  1. Portfolio Health   — color-coded health grid, auto-refresh every 60s
  2. Decision Queue     — approve/reject buttons via human_review_queue
  3. Risk Feed          — last 20 risk detections with confidence badges
  4. Explainability     — per-decision expandable evidence panel

Usage: streamlit run ui/app.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Add project root to path so imports work
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Autonomous PMO",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _health_color(score: Optional[float]) -> str:
    if score is None:
        return "gray"
    if score > 0.8:
        return "green"
    if score >= 0.5:
        return "yellow"
    return "red"


def _confidence_badge(score: Optional[float]) -> str:
    if score is None:
        return "⬜ UNKNOWN"
    if score > 0.75:
        return "🟢 HIGH"
    if score >= 0.5:
        return "🟡 MEDIUM"
    return "🔴 LOW"


def _run_async(coro):
    """Run an async coroutine from Streamlit's sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=10)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@st.cache_resource
def _get_state_store():
    from state.canonical_state import CanonicalStateStore
    store = CanonicalStateStore(db_path=_DB_PATH)
    _run_async(store.initialize())
    return store


@st.cache_resource
def _get_review_queue():
    from orchestrator.human_review_queue import HumanReviewQueue
    from audit.logger import AuditLogger
    audit_logger = AuditLogger(db_path=_DB_PATH)
    _run_async(audit_logger.initialize())
    q = HumanReviewQueue(db_path=_DB_PATH, audit_logger=audit_logger)
    _run_async(q.initialize())
    return q


@st.cache_resource
def _get_audit_logger():
    from audit.logger import AuditLogger
    logger = AuditLogger(db_path=_DB_PATH)
    _run_async(logger.initialize())
    return logger


def _load_all_projects() -> List[Dict[str, Any]]:
    """Load all projects from canonical state store."""
    try:
        store = _get_state_store()
        import aiosqlite

        async def _fetch():
            async with aiosqlite.connect(_DB_PATH) as db:
                async with db.execute(
                    "SELECT project_id, state_json FROM canonical_project_state"
                ) as cursor:
                    rows = await cursor.fetchall()
            results = []
            for row in rows:
                try:
                    data = json.loads(row[1])
                    results.append(data)
                except Exception:
                    results.append({"project_id": row[0]})
            return results

        return _run_async(_fetch()) or []
    except Exception as e:
        st.warning(f"Could not load projects: {e}")
        return []


def _load_pending_reviews() -> List[Dict[str, Any]]:
    """Load pending review queue items."""
    try:
        queue = _get_review_queue()

        async def _fetch():
            return await queue.get_pending()

        return _run_async(_fetch()) or []
    except Exception as e:
        st.warning(f"Could not load review queue: {e}")
        return []


def _load_risk_feed(limit: int = 20) -> List[Dict[str, Any]]:
    """Load recent risk detections from audit log."""
    try:
        async def _fetch():
            import aiosqlite
            async with aiosqlite.connect(_DB_PATH) as db:
                async with db.execute(
                    """
                    SELECT event_id, timestamp, actor, action, project_id,
                           outputs, policy_result
                    FROM audit_log
                    WHERE action IN ('risk.detected', 'dependency.blocked',
                                     'recommendation_generated')
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
            return [
                {
                    "event_id": r[0],
                    "timestamp": r[1],
                    "actor": r[2],
                    "action": r[3],
                    "project_id": r[4],
                    "outputs": r[5],
                    "policy_result": r[6],
                }
                for r in rows
            ]

        return _run_async(_fetch()) or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# View 1: Portfolio Health
# ---------------------------------------------------------------------------


def view_portfolio_health():
    st.header("Portfolio Health")
    st.caption("Auto-refreshes every 60 seconds.")

    projects = _load_all_projects()

    if not projects:
        st.info("No projects in the state store yet. Run the bootstrap script to add projects.")
        st.code("python -m integrations.github_issues.bootstrap")
        return

    # Summary metrics
    total = len(projects)
    healthy = sum(
        1 for p in projects
        if (p.get("health", {}) or {}).get("schedule_health", 0) > 0.8
    )
    at_risk = sum(
        1 for p in projects
        if 0.5 <= (p.get("health", {}) or {}).get("schedule_health", 0) <= 0.8
    )
    critical = total - healthy - at_risk

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Projects", total)
    col2.metric("Healthy (>80%)", healthy, delta=None)
    col3.metric("At Risk (50-80%)", at_risk)
    col4.metric("Critical (<50%)", critical)

    st.divider()

    # Per-project health cards
    cols = st.columns(3)
    for i, proj in enumerate(projects):
        health = proj.get("health", {}) or {}
        score = health.get("schedule_health")
        blockers = health.get("open_blockers", 0)
        proj_id = proj.get("project_id") or proj.get("identity", {}).get("project_id", "unknown")
        name = (proj.get("identity", {}) or {}).get("name", proj_id)

        color = _health_color(score)
        score_pct = f"{score * 100:.0f}%" if score is not None else "N/A"

        with cols[i % 3]:
            if color == "green":
                st.success(f"**{name}**\nHealth: {score_pct} | Blockers: {blockers}")
            elif color == "yellow":
                st.warning(f"**{name}**\nHealth: {score_pct} | Blockers: {blockers}")
            else:
                st.error(f"**{name}**\nHealth: {score_pct} | Blockers: {blockers}")

    # Auto-refresh
    st.markdown(
        """
        <script>
        setTimeout(function() { window.location.reload(); }, 60000);
        </script>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# View 2: Decision Queue
# ---------------------------------------------------------------------------


def view_decision_queue():
    st.header("Decision Queue")
    st.caption("Pending approvals. Approve or reject each item.")

    items = _load_pending_reviews()

    if not items:
        st.success("No pending decisions. All clear!")
        return

    queue = _get_review_queue()

    for item in items:
        item_id = item.get("item_id", "unknown")
        project_id = item.get("project_id", "")
        policy_action = item.get("policy_action", "")
        agent_name = item.get("agent_name", "")
        recommendation = item.get("recommendation", "No recommendation provided.")
        enqueued_at = item.get("enqueued_at", "")
        sla_hours = item.get("sla_hours", 24)

        # Compute age
        age_str = ""
        if enqueued_at:
            try:
                enqueued_dt = datetime.fromisoformat(enqueued_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - enqueued_dt).total_seconds() / 3600
                age_str = f"  ({age_hours:.1f}h old, SLA: {sla_hours}h)"
                if age_hours > sla_hours:
                    age_str = f" ⚠️ SLA BREACHED ({age_hours:.1f}h / {sla_hours}h)"
            except Exception:
                pass

        with st.expander(
            f"[{policy_action.upper()}] {project_id} — {agent_name}{age_str}",
            expanded=True,
        ):
            st.markdown(f"**Recommendation:** {recommendation}")
            st.caption(f"Item ID: `{item_id}`")

            col_approve, col_reject, _ = st.columns([1, 1, 3])
            with col_approve:
                if st.button("✅ Approve", key=f"approve_{item_id}"):
                    try:
                        _run_async(queue.approve(
                            item_id=item_id,
                            approved_by="ui_user",
                            note="Approved via Streamlit UI",
                        ))
                        st.success("Approved!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            with col_reject:
                if st.button("❌ Reject", key=f"reject_{item_id}"):
                    try:
                        _run_async(queue.reject(
                            item_id=item_id,
                            rejected_by="ui_user",
                            note="Rejected via Streamlit UI",
                        ))
                        st.warning("Rejected.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# View 3: Risk Feed
# ---------------------------------------------------------------------------


def view_risk_feed():
    st.header("Risk Feed")
    st.caption("Last 20 risk detections and escalations.")

    feed = _load_risk_feed(limit=20)

    if not feed:
        st.info("No risk events recorded yet.")
        return

    for item in feed:
        project_id = item.get("project_id", "")
        action = item.get("action", "")
        timestamp = item.get("timestamp", "")
        policy_result = item.get("policy_result", "")
        outputs_raw = item.get("outputs", "{}")

        # Parse outputs for confidence
        confidence = None
        try:
            outputs = json.loads(outputs_raw) if outputs_raw else {}
            confidence = outputs.get("confidence_score")
        except Exception:
            pass

        badge = _confidence_badge(confidence)

        # Color by policy result
        if policy_result in ("escalate", "approval_required", "deny"):
            border = "🔴"
        elif policy_result == "allow_with_audit":
            border = "🟡"
        else:
            border = "🟢"

        st.markdown(
            f"{border} **{project_id}** — `{action}` &nbsp; {badge} &nbsp; "
            f"<small>{timestamp}</small>",
            unsafe_allow_html=True,
        )
        if policy_result:
            st.caption(f"Policy outcome: `{policy_result}`")
        st.divider()


# ---------------------------------------------------------------------------
# View 4: Explainability Panel
# ---------------------------------------------------------------------------


def view_explainability():
    st.header("Explainability Panel")
    st.caption("Drill into the evidence behind each decision.")

    # Load recent audit events with agent outputs
    try:
        async def _fetch():
            import aiosqlite
            async with aiosqlite.connect(_DB_PATH) as db:
                async with db.execute(
                    """
                    SELECT event_id, timestamp, actor, action, project_id,
                           inputs, outputs, policy_result
                    FROM audit_log
                    ORDER BY timestamp DESC
                    LIMIT 50
                    """,
                ) as cursor:
                    rows = await cursor.fetchall()
            return rows

        rows = _run_async(_fetch()) or []
    except Exception as e:
        st.warning(f"Could not load audit records: {e}")
        return

    if not rows:
        st.info("No audit records yet. Process a sample event to see explainability data.")
        st.code("python -m examples.run_sample_event")
        return

    for row in rows:
        event_id = row[0]
        timestamp = row[1]
        actor = row[2]
        action = row[3]
        project_id = row[4]
        inputs_raw = row[5] or "{}"
        outputs_raw = row[6] or "{}"
        policy_result = row[7] or ""

        try:
            outputs = json.loads(outputs_raw)
        except Exception:
            outputs = {}

        confidence = outputs.get("confidence_score")
        uncertainty_notes = outputs.get("uncertainty_notes", [])
        recommendation = outputs.get("recommendation", "")
        evidence = outputs.get("evidence", [])

        badge = _confidence_badge(confidence)
        policy_display = policy_result.replace("_", " ").upper() if policy_result else "UNKNOWN"

        with st.expander(
            f"[{policy_display}] {project_id} — {action} {badge}",
            expanded=False,
        ):
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Triggering Evidence")
                if evidence:
                    for ev in evidence[:5]:
                        st.markdown(f"• {ev}")
                else:
                    try:
                        inputs = json.loads(inputs_raw)
                        event_type = inputs.get("event_type", "")
                        if event_type:
                            st.markdown(f"• Event type: `{event_type}`")
                    except Exception:
                        st.markdown("• No evidence recorded")

                st.subheader("Contributing Agent")
                st.code(actor or "unknown")

            with col2:
                st.subheader("Confidence")
                conf_str = f"{confidence:.2f}" if confidence is not None else "N/A"
                st.metric("Score", conf_str)

                st.subheader("Uncertainty Notes")
                if uncertainty_notes:
                    for note in uncertainty_notes:
                        st.markdown(f"⚠️ {note}")
                else:
                    st.caption("No uncertainty notes recorded.")

            if recommendation:
                st.subheader("Recommended Next Step")
                st.info(recommendation)

            st.subheader("Policy Outcome")
            st.markdown(f"`{policy_result or 'unknown'}`")
            st.caption(f"Event ID: `{event_id}` | {timestamp}")


# ---------------------------------------------------------------------------
# Main navigation
# ---------------------------------------------------------------------------


def main():
    st.sidebar.title("Autonomous PMO")
    st.sidebar.caption("Decision preparation infrastructure")
    st.sidebar.divider()

    view = st.sidebar.radio(
        "View",
        options=[
            "Portfolio Health",
            "Decision Queue",
            "Risk Feed",
            "Explainability",
        ],
        index=0,
    )

    st.sidebar.divider()
    st.sidebar.caption(f"DB: `{os.path.basename(_DB_PATH)}`")
    if st.sidebar.button("🔄 Refresh"):
        st.rerun()

    if view == "Portfolio Health":
        view_portfolio_health()
    elif view == "Decision Queue":
        view_decision_queue()
    elif view == "Risk Feed":
        view_risk_feed()
    elif view == "Explainability":
        view_explainability()


if __name__ == "__main__":
    main()
