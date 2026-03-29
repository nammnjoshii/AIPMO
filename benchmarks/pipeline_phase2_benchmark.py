"""
Pipeline Phase 2 Benchmark
===========================
Compares three configurations across 5 delivery scenarios:

  Baseline  — rule-based agents, no LLM (0.2 ms, what runs today)
  Phase 1   — baseline + phi3:mini local enrichment (hardcoded results from 2026-03-29)
  Groq      — baseline + llama-3.3-70b-versatile via Groq free tier
  Gemini    — baseline + gemini-1.5-flash via Google AI Studio free tier

Key difference from Phase 1 benchmark:
  Phase 1 enriched 3 lightweight agents only (comm, knowledge, exec_monitoring)
  Phase 2 enriches ALL 7 agents, matching the full configs/models.yaml tier mapping:
    complex tier   → risk_intelligence, planning, program_director
    lightweight tier → execution_monitoring, issue_management, communication, knowledge

Phase 2 is the gate for wiring LLM into the live pipeline. It passes if:
  ✓ Avg communication brief latency < 30s (report target)
  ✓ Total pipeline latency < 120s (2-min event-to-observation target)
  ✓ LLM JSON validity ≥ 80% across all enriched agents
  ✓ Narrative richness ≥ 2x Phase 1 word count (currently 28 words)

Setup (one-time):
  export GROQ_API_KEY=...    # https://console.groq.com — free, no credit card
  export GEMINI_API_KEY=...  # https://aistudio.google.com — free tier

Run:
  python3 benchmarks/pipeline_phase2_benchmark.py

If a key is missing, that provider is skipped and shown as KEY_NOT_SET.
No Ollama required. Only openai package needed (already installed).
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Project root ──────────────────────────────────────────────────────────────
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

# ── Phase 1 hardcoded baseline (from 2026-03-29 run on M1 8GB) ───────────────
PHASE1_RESULTS = {
    "total_pipeline_ms":   54899,
    "comm_narrative_words": 28,
    "json_validity_avg":   0.47,
    "tokens_per_sec":      11.5,    # phi3:mini avg
    "ram_delta_mb":         3.8,
}

# ── Providers ─────────────────────────────────────────────────────────────────
PROVIDERS = {
    "groq": {
        "base_url":  "https://api.groq.com/openai/v1",
        "env_key":   "GROQ_API_KEY",
        "complex":   "llama-3.3-70b-versatile",
        "lightweight": "llama-3.3-70b-versatile",   # Groq free tier — one model serves both
    },
    "gemini": {
        "base_url":  "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key":   "GEMINI_API_KEY",
        "complex":   "gemini-1.5-flash",
        "lightweight": "gemini-1.5-flash",
    },
}

# ── Agent tier mapping (mirrors configs/models.yaml) ─────────────────────────
AGENT_TIERS: Dict[str, str] = {
    "program_director":    "complex",
    "planning":            "complex",
    "risk_intelligence":   "complex",
    "execution_monitoring": "lightweight",
    "issue_management":    "lightweight",
    "communication":       "lightweight",
    "knowledge":           "lightweight",
}

# ── Scenarios (identical to pipeline_ab_benchmark.py) ────────────────────────
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
    },
    {
        "name": "capacity_overload",
        "project_id": "proj_002",
        "event_type": "resource.overloaded",
        "canonical_state": {
            "health": {"schedule_health": 0.60, "open_blockers": 1},
            "milestones": [{"milestone_id": "m3", "name": "Sprint 8", "status": "on_track"}],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.70, "is_decayed": False, "sparsity_alert": None},
    },
    {
        "name": "silent_scope_creep",
        "project_id": "proj_003",
        "event_type": "scope.change_detected",
        "canonical_state": {
            "health": {"schedule_health": 0.72, "open_blockers": 0},
            "milestones": [{"milestone_id": "m4", "name": "Phase 2 Kickoff", "status": "on_track"}],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.55, "is_decayed": True, "sparsity_alert": "stale_velocity"},
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
    },
    {
        "name": "healthy_delivery",
        "project_id": "proj_005",
        "event_type": "milestone.completed",
        "canonical_state": {
            "health": {"schedule_health": 0.92, "open_blockers": 0},
            "milestones": [{"milestone_id": "m5", "name": "MVP Release", "status": "completed"}],
            "tasks": [],
        },
        "signal_quality": {"confidence_score": 0.95, "is_decayed": False, "sparsity_alert": None},
    },
]

# ── Prompts — one per agent, richer than Phase 1 (larger model can handle it) ─

LLM_PROMPTS: Dict[str, str] = {
    # lightweight tier
    "communication": (
        "You are a PMO communication agent generating a decision preparation brief.\n"
        "Project: {project_id} | Event: {event_type} | Audience: executive\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers} | "
        "Signal confidence: {confidence:.2f}\n"
        "Generate a 4-bullet executive brief with a clear recommended action.\n"
        'Output ONLY valid JSON (no markdown): {{"title": "string", '
        '"bullets": ["string","string","string","string"], '
        '"risk_level": "low|medium|high|critical", '
        '"recommended_action": "string"}}'
    ),
    "knowledge": (
        "Classify this delivery event and extract a lesson learned.\n"
        "Event: {event_type} | Project: {project_id}\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers} | "
        "Confidence: {confidence:.2f}\n"
        "Patterns: [dependency_failure, scope_creep, resource_overload, "
        "schedule_slip, quality_issue, healthy]\n"
        'Output ONLY valid JSON (no markdown): {{"pattern": "string", '
        '"confidence": 0.0, '
        '"lesson": "one actionable sentence", '
        '"applies_to_future_projects": true}}'
    ),
    "execution_monitoring": (
        "Analyse delivery execution status.\n"
        "Project: {project_id} | Event: {event_type}\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers}\n"
        'Output ONLY valid JSON (no markdown): {{"status": "on_track|at_risk|critical", '
        '"variance_summary": "one sentence", '
        '"throughput_trend": "improving|stable|declining", '
        '"recommended_action": "one sentence"}}'
    ),
    "issue_management": (
        "Classify the root cause of this delivery issue.\n"
        "Project: {project_id} | Event: {event_type}\n"
        "Open blockers: {open_blockers} | Schedule health: {schedule_health:.0%}\n"
        "Root cause categories: [dependency, resourcing, technical_debt, "
        "scope, process, external]\n"
        'Output ONLY valid JSON (no markdown): {{"root_cause": "string", '
        '"severity": "low|medium|high|critical", '
        '"resolution_path": "one sentence"}}'
    ),
    # complex tier
    "risk_intelligence": (
        "You are a risk intelligence agent. Score this delivery risk.\n"
        "Project: {project_id} | Event: {event_type}\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers} | "
        "Signal confidence: {confidence:.2f}\n"
        "Compute schedule_slip_probability (0.0–1.0) based on all factors.\n"
        "Policy thresholds: >0.40 = escalate, 0.20–0.40 = approval_required, <0.20 = allow\n"
        'Output ONLY valid JSON (no markdown): {{"schedule_slip_probability": 0.0, '
        '"risk_factors": ["string","string"], '
        '"mitigation": "one sentence", '
        '"policy_action": "allow|approval_required|escalate"}}'
    ),
    "planning": (
        "Analyse milestone risk and generate a recovery recommendation.\n"
        "Project: {project_id} | Event: {event_type}\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers}\n"
        'Output ONLY valid JSON (no markdown): {{"milestone_at_risk": true, '
        '"estimated_slip_days": 0, '
        '"dependency_chain_impact": "none|low|medium|high", '
        '"recovery_recommendation": "one sentence"}}'
    ),
    "program_director": (
        "You are the Program Director agent. Coordinate a response to this delivery event.\n"
        "Project: {project_id} | Event: {event_type}\n"
        "Schedule health: {schedule_health:.0%} | Open blockers: {open_blockers} | "
        "Confidence: {confidence:.2f}\n"
        "Determine escalation level and coordination pattern needed.\n"
        'Output ONLY valid JSON (no markdown): {{"escalation_level": "none|pm|director|exec", '
        '"coordination_pattern": "sequential|parallel|arbitration|escalation", '
        '"next_action": "one sentence", '
        '"confidence": 0.0}}'
    ),
}

# ── Agent instances ───────────────────────────────────────────────────────────
AGENTS = {
    "program_director":    ProgramDirectorAgent(),
    "planning":            PlanningAgent(),
    "risk_intelligence":   RiskIntelligenceAgent(),
    "execution_monitoring": ExecutionMonitoringAgent(),
    "issue_management":    IssueManagementAgent(),
    "communication":       CommunicationAgent(),
    "knowledge":           KnowledgeAgent(),
}

# ── Data containers ───────────────────────────────────────────────────────────
@dataclass
class AgentResult:
    agent_name: str
    rule_latency_ms: float
    llm_latency_ms: float = 0.0
    llm_tokens_prompt: int = 0
    llm_tokens_output: int = 0
    llm_tokens_per_sec: float = 0.0
    llm_valid_json: bool = False
    llm_output_snippet: str = ""
    schema_score: float = 1.0
    narrative_words: int = 0
    error: Optional[str] = None

@dataclass
class ScenarioResult:
    scenario_name: str
    rule_total_ms: float
    llm_total_ms: float
    agents: List[AgentResult] = field(default_factory=list)

# ── Helpers ───────────────────────────────────────────────────────────────────
_REQUIRED_FIELDS = [
    "agent_name", "decision_type", "confidence_score",
    "evidence", "decision_factors", "uncertainty_notes", "policy_action",
]

def _schema_score(output: Optional[AgentOutput]) -> float:
    if not output:
        return 0.0
    return sum(
        1 for f in _REQUIRED_FIELDS
        if getattr(output, f, None) not in (None, [], "")
    ) / len(_REQUIRED_FIELDS)

def _narrative_words(output: Optional[AgentOutput]) -> int:
    if not output:
        return 0
    body = (output.extra or {}).get("body", "")
    bullets = (output.extra or {}).get("bullets", [])
    return len((body + " " + " ".join(bullets)).split())

def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```")).strip()
    return text

def _build_input(scenario: Dict[str, Any]) -> AgentInput:
    return AgentInput(
        project_id=scenario["project_id"],
        event_type=scenario["event_type"],
        canonical_state=scenario["canonical_state"],
        graph_context={"neighbors": []},
        historical_cases=[
            {"case_id": "c1", "outcome": "resolved", "pattern": "dependency_failure"},
            {"case_id": "c2", "outcome": "slipped",  "pattern": "resource_overload"},
        ],
        policy_context={"project_id": scenario["project_id"], "actions": {}},
        signal_quality=scenario["signal_quality"],
        extra={"audience": "executive", "agent_outputs": [], "knowledge_type": "lesson_extraction"},
    )

def _avg(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0

# ── LLM call (OpenAI-compatible) ──────────────────────────────────────────────
def _call_llm(
    client,
    model: str,
    prompt: str,
    max_tokens: int = 300,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    content = resp.choices[0].message.content or ""
    usage = resp.usage
    output_tokens = usage.completion_tokens if usage else 0
    return {
        "content": content,
        "elapsed_ms": elapsed_ms,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "output_tokens": output_tokens,
        "tokens_per_sec": output_tokens / (elapsed_ms / 1000) if elapsed_ms > 0 else 0,
    }

# ── Run one scenario ──────────────────────────────────────────────────────────
def run_scenario(
    scenario: Dict[str, Any],
    client=None,
    provider_cfg: Optional[Dict[str, str]] = None,
) -> ScenarioResult:
    inp = _build_input(scenario)
    health = scenario["canonical_state"]["health"]
    sq = scenario["signal_quality"]

    agent_results = []
    rule_total = 0.0
    llm_total = 0.0

    for key, agent in AGENTS.items():
        # Rule-based run
        t0 = time.perf_counter()
        output = None
        err = None
        try:
            output = agent.run(inp)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        rule_ms = (time.perf_counter() - t0) * 1000
        rule_total += rule_ms

        ar = AgentResult(
            agent_name=key,
            rule_latency_ms=round(rule_ms, 1),
            schema_score=_schema_score(output),
            narrative_words=_narrative_words(output),
            error=err,
        )

        # LLM enrichment (if client provided and prompt defined)
        if client and provider_cfg and key in LLM_PROMPTS:
            tier = AGENT_TIERS.get(key, "lightweight")
            model = provider_cfg[tier]
            prompt = LLM_PROMPTS[key].format(
                project_id=scenario["project_id"],
                event_type=scenario["event_type"],
                schedule_health=health.get("schedule_health", 0.7),
                open_blockers=health.get("open_blockers", 0),
                confidence=sq.get("confidence_score", 0.5),
            )
            try:
                result = _call_llm(client, model, prompt)
                stripped = _strip_fence(result["content"])
                valid = False
                extra_words = 0
                try:
                    parsed = json.loads(stripped)
                    valid = True
                    # Narrative richness from LLM output
                    texts = [str(v) for v in parsed.values() if isinstance(v, (str, list))]
                    flat = " ".join(t if isinstance(t, str) else " ".join(t) for t in texts)
                    extra_words = len(flat.split())
                except Exception:
                    pass
                ar.llm_latency_ms = round(result["elapsed_ms"], 1)
                ar.llm_tokens_prompt = result["prompt_tokens"]
                ar.llm_tokens_output = result["output_tokens"]
                ar.llm_tokens_per_sec = round(result["tokens_per_sec"], 1)
                ar.llm_valid_json = valid
                ar.llm_output_snippet = result["content"][:120]
                ar.narrative_words = max(ar.narrative_words, extra_words)
                llm_total += ar.llm_latency_ms
            except Exception as e:
                ar.llm_output_snippet = f"LLM_ERROR: {e}"
                ar.llm_latency_ms = 0.0

        agent_results.append(ar)

    return ScenarioResult(
        scenario_name=scenario["name"],
        rule_total_ms=round(rule_total, 1),
        llm_total_ms=round(llm_total, 1),
        agents=agent_results,
    )

# ── Gate checks ───────────────────────────────────────────────────────────────
GATES = {
    "comm_latency_s":    30.0,    # report generation target
    "pipeline_latency_s": 120.0,  # event-to-observation target
    "json_validity":      0.80,   # min LLM valid JSON rate
    "narrative_words":    56,     # 2x Phase 1 (28 words)
}

def _gate_check(label: str, comm_ms: float, pipeline_ms: float, json_rate: float, words: float) -> List[str]:
    results = []
    checks = [
        ("comm latency < 30s",      comm_ms / 1000,     GATES["comm_latency_s"],    True),
        ("pipeline latency < 120s", pipeline_ms / 1000, GATES["pipeline_latency_s"], True),
        ("JSON validity ≥ 80%",     json_rate,           GATES["json_validity"],     True),
        ("narrative words ≥ 56",    words,               GATES["narrative_words"],   True),
    ]
    all_pass = True
    for name, val, threshold, lower_better_is_false in checks:
        passed = val <= threshold if name.startswith("comm") or name.startswith("pipeline") else val >= threshold
        sym = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        results.append(f"  {sym}  {name}: {val:.1f} (target {'<' if 'latency' in name else '>='} {threshold})")
    results.append(f"  {'→ READY to wire LLM into live pipeline' if all_pass else '→ NOT READY — fix failing gates first'}")
    return results

# ── Report ────────────────────────────────────────────────────────────────────
def print_report(
    baseline_results: List[ScenarioResult],
    provider_runs: Dict[str, Optional[List[ScenarioResult]]],
) -> None:
    W = 100
    print(f"\n{'=' * W}")
    print(f"{'PHASE 2 PIPELINE BENCHMARK':^{W}}")
    print("=" * W)

    def collect(results, key, attr):
        return [getattr(r, attr) for sr in results for r in sr.agents if r.agent_name == key]

    # ── Latency table ─────────────────────────────────────────────────────────
    print(f"\n── Avg Pipeline Latency (ms) across 5 scenarios ──")
    hdr = f"{'Configuration':<22} {'Rule (ms)':>12} {'LLM (ms)':>12} {'Total (ms)':>12} {'Comm LLM (ms)':>14}"
    print(hdr)
    print("-" * 75)

    # Baseline
    b_rule = _avg([sr.rule_total_ms for sr in baseline_results])
    print(f"{'Baseline (rule-based)':<22} {b_rule:>12.1f} {'N/A':>12} {b_rule:>12.1f} {'N/A':>14}")

    # Phase 1 (hardcoded)
    print(f"{'Phase 1 (phi3:mini)':<22} {'0.2':>12} {PHASE1_RESULTS['total_pipeline_ms']:>12,} {PHASE1_RESULTS['total_pipeline_ms']:>12,} {'~11,215':>14}")

    # Provider runs
    for pname, results in provider_runs.items():
        if results is None:
            print(f"{pname:<22} {'KEY_NOT_SET':>12} {'KEY_NOT_SET':>12} {'KEY_NOT_SET':>12} {'KEY_NOT_SET':>14}")
            continue
        r_rule = _avg([sr.rule_total_ms for sr in results])
        r_llm  = _avg([sr.llm_total_ms  for sr in results])
        r_comm_llm = _avg(collect(results, "communication", "llm_latency_ms"))
        print(f"{pname:<22} {r_rule:>12.1f} {r_llm:>12.1f} {r_rule + r_llm:>12.1f} {r_comm_llm:>14.1f}")

    # ── Quality & token table ─────────────────────────────────────────────────
    print(f"\n── Quality Metrics (avg across 5 scenarios) ──")
    hdr2 = f"{'Configuration':<22} {'JSON valid%':>12} {'Comm words':>12} {'tok/sec':>10} {'Schema%':>10}"
    print(hdr2)
    print("-" * 70)

    print(f"{'Baseline':<22} {'N/A':>12} {26:>12} {'N/A':>10} {'100%':>10}")
    print(f"{'Phase 1 (phi3:mini)':<22} {PHASE1_RESULTS['json_validity_avg']:>11.0%} "
          f"{PHASE1_RESULTS['comm_narrative_words']:>12} "
          f"{PHASE1_RESULTS['tokens_per_sec']:>10.1f} {'100%':>10}")

    for pname, results in provider_runs.items():
        if results is None:
            print(f"{pname:<22} {'KEY_NOT_SET':>12} {'KEY_NOT_SET':>12} {'KEY_NOT_SET':>10} {'KEY_NOT_SET':>10}")
            continue
        all_valid = [r.llm_valid_json for sr in results for r in sr.agents if r.llm_tokens_output > 0]
        json_rate = _avg([float(v) for v in all_valid]) if all_valid else 0.0
        comm_words = _avg(collect(results, "communication", "narrative_words"))
        tps_vals = [r.llm_tokens_per_sec for sr in results for r in sr.agents if r.llm_tokens_per_sec > 0]
        schema = _avg([r.schema_score for sr in results for r in sr.agents])
        print(f"{pname:<22} {json_rate:>11.0%} {comm_words:>12.0f} {_avg(tps_vals):>10.1f} {schema:>10.0%}")

    # ── Per-agent LLM latency (providers only) ────────────────────────────────
    for pname, results in provider_runs.items():
        if results is None:
            continue
        print(f"\n── Per-Agent LLM Latency — {pname} (avg ms) ──")
        hdr3 = f"{'Agent':<28} {'Tier':<12} {'LLM ms':>10} {'Prompt tok':>12} {'Out tok':>10} {'JSON?':>8}"
        print(hdr3)
        print("-" * 85)
        for key in AGENTS:
            tier = AGENT_TIERS.get(key, "lightweight")
            lms  = collect(results, key, "llm_latency_ms")
            pts  = collect(results, key, "llm_tokens_prompt")
            ots  = collect(results, key, "llm_tokens_output")
            jvs  = collect(results, key, "llm_valid_json")
            json_r = f"{_avg([float(v) for v in jvs]):.0%}" if any(ots) else "N/A"
            print(f"{key:<28} {tier:<12} {_avg(lms):>10.0f} {_avg(pts):>12.0f} {_avg(ots):>10.0f} {json_r:>8}")

    # ── Gate checks ───────────────────────────────────────────────────────────
    print(f"\n── Phase 2 Gate Checks (must pass all 4 to wire LLM into live pipeline) ──")
    for pname, results in provider_runs.items():
        print(f"\n  {pname}:")
        if results is None:
            print("    SKIP  Key not set — cannot evaluate")
            continue
        comm_ms_vals = collect(results, "communication", "llm_latency_ms")
        comm_ms = _avg(comm_ms_vals)
        pipeline_ms = _avg([sr.rule_total_ms + sr.llm_total_ms for sr in results])
        all_valid = [r.llm_valid_json for sr in results for r in sr.agents if r.llm_tokens_output > 0]
        json_rate = _avg([float(v) for v in all_valid]) if all_valid else 0.0
        comm_words = _avg(collect(results, "communication", "narrative_words"))
        for line in _gate_check(pname, comm_ms, pipeline_ms, json_rate, comm_words):
            print(f"  {line}")

    # ── Recommendation ────────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print("RECOMMENDATION")
    print("=" * W)
    any_ready = False
    for pname, results in provider_runs.items():
        if results is None:
            print(f"  {pname:<10} → KEY_NOT_SET. Get a free key and re-run.")
            continue
        comm_ms = _avg(collect(results, "communication", "llm_latency_ms"))
        pipeline_ms = _avg([sr.rule_total_ms + sr.llm_total_ms for sr in results])
        all_valid = [r.llm_valid_json for sr in results for r in sr.agents if r.llm_tokens_output > 0]
        json_rate = _avg([float(v) for v in all_valid]) if all_valid else 0.0
        comm_words = _avg(collect(results, "communication", "narrative_words"))
        passes = (
            comm_ms / 1000 <= GATES["comm_latency_s"]
            and pipeline_ms / 1000 <= GATES["pipeline_latency_s"]
            and json_rate >= GATES["json_validity"]
            and comm_words >= GATES["narrative_words"]
        )
        if passes:
            any_ready = True
            print(f"  {pname:<10} → READY. Wire {pname} as LLM_PROVIDER in .env and re-run pytest.")
        else:
            failing = []
            if comm_ms / 1000 > GATES["comm_latency_s"]:      failing.append("comm latency")
            if pipeline_ms / 1000 > GATES["pipeline_latency_s"]: failing.append("pipeline latency")
            if json_rate < GATES["json_validity"]:             failing.append("JSON validity")
            if comm_words < GATES["narrative_words"]:          failing.append("narrative richness")
            print(f"  {pname:<10} → NOT READY. Failing: {', '.join(failing)}.")

    if not any_ready:
        print("\n  Neither provider passed all gates. Keep rule-based pipeline until keys are set.")
    print("=" * W + "\n")

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    from openai import OpenAI

    print("\nInitialising agents...", flush=True)
    _ = AGENTS  # warm up

    # Baseline run
    print("Running Baseline (rule-based, no LLM — 5 scenarios)...", flush=True)
    baseline_results: List[ScenarioResult] = []
    for s in SCENARIOS:
        print(f"  {s['name']}", flush=True)
        baseline_results.append(run_scenario(s, client=None, provider_cfg=None))

    # Provider runs
    provider_runs: Dict[str, Optional[List[ScenarioResult]]] = {}
    for pname, cfg in PROVIDERS.items():
        api_key = os.environ.get(cfg["env_key"], "")
        if not api_key:
            print(f"\n{pname}: {cfg['env_key']} not set — skipping.", flush=True)
            provider_runs[pname] = None
            continue

        print(f"\nRunning {pname} ({cfg['complex']}) — 5 scenarios...", flush=True)
        client = OpenAI(base_url=cfg["base_url"], api_key=api_key)
        results: List[ScenarioResult] = []
        for s in SCENARIOS:
            print(f"  {s['name']}", flush=True)
            try:
                results.append(run_scenario(s, client=client, provider_cfg=cfg))
            except Exception as e:
                print(f"  ERROR on {s['name']}: {e}", flush=True)
                results.append(ScenarioResult(
                    scenario_name=s["name"],
                    rule_total_ms=0.0,
                    llm_total_ms=0.0,
                    agents=[],
                ))
        provider_runs[pname] = results

    print_report(baseline_results, provider_runs)


if __name__ == "__main__":
    main()
