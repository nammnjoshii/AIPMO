"""
Pipeline A/B Benchmark
======================
Test A — Current pipeline: all 7 agents run as rule-based deterministic logic.
         No LLM calls. This is what the 343 passing tests validate.

Test B — LLM-enriched pipeline: same rule-based agents PLUS an LLM enrichment
         pass on the three lightweight-tier agents (communication, knowledge,
         execution_monitoring) using locally available 3B models via Ollama.
         Model assignments:
           communication_agent      → phi3:mini
           knowledge_agent          → phi3:mini
           execution_monitoring_agent → phi3:mini
           All complex-tier agents  → qwen2.5-coder:3b (best structured output)

Metrics captured per run:
  - Latency (ms): per agent + total pipeline
  - Output schema compliance (% required fields populated)
  - Narrative richness (word count of communication brief body)
  - LLM token usage + tokens/sec (Test B only)
  - RAM delta (MB) between A and B

Run:
  python3 benchmarks/pipeline_ab_benchmark.py

No imports from outside this project. No API keys required.
Ollama must be running at http://localhost:11434
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Make project root importable ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

from agents.base_agent import AgentInput, AgentOutput
from agents.communication.agent import CommunicationAgent
from agents.execution_monitoring.agent import ExecutionMonitoringAgent
from agents.issue_management.agent import IssueManagementAgent
from agents.knowledge.agent import KnowledgeAgent
from agents.planning.agent import PlanningAgent
from agents.program_director.agent import ProgramDirectorAgent
from agents.risk_intelligence.agent import RiskIntelligenceAgent

# ── Test scenarios (5 representative delivery states) ────────────────────────

SCENARIOS = [
    {
        "name": "blocked_dependency",
        "project_id": "proj_001",
        "event_type": "dependency.blocked",
        "canonical_state": {
            "health": {"schedule_health": 0.45, "open_blockers": 3},
            "milestones": [
                {"milestone_id": "m1", "name": "Beta Release", "status": "at_risk"},
                {"milestone_id": "m2", "name": "GA Launch",    "status": "delayed"},
            ],
            "tasks": [{"task_id": "t1", "status": "blocked", "assigned_team": "team_alpha"}],
        },
        "signal_quality": {"confidence_score": 0.80, "is_decayed": False, "sparsity_alert": None},
        "expected_policy": "escalate",
    },
    {
        "name": "capacity_overload",
        "project_id": "proj_002",
        "event_type": "resource.overloaded",
        "canonical_state": {
            "health": {"schedule_health": 0.60, "open_blockers": 1},
            "milestones": [
                {"milestone_id": "m3", "name": "Sprint 8", "status": "on_track"},
            ],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.70, "is_decayed": False, "sparsity_alert": None},
        "expected_policy": "approval_required",
    },
    {
        "name": "silent_scope_creep",
        "project_id": "proj_003",
        "event_type": "scope.change_detected",
        "canonical_state": {
            "health": {"schedule_health": 0.72, "open_blockers": 0},
            "milestones": [
                {"milestone_id": "m4", "name": "Phase 2 Kickoff", "status": "on_track"},
            ],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.55, "is_decayed": True, "sparsity_alert": "stale_velocity"},
        "expected_policy": "allow_with_audit",
    },
    {
        "name": "low_confidence_signal",
        "project_id": "proj_004",
        "event_type": "status.stale",
        "canonical_state": {
            "health": {"schedule_health": 0.85, "open_blockers": 0},
            "milestones": [],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.30, "is_decayed": True, "sparsity_alert": "missing_update"},
        "expected_policy": "allow_with_audit",
    },
    {
        "name": "healthy_delivery",
        "project_id": "proj_005",
        "event_type": "milestone.completed",
        "canonical_state": {
            "health": {"schedule_health": 0.92, "open_blockers": 0},
            "milestones": [
                {"milestone_id": "m5", "name": "MVP Release", "status": "completed"},
            ],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.95, "is_decayed": False, "sparsity_alert": None},
        "expected_policy": "allow",
    },
]

# ── Agent registry (instantiated once, reused across scenarios) ───────────────

AGENTS = {
    "program_director":    ProgramDirectorAgent(),
    "planning":            PlanningAgent(),
    "risk_intelligence":   RiskIntelligenceAgent(),
    "execution_monitoring": ExecutionMonitoringAgent(),
    "issue_management":    IssueManagementAgent(),
    "communication":       CommunicationAgent(),
    "knowledge":           KnowledgeAgent(),
}

# Agents that get LLM enrichment in Test B
LLM_ENRICHED_AGENTS = {"communication", "knowledge", "execution_monitoring"}

# ── LLM enrichment prompts (mirrors existing prompt templates in agents/) ─────

LLM_PROMPTS: Dict[str, str] = {
    "communication": (
        "You are a PMO communication agent. "
        "Given project_id={project_id}, event={event_type}, "
        "schedule_health={schedule_health:.0%}, open_blockers={open_blockers}, "
        "signal_confidence={confidence:.2f}. "
        "Generate a 3-bullet executive brief. "
        'Output ONLY JSON: {{"title": "...", "bullets": ["...", "...", "..."], '
        '"risk_level": "low|medium|high|critical"}}'
    ),
    "knowledge": (
        "Classify this delivery event into one of: "
        "[dependency_failure, scope_creep, resource_overload, schedule_slip, quality_issue, healthy]. "
        "Event type: {event_type}. Schedule health: {schedule_health:.0%}. "
        "Open blockers: {open_blockers}. Signal confidence: {confidence:.2f}. "
        'Output ONLY JSON: {{"pattern": "...", "confidence": 0.0, "lesson": "one sentence"}}'
    ),
    "execution_monitoring": (
        "Summarise delivery execution status. "
        "Project: {project_id}. Schedule health: {schedule_health:.0%}. "
        "Open blockers: {open_blockers}. Event: {event_type}. "
        'Output ONLY JSON: {{"status": "on_track|at_risk|critical", '
        '"variance_summary": "one sentence", "recommended_action": "one sentence"}}'
    ),
}

# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    agent_name: str
    latency_ms: float
    schema_score: float          # 0.0–1.0: fraction of required fields populated
    output: Optional[AgentOutput]
    error: Optional[str] = None
    llm_tokens_prompt: int = 0
    llm_tokens_output: int = 0
    llm_tokens_per_sec: float = 0.0
    llm_latency_ms: float = 0.0
    llm_output_snippet: str = ""
    llm_valid_json: bool = False
    narrative_words: int = 0     # communication agent only


@dataclass
class ScenarioResult:
    scenario_name: str
    total_latency_ms: float
    agents: List[AgentResult] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_input(scenario: Dict[str, Any]) -> AgentInput:
    health = scenario["canonical_state"]["health"]
    return AgentInput(
        project_id=scenario["project_id"],
        event_type=scenario["event_type"],
        canonical_state=scenario["canonical_state"],
        graph_context={"neighbors": []},
        historical_cases=[
            {"case_id": "c1", "outcome": "resolved", "pattern": "dependency_failure"},
            {"case_id": "c2", "outcome": "slipped", "pattern": "resource_overload"},
        ],
        policy_context={"project_id": scenario["project_id"], "actions": {}},
        signal_quality=scenario["signal_quality"],
        extra={
            "audience": "executive",
            "agent_outputs": [],
            "knowledge_type": "lesson_extraction",
        },
    )


_REQUIRED_OUTPUT_FIELDS = [
    "agent_name", "decision_type", "confidence_score",
    "evidence", "decision_factors", "uncertainty_notes", "policy_action",
]

def _schema_score(output: Optional[AgentOutput]) -> float:
    if output is None:
        return 0.0
    score = 0
    for f in _REQUIRED_OUTPUT_FIELDS:
        val = getattr(output, f, None)
        if val is not None and val != [] and val != "":
            score += 1
    return score / len(_REQUIRED_OUTPUT_FIELDS)


def _narrative_words(output: Optional[AgentOutput]) -> int:
    if output is None:
        return 0
    body = output.extra.get("body", "") if output.extra else ""
    bullets = output.extra.get("bullets", []) if output.extra else []
    text = body + " " + " ".join(bullets)
    return len(text.split())


def _ollama_call(model: str, prompt: str, max_tokens: int = 150) -> Dict[str, Any]:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    elapsed_ms = (time.perf_counter() - t0) * 1000
    result["_elapsed_ms"] = elapsed_ms
    return result


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()
    return text


# ── Core runner ───────────────────────────────────────────────────────────────

def run_agent(
    agent_key: str,
    agent,
    inp: AgentInput,
    scenario: Dict[str, Any],
    with_llm: bool = False,
    llm_model_lightweight: str = "phi3:mini",
    llm_model_complex: str = "qwen2.5-coder:3b",
) -> AgentResult:
    t0 = time.perf_counter()
    output = None
    error = None
    try:
        output = agent.run(inp)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    latency_ms = (time.perf_counter() - t0) * 1000

    result = AgentResult(
        agent_name=agent_key,
        latency_ms=round(latency_ms, 1),
        schema_score=_schema_score(output),
        output=output,
        error=error,
        narrative_words=_narrative_words(output),
    )

    if with_llm and agent_key in LLM_ENRICHED_AGENTS and agent_key in LLM_PROMPTS:
        health = scenario["canonical_state"]["health"]
        sq = scenario["signal_quality"]
        prompt = LLM_PROMPTS[agent_key].format(
            project_id=scenario["project_id"],
            event_type=scenario["event_type"],
            schedule_health=health.get("schedule_health", 0.7),
            open_blockers=health.get("open_blockers", 0),
            confidence=sq.get("confidence_score", 0.5),
        )
        model = llm_model_lightweight
        try:
            llm_result = _ollama_call(model, prompt)
            raw = llm_result.get("response", "")
            stripped = _strip_fence(raw)
            is_valid = False
            try:
                json.loads(stripped)
                is_valid = True
            except Exception:
                pass
            result.llm_tokens_prompt = llm_result.get("prompt_eval_count", 0)
            result.llm_tokens_output = llm_result.get("eval_count", 0)
            result.llm_latency_ms = round(llm_result.get("_elapsed_ms", 0), 1)
            elapsed_s = result.llm_latency_ms / 1000
            result.llm_tokens_per_sec = round(
                result.llm_tokens_output / elapsed_s if elapsed_s > 0 else 0, 1
            )
            result.llm_output_snippet = raw[:120]
            result.llm_valid_json = is_valid
            # Word count from LLM output counts toward narrative richness
            if is_valid:
                parsed = json.loads(stripped)
                bullets = parsed.get("bullets", [])
                narrative = parsed.get("variance_summary", "") + parsed.get("lesson", "") + " ".join(bullets)
                result.narrative_words = max(result.narrative_words, len(narrative.split()))
        except Exception as e:
            result.llm_output_snippet = f"LLM_ERROR: {e}"

    return result


def run_scenario(
    scenario: Dict[str, Any],
    with_llm: bool = False,
) -> ScenarioResult:
    inp = _build_input(scenario)
    t_total = time.perf_counter()
    agent_results = []
    for key, agent in AGENTS.items():
        ar = run_agent(key, agent, inp, scenario, with_llm=with_llm)
        agent_results.append(ar)
    total_ms = (time.perf_counter() - t_total) * 1000
    return ScenarioResult(
        scenario_name=scenario["name"],
        total_latency_ms=round(total_ms, 1),
        agents=agent_results,
    )


# ── RAM helper ────────────────────────────────────────────────────────────────

def _rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    except Exception:
        return 0.0


# ── Report ────────────────────────────────────────────────────────────────────

def _avg(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def print_report(
    results_a: List[ScenarioResult],
    results_b: List[ScenarioResult],
    ram_a: float,
    ram_b: float,
) -> None:
    SEP = "=" * 90
    print(f"\n{SEP}")
    print(f"{'PIPELINE A/B BENCHMARK RESULTS':^90}")
    print(SEP)
    print("Test A = rule-based deterministic agents (current — no LLM)")
    print("Test B = same agents + phi3:mini LLM enrichment on communication / knowledge / execution_monitoring")
    print(SEP)

    # ── Per-agent latency comparison ──────────────────────────────────────────
    print(f"\n{'── Per-Agent Latency (avg ms across 5 scenarios) ──':}")
    hdr = f"{'Agent':<28} {'A (ms)':>10} {'B rule (ms)':>12} {'B LLM (ms)':>12} {'B total (ms)':>13}"
    print(hdr)
    print("-" * 78)

    for key in AGENTS:
        a_lats = [r.latency_ms for sr in results_a for r in sr.agents if r.agent_name == key]
        b_rule_lats = [r.latency_ms for sr in results_b for r in sr.agents if r.agent_name == key]
        b_llm_lats  = [r.llm_latency_ms for sr in results_b for r in sr.agents if r.agent_name == key]
        b_total     = [r + l for r, l in zip(b_rule_lats, b_llm_lats)]
        print(f"{key:<28} {_avg(a_lats):>10.1f} {_avg(b_rule_lats):>12.1f} {_avg(b_llm_lats):>12.1f} {_avg(b_total):>13.1f}")

    # ── Total pipeline latency ────────────────────────────────────────────────
    a_totals = [sr.total_latency_ms for sr in results_a]
    b_totals = [sr.total_latency_ms for sr in results_b]
    b_llm_totals = [sum(r.llm_latency_ms for r in sr.agents) for sr in results_b]
    b_combined = [t + l for t, l in zip(b_totals, b_llm_totals)]

    print("-" * 78)
    print(f"{'TOTAL PIPELINE':.<28} {_avg(a_totals):>10.1f} {_avg(b_totals):>12.1f} {_avg(b_llm_totals):>12.1f} {_avg(b_combined):>13.1f}")

    # ── Quality metrics ───────────────────────────────────────────────────────
    print(f"\n{'── Quality Metrics (avg across 5 scenarios) ──':}")
    hdr2 = f"{'Metric':<45} {'Test A':>10} {'Test B':>10} {'Delta':>10}"
    print(hdr2)
    print("-" * 78)

    def _collect(results, key, attr):
        return [getattr(r, attr) for sr in results for r in sr.agents if r.agent_name == key]

    # Schema compliance
    for k in AGENTS:
        a_scores = _collect(results_a, k, "schema_score")
        b_scores = _collect(results_b, k, "schema_score")
        delta = _avg(b_scores) - _avg(a_scores)
        print(f"  {k:<43} {_avg(a_scores):>9.1%} {_avg(b_scores):>9.1%} {delta:>+9.1%}")

    # Narrative richness (communication only)
    a_words = _collect(results_a, "communication", "narrative_words")
    b_words = _collect(results_b, "communication", "narrative_words")
    print(f"\n  {'Communication narrative (avg words)':<43} {_avg(a_words):>9.0f} {_avg(b_words):>9.0f} {_avg(b_words)-_avg(a_words):>+9.0f}")

    # LLM JSON validity (Test B enriched agents)
    for k in sorted(LLM_ENRICHED_AGENTS):
        b_valid = [r.llm_valid_json for sr in results_b for r in sr.agents if r.agent_name == k]
        rate = sum(b_valid) / len(b_valid) if b_valid else 0.0
        print(f"  {k + ' LLM valid JSON rate':<43} {'N/A':>9} {rate:>9.1%} {'':>10}")

    # ── Token usage (Test B only) ─────────────────────────────────────────────
    print(f"\n{'── LLM Token Usage — Test B (phi3:mini, enriched agents only) ──':}")
    hdr3 = f"{'Agent':<28} {'Prompt tok':>12} {'Output tok':>12} {'tok/sec':>10} {'LLM ms':>10}"
    print(hdr3)
    print("-" * 65)
    for k in sorted(LLM_ENRICHED_AGENTS):
        b_pt = _collect(results_b, k, "llm_tokens_prompt")
        b_ot = _collect(results_b, k, "llm_tokens_output")
        b_ts = _collect(results_b, k, "llm_tokens_per_sec")
        b_lm = _collect(results_b, k, "llm_latency_ms")
        if any(v > 0 for v in b_pt):
            print(f"{k:<28} {_avg(b_pt):>12.0f} {_avg(b_ot):>12.0f} {_avg(b_ts):>10.1f} {_avg(b_lm):>10.0f}")

    # ── RAM ───────────────────────────────────────────────────────────────────
    print(f"\n{'── Memory (RSS) ──':}")
    print(f"  After Test A: {ram_a:.1f} MB")
    print(f"  After Test B: {ram_b:.1f} MB")
    print(f"  Delta:        {ram_b - ram_a:+.1f} MB (Ollama model stays resident in separate process)")

    # ── Errors ───────────────────────────────────────────────────────────────
    all_errors = [
        (sr.scenario_name, r.agent_name, r.error)
        for results in [results_a, results_b]
        for sr in results
        for r in sr.agents
        if r.error
    ]
    if all_errors:
        print(f"\n{'── Errors ──':}")
        for sname, aname, err in all_errors:
            print(f"  [{sname}] {aname}: {err}")

    # ── Summary verdict ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("VERDICT")
    print(SEP)
    a_total_ms = _avg(a_totals)
    b_total_ms = _avg(b_combined)
    latency_cost_ms = b_total_ms - a_total_ms
    a_comm_words = _avg(_collect(results_a, "communication", "narrative_words"))
    b_comm_words = _avg(_collect(results_b, "communication", "narrative_words"))
    b_json_rates = []
    for k in sorted(LLM_ENRICHED_AGENTS):
        b_valid = [r.llm_valid_json for sr in results_b for r in sr.agents if r.agent_name == k]
        if b_valid:
            b_json_rates.append(sum(b_valid) / len(b_valid))

    print(f"  Latency cost of LLM enrichment:      +{latency_cost_ms:.0f} ms avg per pipeline run")
    print(f"  Narrative richness gain (comm agent): {a_comm_words:.0f} → {b_comm_words:.0f} words (+{b_comm_words - a_comm_words:.0f})")
    print(f"  LLM JSON validity rate (avg):         {_avg(b_json_rates):.0%}")
    print(f"  Schema compliance:                    unchanged (rule-based layer unaffected)")
    print(f"  Evaluation target risk:               communication acceptance 90% target — validate manually")
    print(SEP + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\nInitialising agents...", flush=True)
    # warm up (first import is slow)
    _ = AGENTS

    print("Running Test A — rule-based pipeline (5 scenarios)...", flush=True)
    results_a: List[ScenarioResult] = []
    for s in SCENARIOS:
        print(f"  scenario: {s['name']}", flush=True)
        results_a.append(run_scenario(s, with_llm=False))
    ram_a = _rss_mb()

    print("\nRunning Test B — LLM-enriched pipeline (5 scenarios, phi3:mini)...", flush=True)
    results_b: List[ScenarioResult] = []
    for s in SCENARIOS:
        print(f"  scenario: {s['name']}", flush=True)
        results_b.append(run_scenario(s, with_llm=True))
    ram_b = _rss_mb()

    print_report(results_a, results_b, ram_a, ram_b)


if __name__ == "__main__":
    main()
