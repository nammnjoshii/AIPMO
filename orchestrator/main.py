"""LangGraph orchestrator entry point — T-060.

7-node LangGraph:
  ingest_signal → update_state → route_to_agents → evaluate_policy →
  execute_or_queue → generate_brief → log_audit

Conditional edge from evaluate_policy:
  DENY       → log_audit          (skip execution)
  ESCALATE   → human_review_queue → log_audit
  ALLOW/AUD  → execute_or_queue → generate_brief → log_audit

Usage:
    python -m orchestrator.main
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional, TypedDict

from langgraph.graph import END, StateGraph

from agents.base_agent import AgentInput, AgentOutput, PolicyAction
from audit.logger import AuditLogger
from context_assembly.assembler import assemble_context
from events.schemas.event_types import DeliveryEvent
from orchestrator.event_router import EventRouter
from orchestrator.human_review_queue import HumanReviewQueue
from policy.engine import PolicyEngine
from signal_quality.pipeline import SignalQualityPipeline
from state.canonical_state import CanonicalStateStore
from state.schemas import CanonicalProjectState

logger = logging.getLogger(__name__)

_SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")


# ---- State schema for the LangGraph graph ----

class PMOState(TypedDict, total=False):
    """State passed between LangGraph nodes."""
    raw_signal: Dict[str, Any]
    event: Optional[DeliveryEvent]
    qualified_signal: Any
    canonical_state: Optional[CanonicalProjectState]
    agent_input: Optional[AgentInput]
    agent_output: Optional[AgentOutput]
    policy_result: str        # PolicyAction value string
    review_item_id: Optional[str]
    audit_event_id: Optional[str]
    error: Optional[str]


# ---- Node implementations ----

async def ingest_signal(state: PMOState, pipeline: SignalQualityPipeline) -> PMOState:
    """Node 1: Run signal quality pipeline on raw signal."""
    raw = state.get("raw_signal", {})
    try:
        source = raw.get("source", "unknown")
        qs = pipeline.process(raw, source=source)
        return {**state, "event": qs.event, "qualified_signal": qs}
    except Exception as e:
        logger.error("ingest_signal failed: %s", e)
        return {**state, "error": str(e)}


async def update_state(state: PMOState, store: CanonicalStateStore) -> PMOState:
    """Node 2: Update canonical state from qualified signal."""
    event: DeliveryEvent = state.get("event")
    if not event or state.get("error"):
        return state

    try:
        canonical = await store.get(event.project_id)
        if not canonical:
            canonical = CanonicalProjectState(
                project_id=event.project_id,
                identity={  # type: ignore[arg-type]
                    "project_id": event.project_id,
                    "name": event.project_id,
                    "tenant_id": event.tenant_id,
                },
            )
            await store.upsert(canonical)
        return {**state, "canonical_state": canonical}
    except Exception as e:
        logger.error("update_state failed: %s", e)
        return {**state, "error": str(e)}


async def route_to_agents(
    state: PMOState,
    router: EventRouter,
) -> PMOState:
    """Node 3: Assemble context and route event to agent pipeline."""
    event = state.get("event")
    canonical = state.get("canonical_state")
    qs = state.get("qualified_signal")

    if not event or not canonical or not qs or state.get("error"):
        return state

    try:
        agent_input = assemble_context(event, canonical, qs, "program_director_agent", {})
        agent_output = router.route(agent_input)
        return {**state, "agent_input": agent_input, "agent_output": agent_output}
    except Exception as e:
        logger.error("route_to_agents failed: %s", e)
        return {**state, "error": str(e)}


async def evaluate_policy(
    state: PMOState,
    policy_engine: PolicyEngine,
) -> PMOState:
    """Node 4: Evaluate policy on agent output — fail closed on any error."""
    agent_output: AgentOutput = state.get("agent_output")
    event = state.get("event")

    if not agent_output or state.get("error"):
        return {**state, "policy_result": PolicyAction.DENY.value}

    try:
        result = policy_engine.evaluate(
            action=agent_output.recommendation or "generate_status_report",
            project_id=event.project_id if event else "unknown",
            agent_name=agent_output.agent_name,
        )
        return {**state, "policy_result": result.policy_action.value}
    except Exception as e:
        logger.error("evaluate_policy failed: %s — fail-closed (DENY)", e)
        return {**state, "policy_result": PolicyAction.DENY.value, "error": str(e)}


async def execute_or_queue(
    state: PMOState,
    review_queue: HumanReviewQueue,
) -> PMOState:
    """Node 5: Execute or route to human review queue based on policy result."""
    policy_result = state.get("policy_result", PolicyAction.DENY.value)
    agent_output: AgentOutput = state.get("agent_output")
    event = state.get("event")

    if policy_result == PolicyAction.ESCALATE.value and agent_output and event:
        try:
            item_id = await review_queue.enqueue(
                project_id=event.project_id,
                policy_action=policy_result,
                agent_name=agent_output.agent_name,
                recommendation=agent_output.recommendation or "",
                context={
                    "event_type": str(event.event_type),
                    "confidence_score": agent_output.confidence_score,
                    "policy_action": policy_result,
                },
            )
            return {**state, "review_item_id": item_id}
        except Exception as e:
            logger.error("execute_or_queue enqueue failed: %s", e)
            return {**state, "error": str(e)}

    # ALLOW / ALLOW_WITH_AUDIT — proceed to brief generation
    return state


async def generate_brief(state: PMOState) -> PMOState:
    """Node 6: The brief is already generated by the Communication Agent in route_to_agents."""
    # Brief is embedded in agent_output.extra — no additional work needed here.
    # This node is a checkpoint for future async brief delivery (email, Slack, etc.)
    agent_output = state.get("agent_output")
    if agent_output:
        brief_title = agent_output.extra.get("brief_title", "")
        if brief_title:
            logger.info("generate_brief: brief ready — '%s'", brief_title)
    return state


async def log_audit(
    state: PMOState,
    audit_logger: AuditLogger,
) -> PMOState:
    """Node 7: Always logs audit record — runs even on DENY path."""
    event = state.get("event")
    agent_output = state.get("agent_output")
    policy_result = state.get("policy_result", "unknown")

    project_id = event.project_id if event else "unknown"
    actor = agent_output.agent_name if agent_output else "orchestrator"
    action = "orchestrator_event_processed"
    confidence = agent_output.confidence_score if agent_output else 0.0
    error = state.get("error")

    inputs = [f"event_type={event.event_type}" if event else "event=None"]
    outputs = [f"policy_result={policy_result}"]
    if error:
        outputs.append(f"error={error}")
    if state.get("review_item_id"):
        outputs.append(f"review_item_id={state['review_item_id']}")

    try:
        event_id = await audit_logger.log(
            event_type="automated_action_executed",
            actor=actor,
            action=action,
            project_id=project_id,
            inputs=inputs,
            outputs=outputs,
            policy_result=policy_result,
            metadata={
                "confidence_score": confidence,
                "error": error,
            },
        )
        return {**state, "audit_event_id": event_id}
    except Exception as e:
        logger.error("log_audit failed: %s", e)
        return state


# ---- Graph construction ----

def _policy_router(state: PMOState) -> str:
    """Conditional edge: route based on policy_result."""
    policy_result = state.get("policy_result", PolicyAction.DENY.value)
    if policy_result == PolicyAction.DENY.value:
        return "log_audit"
    if policy_result == PolicyAction.ESCALATE.value:
        return "execute_or_queue"
    return "execute_or_queue"


def build_graph(
    pipeline: SignalQualityPipeline,
    store: CanonicalStateStore,
    router: EventRouter,
    policy_engine: PolicyEngine,
    review_queue: HumanReviewQueue,
    audit_logger: AuditLogger,
) -> StateGraph:
    """Construct and compile the 7-node LangGraph PMO graph."""
    graph = StateGraph(PMOState)

    # Add nodes with bound dependencies
    graph.add_node("ingest_signal", lambda s: asyncio.run(ingest_signal(s, pipeline)))
    graph.add_node("update_state", lambda s: asyncio.run(update_state(s, store)))
    graph.add_node("route_to_agents", lambda s: asyncio.run(route_to_agents(s, router)))
    graph.add_node("evaluate_policy", lambda s: asyncio.run(evaluate_policy(s, policy_engine)))
    graph.add_node("execute_or_queue", lambda s: asyncio.run(execute_or_queue(s, review_queue)))
    graph.add_node("generate_brief", lambda s: asyncio.run(generate_brief(s)))
    graph.add_node("log_audit", lambda s: asyncio.run(log_audit(s, audit_logger)))

    # Entry point
    graph.set_entry_point("ingest_signal")

    # Sequential edges
    graph.add_edge("ingest_signal", "update_state")
    graph.add_edge("update_state", "route_to_agents")
    graph.add_edge("route_to_agents", "evaluate_policy")

    # Conditional edge after policy evaluation
    graph.add_conditional_edges(
        "evaluate_policy",
        _policy_router,
        {
            "execute_or_queue": "execute_or_queue",
            "log_audit": "log_audit",
        },
    )

    graph.add_edge("execute_or_queue", "generate_brief")
    graph.add_edge("generate_brief", "log_audit")
    graph.add_edge("log_audit", END)

    return graph.compile()


async def create_pmo_app(db_path: Optional[str] = None):
    """Factory: create and initialize all dependencies for the PMO orchestrator."""
    db_path = db_path or _SQLITE_DB_PATH
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    store = CanonicalStateStore(db_path=db_path)
    await store.initialize()

    audit_logger = AuditLogger(db_path=db_path)
    await audit_logger.initialize()

    review_queue = HumanReviewQueue(db_path=db_path, audit_logger=audit_logger)
    await review_queue.initialize()

    pipeline = SignalQualityPipeline(redis_url=redis_url, state_store=store)
    policy_engine = PolicyEngine()
    try:
        policy_engine.load(os.path.join(os.path.dirname(__file__), "..", "configs", "policies.yaml"))
    except Exception as e:
        logger.warning("Could not load policies.yaml: %s — using defaults", e)

    router = EventRouter()

    app = build_graph(pipeline, store, router, policy_engine, review_queue, audit_logger)
    return app, {"store": store, "audit_logger": audit_logger, "review_queue": review_queue}


async def process_event(raw_signal: Dict[str, Any], app=None) -> PMOState:
    """Process a single raw signal through the full PMO pipeline.

    Args:
        raw_signal: Dict with event data.
        app: Pre-built LangGraph app (creates one if None).

    Returns:
        Final PMOState after all nodes have executed.
    """
    if app is None:
        app, _ = await create_pmo_app()

    initial_state: PMOState = {"raw_signal": raw_signal}
    result = await app.ainvoke(initial_state)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Autonomous PMO Orchestrator...")

    async def _main():
        app, deps = await create_pmo_app()
        logger.info("PMO Orchestrator ready. Waiting for events...")

    asyncio.run(_main())
