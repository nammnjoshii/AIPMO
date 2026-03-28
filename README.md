# Autonomous PMO

**Enterprise Multi-Agent System for Program Delivery Intelligence**

Autonomous PMO is an event-driven, multi-agent platform that creates a governed intelligence layer above existing project delivery tools. It ingests signals from Jira, GitHub, Slack, MS Project, and Smartsheet, reasons across them using seven specialized AI agents, and surfaces decision-ready intelligence to project managers and executives — continuously, not just at reporting time.

This is decision preparation, not automation. Agents observe, synthesize, and recommend. Humans approve, escalate, and lead.

---

## What This Does

| Capability | Replaces | Value |
|---|---|---|
| Continuous health monitoring | Manual status chasing | ~3–5 hrs/PM/week |
| Early risk detection | Reactive issue discovery | Days of lag eliminated |
| Decision preparation briefs | Hand-crafted exec updates | ~2–4 hrs/reporting cycle |
| Cross-project dependency mapping | Spreadsheet tracking | Near real-time |
| Lessons learned capture | Post-mortems that never happen | Institutional memory, automated |

---

## Stack

| Layer | Tool |
|---|---|
| Agent orchestration | LangGraph |
| LLM | Claude claude-sonnet-4-20250514 via Anthropic API |
| Integrations | Jira REST API · Smartsheet API · GitHub API · Slack API |
| Canonical state store | PostgreSQL + pgvector |
| Knowledge graph | Neo4j (local) or Amazon Neptune (cloud) |
| Event bus | Redis Streams (Phase 1) → Kafka (Phase 2+) |
| Policy engine | Custom Python rule engine (YAML-configured) |
| Demo UI | Streamlit (Phase 1) → Next.js (Phase 2) |
| Auth + scheduling | Supabase + APScheduler |

Python 3.11+. Docker required for local infrastructure.

---

## Repository Structure

```
autonomous-pmo/
├── agents/
│   ├── program_director/     # Orchestration, conflict resolution, escalation routing
│   ├── planning/             # Roadmap generation, milestone planning, dependency analysis
│   ├── execution_monitoring/ # Progress tracking, variance detection, throughput analysis
│   ├── risk_intelligence/    # Risk detection, impact estimation, mitigation recommendation
│   ├── issue_management/     # Blocker detection, root cause classification
│   ├── communication/        # Reporting, narrative synthesis, stakeholder updates
│   └── knowledge/            # Lessons learned, heuristic improvement, pattern retrieval
├── orchestrator/
│   ├── main.py               # LangGraph entry point
│   ├── event_router.py       # Routes events to agent coordination patterns
│   ├── conflict_resolver.py  # Program Director conflict arbitration logic
│   └── runtime.py            # Agent lifecycle management
├── policy/
│   ├── engine.py             # Policy evaluation engine
│   ├── schemas.py            # Policy object definitions
│   └── policies/             # YAML policy configs per tenant/project
├── state/
│   ├── canonical_state.py    # State read/write with idempotent updates
│   ├── normalization.py      # Signal normalization across sources
│   ├── reliability.py        # Source reliability scoring
│   └── schemas.py            # Canonical state object definitions
├── signal_quality/
│   ├── pipeline.py           # Main signal quality assessment pipeline
│   ├── noise_filter.py       # Deduplication and low-signal filtering
│   ├── confidence_decay.py   # Freshness-based confidence scoring
│   ├── missing_data.py       # Gap detection and sparsity alerts
│   └── source_profiles.py    # Per-source reliability profiles
├── context_assembly/
│   ├── assembler.py          # Builds scoped context per agent invocation
│   ├── state_slicer.py       # Extracts relevant canonical state slice
│   ├── graph_neighborhood.py # 2-hop graph context retrieval
│   └── case_matcher.py       # Historical case retrieval (top-3 match)
├── knowledge_graph/
│   ├── graph_store.py        # Neo4j read/write operations
│   ├── entity_extractor.py   # Entity extraction from signals
│   ├── relationship_builder.py
│   ├── query_service.py      # Cypher query patterns
│   ├── graph_schema.py       # Node and edge type definitions
│   └── graph_sync.py         # Graph update pipeline
├── events/
│   ├── producers/            # Source-specific event emitters
│   ├── consumers/            # Agent event consumers
│   └── schemas/              # Event type definitions
├── integrations/
│   ├── jira/                 # Jira REST API adapter
│   ├── github/               # GitHub API adapter
│   ├── slack/                # Slack Events API adapter
│   └── smartsheet/           # Smartsheet API adapter
├── evaluation/
│   ├── metrics.py            # Precision, recall, acceptance rate, edit rate
│   ├── calibration.py        # Threshold tuning and calibration loop
│   └── labeling.py           # Human feedback label ingestion
├── simulation/
│   ├── harness.py            # Synthetic delivery scenario runner
│   ├── scenarios/            # Predefined simulation scenarios
│   └── injectors/            # Failure and risk event injectors
├── audit/
│   ├── logger.py             # Structured audit event logger
│   └── retention.py          # Retention policy enforcement
├── security/
│   ├── rbac.py               # Role-based access control
│   ├── isolation.py          # Tenant and project isolation
│   └── secrets.py            # Secret management (never hardcode keys)
├── configs/
│   ├── agents.yaml           # Agent skill and tool configuration
│   ├── policies.yaml         # Default policy rules
│   ├── models.yaml           # Model routing by agent
│   └── tenants.yaml          # Tenant configuration
├── tests/
│   ├── unit/                 # Agent skill logic in isolation
│   ├── integration/          # Agent + Policy + State interactions
│   ├── policy/               # All policy outcomes for all action types
│   └── simulation/           # End-to-end delivery scenario playback
├── CLAUDE.md                 # Claude Code project context
└── README.md
```

