# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.
See @README.md for full project overview, stack, repo structure, and build phases.

---

## What This Project Is

An event-driven, multi-agent platform that creates a governed delivery intelligence layer above Jira, GitHub, Slack, and Smartsheet. Seven specialized AI agents observe, reason, and prepare decisions. Humans approve and act.

This is decision preparation infrastructure — not autonomous execution.

---

## Architecture in One Paragraph

Events flow in from integrations → Signal Quality Pipeline qualifies them → Canonical State and Knowledge Graph are updated → Agent Intelligence Layer reasons over scoped context → Policy Engine gates every proposed action → Communication Agent surfaces decision preparation briefs → humans approve → audit log captures everything. The Program Director Agent orchestrates multi-agent coordination patterns (sequential, parallel, conflict arbitration, escalation routing).

---

## Skill Bank

Path: /Users/khuushaliraja/Desktop/Nammn AI Practice/skill-bank-refactored/CATALOG.md

Before starting any significant implementation task, read CATALOG.md, detect the project type from files in this directory, load the relevant bundle, and identify applicable skills.
Then you just say: "check the skill bank for this task" — Claude auto-reads CATALOG.md without you specifying the path each time.

---

## Commands

```bash
# Start infrastructure
docker compose up -d

# Start orchestrator
python -m orchestrator.main

# Run all tests
pytest tests/

# Run a simulation scenario
python -m simulation.harness --scenario simulation/scenarios/program_alpha.yaml

# Validate policy config
python -m policy.engine --validate configs/policies.yaml

# Check agent evaluation metrics
python -m evaluation.metrics --report

# Run signal quality pipeline on a sample
python -m signal_quality.pipeline --sample examples/sample_events/task_blocked.json

# Start demo UI
streamlit run ui/app.py
```

---

## Where Things Live

| What you're working on | Where it lives |
|---|---|
| Agent logic | `agents/<agent_name>/` |
| Multi-agent coordination | `orchestrator/` |
| Policy rules and outcomes | `policy/` |
| Current project state | `state/` |
| Signal ingestion and quality | `signal_quality/` |
| Context assembly per agent call | `context_assembly/` |
| Knowledge graph queries | `knowledge_graph/query_service.py` |
| Integration adapters | `integrations/<source>/` |
| Evaluation and calibration | `evaluation/` |
| Simulation scenarios | `simulation/scenarios/` |
| Audit logging | `audit/` |
| RBAC and tenant isolation | `security/` |
| YAML config (agents, models, policies) | `configs/` |

---

## Hard Rules — Follow These on Every Task

**Agent boundaries**
- Agent logic never imports from sibling agent folders
- Agents propose actions — they do not call the policy engine directly
- Agents do not self-assemble context — use `context_assembly/assembler.py`
- All graph queries go through `knowledge_graph/query_service.py` — no ad hoc Cypher in agent code

**Outputs**
- Every `AgentOutput` must populate `uncertainty_notes` — empty is a bug
- Low-confidence outputs must surface a sparsity alert — never hide uncertainty
- Every state write uses `allow_with_audit` — bare `allow` is for read-only operations only

**Policy**
- All policy logic lives in `policy/` only
- Policy engine failure mode: fail closed — never fail open on restricted actions
- Do not inline permission checks in agent code

**Security**
- No hardcoded credentials anywhere — environment variables only
- No cross-project data in agent context — isolation is enforced in `context_assembly/assembler.py`
- Audit records are append-only — do not modify logs after write

**Testing**
- New agent logic requires unit tests in `tests/unit/`
- New coordination patterns require a simulation scenario in `simulation/scenarios/`
- Policy changes require a test in `tests/policy/`
- Do not suggest merging without green tests across all four test categories

---

## Agent Decision Types

Every agent output declares one of three decision types:

- `observation` — detect and describe. No human approval needed.
- `decision_preparation` — synthesize, surface options, recommend. Human decides.
- `execution` — pre-approved safe actions only (reports, dashboards, audit entries).

When writing or reviewing agent code, check that the decision type matches the action. Execution-tier actions require explicit policy whitelist.

---

## Signal Quality Rules

Agents must not reason silently over degraded data. When working in `signal_quality/` or agent code that consumes signals:

- Confidence below threshold → surface sparsity alert before producing output
- Source reliability scores live in `signal_quality/source_profiles.py` — update there, not inline
- Confidence decay windows are defined in `signal_quality/confidence_decay.py` — do not hardcode freshness thresholds in agent logic

---

## Model Routing

Model tier per agent is configured in `configs/models.yaml`. Do not change model assignment inside agent code — update the config. Current routing rationale is in README.md.

---

## Coordination Patterns

When adding new multi-agent workflows, use the established patterns in `orchestrator/event_router.py`:

- **Sequential** — agent B waits for agent A output
- **Parallel** — agents run independently, Program Director merges
- **Conflict arbitration** — Program Director resolves inconsistent conclusions
- **Escalation routing** — Policy Engine gates, Communication Agent prepares the brief

Document which pattern a new workflow uses in the function docstring.

---

## Key Interfaces to Know

```python
# All agents implement this contract
class BaseAgent:
    def run(self, data: AgentInput) -> AgentOutput: ...

# Context assembly — always use this, never build context inline
from context_assembly.assembler import assemble_context

# Policy evaluation — call before any execution-tier action
from policy.engine import evaluate_action

# Audit logging — call after every consequential action
from audit.logger import log_event
```

---

## Simulation First

When adding a new detection, escalation, or coordination behavior — write a simulation scenario before writing agent code. Scenarios go in `simulation/scenarios/`. The reference scenario (`program_alpha.yaml`) covers 12 projects, 4 shared teams, 120 tasks, and injected failures. Use it as a template.

---

## What Not to Do

- Do not create new agents without a corresponding skill definition in `configs/agents.yaml`
- Do not add graph relationships outside the defined node and edge types in `knowledge_graph/graph_schema.py`
- Do not bypass the Signal Quality Pipeline to write directly to canonical state
- Do not add Slack, email, or external notification logic inside agents — that belongs in `integrations/`
- Do not expand graph scope to task-level relationships — see README.md for explicit graph boundaries