---

## Setup

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Anthropic API key
- At least one integration credential (Jira or Smartsheet for Phase 1)

### Install

```bash
git clone https://github.com/your-org/autonomous-pmo
cd autonomous-pmo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Add your API keys here
```

### Start Infrastructure

```bash
docker compose up -d
# Starts: PostgreSQL, Redis, Neo4j
```

### Bootstrap Integrations

```bash
python -m integrations.jira.bootstrap
python -m integrations.github.bootstrap
python -m integrations.slack.bootstrap
python -m integrations.smartsheet.bootstrap
```

### Load Policies

```bash
python -m policy.engine --load configs/policies.yaml
```

### Start the Orchestrator

```bash
python -m orchestrator.main
```

### Run a Sample Event (Demo Mode)

```bash
python -m examples.run_sample_event
```

### Start the Demo UI

```bash
streamlit run ui/app.py
```

---

## Key Commands

```bash
# Run all tests
pytest tests/

# Run unit tests only
pytest tests/unit/

# Run a simulation scenario
python -m simulation.harness --scenario scenarios/blocked_dependency.yaml

# Validate policy config
python -m policy.engine --validate configs/policies.yaml

# Check agent metrics
python -m evaluation.metrics --report

# Run the signal quality pipeline on a sample payload
python -m signal_quality.pipeline --sample examples/sample_events/task_blocked.json

# Reload policies without restart
python -m policy.engine --reload
```

---

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=          # Anthropic API key for all LLM calls
DATABASE_URL=               # PostgreSQL connection string
REDIS_URL=                  # Redis connection string
NEO4J_URI=                  # Neo4j bolt URI
NEO4J_USER=
NEO4J_PASSWORD=

# Integrations (add what you have)
JIRA_BASE_URL=
JIRA_API_TOKEN=
JIRA_USER_EMAIL=
GITHUB_TOKEN=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SMARTSHEET_ACCESS_TOKEN=

# Optional
LOG_LEVEL=INFO              # DEBUG | INFO | WARNING | ERROR
ENVIRONMENT=development     # development | staging | production
TENANT_ID=default           # Override for multi-tenant deployments
```

Never hardcode credentials. All secrets must load from environment variables or a secrets manager.

---

## Agent Architecture

Seven specialized agents. Each has bounded responsibilities, defined inputs and outputs, and explicit evaluation targets.

| Agent | Core Job | Decision Type |
|---|---|---|
| Program Director | Orchestration, conflict resolution, escalation routing | Observation + Execution |
| Planning | Roadmap generation, milestone planning, dependency analysis | Recommendation |
| Execution Monitoring | Progress tracking, variance detection, throughput analysis | Observation |
| Risk Intelligence | Risk detection, impact estimation, mitigation recommendation | Recommendation |
| Issue Management | Blocker detection, root cause classification | Observation + Recommendation |
| Communication | Reporting, narrative synthesis, stakeholder-tailored briefs | Execution |
| Knowledge | Lessons learned, heuristic improvement, pattern retrieval | Observation |

### Decision Tiers

- **Observation** — detect and describe. No human approval required.
- **Decision Preparation** — synthesize evidence, surface options, recommend next step. Human decides.
- **Execution** — perform pre-approved safe actions (reports, dashboards, audit entries). Whitelisted only.

### Agent Coordination Patterns

**Sequential** — used when agent B needs agent A's output before reasoning.

```
Issue Management → classify blocker
  ↓
Risk Intelligence → evaluate milestone impact
  ↓
Communication → generate decision preparation brief
```

**Parallel** — used when multiple agents can reason independently.

```
Event: dependency.blocked
  ├── Execution Monitoring → throughput impact
  └── Risk Intelligence   → milestone risk score
              ↓
  Program Director → merge + route
```

**Conflict Arbitration** — when agents produce inconsistent conclusions, the Program Director compares evidence quality and confidence scores, then merges, prefers one signal, or escalates to human review.

**Escalation Routing** — when output crosses a policy threshold, Program Director routes through the Policy Engine, determines approval level, and prepares a human-ready escalation package.

---

## Agent Contracts

Every agent implements `BaseAgent` with typed inputs and outputs.

```python
@dataclass
class AgentInput:
    project_id: str
    event_type: str
    canonical_state: Dict[str, Any]   # scoped slice — not full state
    graph_context: Dict[str, Any]     # 2-hop neighborhood
    historical_cases: List[Any]       # top-3 matched cases
    policy_context: Dict[str, Any]    # applicable rules
    signal_quality: Dict[str, Any]    # reliability + confidence scores

@dataclass
class AgentOutput:
    agent_name: str
    decision_type: str                # observation | decision_preparation | execution
    confidence_score: float
    evidence: List[str]
    decision_factors: List[str]
    recommendation: Optional[str]
    proposed_state_updates: Dict[str, Any]
    proposed_graph_updates: List[Dict[str, Any]]
    policy_action: str
    uncertainty_notes: List[str]      # explicit gaps — never hide low confidence
```

---

## Signal Quality Pipeline

Every ingested signal passes through quality assessment before reaching the canonical state or knowledge graph. Agents must never silently reason over degraded data.

```
Raw Signal
  → Source Reliability Scoring
  → Noise Filtering
  → Confidence Scoring
  → Freshness Decay Check
  → Missing Data Detection
  → Signal Sparsity Alert (if triggered)
  → Qualified Signal → Canonical State / Graph
```

### Confidence Decay by Source

| Source | High Confidence Window | Decay Trigger |
|---|---|---|
| Jira task status | 24 hours | No update in 48 hours |
| GitHub velocity | 72 hours | No commit in 5 days |
| Slack discussion | 4 hours | Thread older than 24 hours |
| Meeting notes | 48 hours | No action item follow-up in 72 hours |
| Manual status report | 7 days | Next reporting cycle overdue |

When confidence is LOW, agents must surface a sparsity alert and cannot produce escalation-level outputs without explicit PM acknowledgment.

---

## Policy Engine

Every proposed agent action passes through the policy engine before execution. Policies are YAML-configured and versioned.

```yaml
# configs/policies/proj_123.yaml
version: 1.2
scope: project
project_id: proj_123
actions:
  generate_status_report: allow
  update_dashboard: allow
  create_risk_log_entry: allow_with_audit
  escalate_issue: approval_required
  modify_schedule: deny
  reassign_resources: approval_required
thresholds:
  schedule_slip_probability:
    escalate_if_greater_than: 0.40
  resource_overload:
    notify_if_greater_than: 0.30
```

**Policy outcomes:** `allow` · `allow_with_audit` · `approval_required` · `deny` · `escalate`

**Failure mode:** If the policy engine is unavailable, all non-whitelisted actions fail closed. Never fail open.

---

## Knowledge Graph

The Delivery Knowledge Graph stores long-lived relationships across the delivery ecosystem. It answers what the canonical state cannot: how everything is connected and what patterns matter over time.

**In scope:**
- Cross-project dependencies
- System ownership mapping
- Stakeholder approval chains
- Risk propagation paths
- Team capacity relationships
- Decision and outcome history

**Out of scope:**
- Individual task-level relationships
- Comment threads and sub-task granularity
- Tool-internal metadata
- Document version histories

Graph queries live in `knowledge_graph/query_service.py`. Add new query patterns there — do not write ad hoc Cypher in agent code.

---

## Model Routing

Model tier is configured per agent in `configs/models.yaml`. Match model capability to task complexity.

| Agent | Model Tier | Reason |
|---|---|---|
| Communication | Fast, low-cost | High-volume structured narrative — speed over depth |
| Execution Monitoring | Fast, low-cost | Pattern detection on structured data |
| Issue Management | Mid-tier | Classification + root cause — moderate reasoning |
| Risk Intelligence | High-accuracy | Multi-factor scoring — precision critical |
| Planning | High-accuracy | Dependency reasoning + estimation — complex inference |
| Program Director | High-accuracy | Conflict resolution + orchestration — highest demand |
| Knowledge | Retrieval-augmented | Pattern retrieval + light reasoning |

---

## Evaluation Targets

| Agent | Metric | Target |
|---|---|---|
| Risk Intelligence | Precision | > 80% |
| Risk Intelligence | Recall | > 70% |
| Risk Intelligence | False Positive Rate | < 15% |
| Risk Intelligence | Time-to-Detection | < 24 hours |
| Issue Management | Precision | > 85% |
| Issue Management | False Positive Rate | < 10% |
| Issue Management | Time-to-Detection | < 12 hours |
| Communication | Human Acceptance Rate | > 90% |
| Communication | Edit Rate | < 20% |
| Communication | Report Generation Latency | < 30 seconds |

Run `python -m evaluation.metrics --report` to see current agent performance against targets.

---

## Testing

```bash
pytest tests/unit/           # Agent skill logic in isolation
pytest tests/integration/    # Agent + Policy + State interactions
pytest tests/policy/         # All policy outcomes for all action types
pytest tests/simulation/     # End-to-end delivery scenario playback
```

### Simulation Scenarios

Simulation scenarios live in `simulation/scenarios/`. The reference scenario is `program_alpha.yaml`:

- 12 projects · 4 shared teams · 120 tasks · 8 milestones
- Injected: 3 dependency failures, 1 capacity overload, 2 silent scope creep signals, 1 late-surfacing critical blocker

Add new scenarios by dropping a YAML file in `simulation/scenarios/` and registering it in `simulation/harness.py`.

---

## Failure Modes

| Failure | Detection | Fallback |
|---|---|---|
| Knowledge graph unavailable | Health check timeout > 5s | Fall back to canonical state reasoning; flag reduced confidence |
| LLM timeout or error | API error + retry exhaustion | Retry smaller model; queue for manual review on final failure |
| Policy engine crash | Service health check | Deny all non-whitelisted actions; restore from last policy snapshot |
| Integration outage | No signal in > 30 min | Activate stale signal detection; continue on last known state |
| Canonical state corruption | Schema validation failure | Reject corrupt update; preserve last valid state; alert ops |

Never surface a partial output without confidence disclosure. Degrade gracefully — reduced capability beats incorrect output.

---

## Security

- All secrets via environment variables or secrets manager. No hardcoded credentials anywhere.
- Tenant and project isolation enforced at the state store and graph layer.
- Agents receive only the context scoped to the current task. No cross-project data leaks.
- RBAC defined in `security/rbac.py`. Add new roles there only — do not inline permission checks in agent code.
- Every consequential action produces an immutable audit record. Do not modify audit logs.

---

## Audit Logging

Logged events: `signal_ingested` · `state_update_proposed` · `graph_update_proposed` · `policy_evaluated` · `recommendation_generated` · `human_override` · `automated_action_executed` · `evaluation_metric_updated`

Audit records are append-only. Retention defaults: audit logs 12 months, state history 24 months, policy decisions 24 months.

---

## Conventions

- One agent, one folder. Agent logic does not reach into sibling agent folders.
- Policy logic lives in `policy/` only. Agents propose actions; they do not enforce policy.
- All graph queries go through `knowledge_graph/query_service.py`. No ad hoc Cypher in agents.
- Context assembly goes through `context_assembly/assembler.py`. Agents do not self-assemble context.
- Every agent output must populate `uncertainty_notes`. Empty uncertainty notes are a bug.
- Use `allow_with_audit` for anything that touches the canonical state. Do not use bare `allow` for state writes.
- Branch naming: `feature/`, `fix/`, `chore/` prefixes. PRs require at least one passing simulation test.

---

## Non-Functional Requirements

| Requirement | Target |
|---|---|
| Report generation latency | < 30 seconds |
| Event-to-observation latency | < 2 minutes |
| Critical risk escalation latency | < 5 minutes after threshold breach |
| Dashboard refresh latency | < 60 seconds |
| Platform availability | 99.9% |
| Event processing | At-least-once delivery |
| State updates | Idempotent |
| Policy failure mode | Fail closed |

---

## Phase 1 Build Scope

The minimum viable demo: one complete end-to-end workflow running against a real or synthetic data source.

**Target:** Connect Jira or Smartsheet → blocked task detected → Risk Intelligence Agent scores milestone impact → Communication Agent produces a decision preparation brief → Policy Engine routes for approval → Human approves → Audit log captured. End-to-end in under 2 minutes.

| Phase | Duration | Deliverable |
|---|---|---|
| 1A — Core pipeline | Week 1–2 | Jira ingestion → Signal Quality Pipeline → Canonical State → Execution Monitoring Agent → health score output |
| 1B — Intelligence layer | Week 3–4 | Risk Intelligence + Issue Management live · Program Director conflict resolution · Policy Engine routing |
| 1C — Demo ready | Week 5 | Communication Agent producing styled briefs · Streamlit UI · end-to-end demo script validated |
| 2 — Graph + learning | Month 2 | Knowledge graph online · Context Assembly Layer · Knowledge Agent · Evaluation calibration loop |
| 3 — Enterprise hardening | Month 3 | Failure mode handling · Cost controls · RBAC · Audit logging · Additional integrations |

---

## Contributing

Open an issue before starting significant work. PRs that add agent logic must include unit tests and at least one simulation scenario that exercises the new behavior. Policy changes require a policy test. Do not merge without green tests across all four test categories.

---

## License

Proprietary. See LICENSE for terms.
