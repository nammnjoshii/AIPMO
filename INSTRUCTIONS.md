# INSTRUCTIONS.md

Step-by-step build instructions for Claude Code.
Read this file completely before executing any step.
Execute steps in order. Do not skip ahead.
Verify each checkpoint before proceeding to the next phase.

Reference files:
- @README.md — full project overview, stack, repo structure, conventions
- @CLAUDE.md — hard rules, architecture summary, what not to do

---

## Before You Start

Read README.md and CLAUDE.md in full.
Confirm you understand the following before writing a single line of code:

1. The event flow: signal in → quality pipeline → canonical state + graph → agents → policy → communication → human
2. The three decision tiers: observation, decision_preparation, execution
3. The five policy outcomes: allow, allow_with_audit, approval_required, deny, escalate
4. The hard rule: policy engine failure always fails closed — never open
5. The hard rule: agents never self-assemble context — always use context_assembly/assembler.py
6. The hard rule: all graph queries go through knowledge_graph/query_service.py
7. The hard rule: uncertainty_notes must always be populated in AgentOutput — empty is a bug

If any of these are unclear, re-read the relevant sections in README.md before continuing.

---

## Phase 0 — Project Scaffold

### Step 0.1 — Create directory structure

Create every directory listed in the Repository Structure section of README.md.
Create a `.gitkeep` file in each empty directory so git tracks them.

```
autonomous-pmo/
├── agents/program_director/
├── agents/planning/
├── agents/execution_monitoring/
├── agents/risk_intelligence/
├── agents/issue_management/
├── agents/communication/
├── agents/knowledge/
├── orchestrator/
├── policy/policies/
├── state/
├── signal_quality/
├── context_assembly/
├── knowledge_graph/
├── events/producers/
├── events/consumers/
├── events/schemas/
├── integrations/jira/
├── integrations/github/
├── integrations/slack/
├── integrations/smartsheet/
├── evaluation/
├── simulation/scenarios/
├── simulation/injectors/
├── audit/
├── security/
├── configs/
├── tests/unit/
├── tests/integration/
├── tests/policy/
├── tests/simulation/
├── examples/sample_events/
└── ui/
```

### Step 0.2 — Create requirements.txt

Include all dependencies needed for the full system:

```
# Core
python-dotenv>=1.0.0
pydantic>=2.0.0
pydantic-settings>=2.0.0

# Agent orchestration
langgraph>=0.2.0
langchain-anthropic>=0.2.0
langchain-core>=0.3.0

# LLM
anthropic>=0.40.0

# Databases
asyncpg>=0.29.0
psycopg2-binary>=2.9.9
pgvector>=0.3.0
sqlalchemy>=2.0.0
alembic>=1.13.0

# Knowledge graph
neo4j>=5.20.0

# Event bus
redis>=5.0.0

# API integrations
httpx>=0.27.0
jira>=3.8.0
PyGithub>=2.3.0
slack-sdk>=3.27.0

# Scheduling
apscheduler>=3.10.0

# Config
pyyaml>=6.0.1

# Auth
supabase>=2.4.0

# UI
streamlit>=1.35.0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=5.0.0

# Observability
structlog>=24.0.0
```

### Step 0.3 — Create .env.example

```bash
# Required
ANTHROPIC_API_KEY=
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/autonomous_pmo
REDIS_URL=redis://localhost:6379
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=

# Integrations
JIRA_BASE_URL=
JIRA_API_TOKEN=
JIRA_USER_EMAIL=
GITHUB_TOKEN=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SMARTSHEET_ACCESS_TOKEN=

# Optional
LOG_LEVEL=INFO
ENVIRONMENT=development
TENANT_ID=default
```

### Step 0.4 — Create docker-compose.yml

Define three services: PostgreSQL 16 with pgvector extension, Redis 7, and Neo4j 5.
PostgreSQL must expose port 5432. Redis must expose port 6379. Neo4j must expose ports 7474 and 7687.
Include health checks for all three services.
Include a named volume for each service for data persistence.

### Step 0.5 — Create configs/models.yaml

```yaml
default_model: claude-sonnet-4-20250514

routing:
  communication_agent:
    model: claude-haiku-4-20250307
    rationale: high-volume structured narrative — speed over depth
  execution_monitoring_agent:
    model: claude-haiku-4-20250307
    rationale: pattern detection on structured data
  issue_management_agent:
    model: claude-sonnet-4-20250514
    rationale: classification and root cause — moderate reasoning
  risk_intelligence_agent:
    model: claude-sonnet-4-20250514
    rationale: multi-factor scoring — precision critical
  planning_agent:
    model: claude-sonnet-4-20250514
    rationale: dependency reasoning and estimation — complex inference
  program_director_agent:
    model: claude-sonnet-4-20250514
    rationale: conflict resolution and orchestration — highest demand
  knowledge_agent:
    model: claude-sonnet-4-20250514
    rationale: retrieval and reasoning combined
```

### Step 0.6 — Create configs/policies.yaml

Define default policies for a project scope.
Include all five action outcomes: allow, allow_with_audit, approval_required, deny, escalate.
Include schedule_slip_probability threshold of 0.40 and resource_overload threshold of 0.30.
Map these actions: generate_status_report → allow, update_dashboard → allow,
create_risk_log_entry → allow_with_audit, escalate_issue → approval_required,
modify_schedule → deny, reassign_resources → approval_required.

### Step 0.7 — Create configs/agents.yaml

Define skill registrations for all seven agents.
Each agent entry must include: name, decision_type, allowed_actions, and evaluation_targets.
Use the agent table in README.md as the source of truth.

### Step 0.8 — Create configs/tenants.yaml

Define a default tenant with id: default, name: Default Tenant, and policy_scope: project.

### Step 0.9 — Verify scaffold

Run the following and confirm zero errors:
```bash
find . -type f -name "*.py" | head -20
cat requirements.txt
cat docker-compose.yml
cat configs/models.yaml
```

**Checkpoint 0 complete when:** All directories exist, all config files are valid YAML, docker-compose.yml parses without errors.

---

## Phase 1 — Core Data Contracts

Build the data contracts before any logic. Everything downstream depends on these being correct.

### Step 1.1 — Create state/schemas.py

Define the canonical state Pydantic models. Required models:

`ProjectIdentity` — project_id, name, project_type, objectives, scope_summary

`Milestone` — id, name, status (Literal: on_track, at_risk, delayed, complete), target_date, confidence_score

`HealthMetrics` — schedule_health (float 0-1), delivery_confidence (float 0-1), open_blockers (int), last_updated (datetime)

`SourceReliabilityProfile` — jira, github, slack, meeting_notes, manual_reports — each a Literal: high, medium, low

`CanonicalProjectState` — project_id, name, objectives, milestones (list), health_metrics, source_reliability_profile, decision_history (list), last_signal_at (datetime)

All models use Pydantic v2. All datetime fields are timezone-aware. All float fields are constrained between 0.0 and 1.0 where applicable.

### Step 1.2 — Create events/schemas/event_types.py

Define the event Pydantic models. Required models:

`DeliveryEvent` — event_type (str), event_id (str), timestamp (datetime), project_id (str), source (str), payload (Dict)

`TaskUpdatedPayload` — task_id, old_status, new_status, assignee, dependency_ids (list)

`MilestoneUpdatedPayload` — milestone_id, old_status, new_status, target_date, moved_by_days (int)

`RiskDetectedPayload` — risk_id, severity (Literal: low, medium, high, critical), description, affected_milestone_ids (list)

`DependencyBlockedPayload` — dependency_id, blocking_task_id, blocked_task_id, blocker_severity

Define an EventType enum covering: task.updated, milestone.updated, risk.detected, dependency.blocked, issue.detected, stakeholder.requested_update, status.reporting_cycle_started

### Step 1.3 — Create agents/base_agent.py

Define the base agent contract. Required classes:

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum

class DecisionType(str, Enum):
    OBSERVATION = "observation"
    DECISION_PREPARATION = "decision_preparation"
    EXECUTION = "execution"

class PolicyAction(str, Enum):
    ALLOW = "allow"
    ALLOW_WITH_AUDIT = "allow_with_audit"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"
    ESCALATE = "escalate"

@dataclass
class AgentInput:
    project_id: str
    event_type: str
    canonical_state: Dict[str, Any]
    graph_context: Dict[str, Any]
    historical_cases: List[Any]
    policy_context: Dict[str, Any]
    signal_quality: Dict[str, Any]

@dataclass
class AgentOutput:
    agent_name: str
    decision_type: DecisionType
    confidence_score: float
    evidence: List[str]
    decision_factors: List[str]
    recommendation: Optional[str]
    proposed_state_updates: Dict[str, Any]
    proposed_graph_updates: List[Dict[str, Any]]
    policy_action: PolicyAction
    uncertainty_notes: List[str]

    def __post_init__(self):
        # uncertainty_notes must never be empty — this is enforced at the contract level
        if not self.uncertainty_notes:
            raise ValueError(
                "uncertainty_notes must be populated. "
                "If confidence is high, state that explicitly. Empty is a bug."
            )
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError("confidence_score must be between 0.0 and 1.0")

class BaseAgent:
    name: str
    decision_type: DecisionType

    def run(self, data: AgentInput) -> AgentOutput:
        raise NotImplementedError(f"{self.__class__.__name__} must implement run()")
```

### Step 1.4 — Create knowledge_graph/graph_schema.py

Define NodeType and EdgeType enums.

NodeType enum values: PROJECT, PROGRAM, PORTFOLIO, MILESTONE, TASK, DEPENDENCY, TEAM, PERSON, STAKEHOLDER, BUSINESS_CAPABILITY, SYSTEM, RISK, ISSUE, DECISION, OUTCOME, DOCUMENT, COMMUNICATION_ARTIFACT

EdgeType enum values: DEPENDS_ON, BLOCKS, OWNS, ASSIGNED_TO, REPORTS_TO, INFLUENCES, APPROVED_BY, IMPACTS, RELATED_TO, MITIGATED_BY, CAUSED_BY, PART_OF, DELIVERED_BY, ESCALATED_TO, LEARNED_FROM

Define GraphNode dataclass: id (str), type (NodeType), name (str), attributes (Dict)
Define GraphEdge dataclass: from_id (str), to_id (str), type (EdgeType), attributes (Dict)

### Step 1.5 — Write unit tests for all contracts

Create tests/unit/test_contracts.py.
Test that AgentOutput raises ValueError when uncertainty_notes is empty.
Test that AgentOutput raises ValueError when confidence_score is outside 0.0-1.0.
Test that all EventType enum values are valid strings.
Test that CanonicalProjectState serializes and deserializes correctly.
Test that all NodeType and EdgeType enum values are defined.

Run: `pytest tests/unit/test_contracts.py -v`
All tests must pass before proceeding.

**Checkpoint 1 complete when:** All contracts defined, all unit tests pass, zero import errors across all schema files.

---

## Phase 2 — Infrastructure Layer

### Step 2.1 — Create state/canonical_state.py

Implement a CanonicalStateStore class with these methods:

`async get(project_id: str) -> Optional[CanonicalProjectState]` — read from PostgreSQL
`async upsert(state: CanonicalProjectState) -> bool` — idempotent write, update only changed fields
`async update_health(project_id: str, metrics: HealthMetrics) -> bool`
`async append_decision(project_id: str, decision: Dict) -> bool`

All writes are idempotent. Use ON CONFLICT DO UPDATE in SQL.
Use SQLAlchemy async with asyncpg driver.
Every write emits an audit event via audit/logger.py (stub the logger for now — implement in Phase 5).

### Step 2.2 — Create state/normalization.py

Implement a SignalNormalizer class with one method:
`normalize(raw_signal: Dict, source: str) -> DeliveryEvent`

It must handle null fields gracefully — missing fields default to safe values, never raise on incomplete input.
It must attach source reliability from the source_reliability_profile in canonical state.
Log a warning when a required field is missing, but do not raise.

### Step 2.3 — Create state/reliability.py

Implement a SourceReliabilityScorer class.
It maintains per-project, per-source reliability scores in memory (Phase 1) with PostgreSQL persistence (Phase 2).

`score(project_id: str, source: str) -> Literal["high", "medium", "low"]`
`update(project_id: str, source: str, outcome: Literal["accurate", "inaccurate", "stale"])` — updates rolling score

Default scores: jira → medium, github → high, slack → low, meeting_notes → medium, manual_reports → medium

### Step 2.4 — Create signal_quality/confidence_decay.py

Implement a ConfidenceDecayCalculator class.

Define decay windows as class-level constants matching README.md exactly:
- jira: high confidence 24h, decay trigger 48h
- github: high confidence 72h, decay trigger 120h (5 days)
- slack: high confidence 4h, decay trigger 24h
- meeting_notes: high confidence 48h, decay trigger 72h
- manual_reports: high confidence 168h (7 days), decay trigger at next cycle

`calculate(source: str, last_updated: datetime) -> float` — returns confidence score 0.0 to 1.0
`is_decayed(source: str, last_updated: datetime) -> bool` — returns True if past decay trigger

### Step 2.5 — Create signal_quality/noise_filter.py

Implement a NoiseFilter class.

`is_duplicate(event: DeliveryEvent, window_seconds: int = 300) -> bool` — checks Redis for recent identical events using event hash
`is_low_signal(event: DeliveryEvent) -> bool` — returns True for: bot-generated events, events with no payload content, status moves to the same status

Use Redis for deduplication window storage. Key format: `dedup:{project_id}:{event_hash}`

### Step 2.6 — Create signal_quality/missing_data.py

Implement a MissingDataDetector class.

`detect_gaps(project_id: str, state: CanonicalProjectState) -> List[str]` — returns list of gap descriptions
`is_sparse(project_id: str, state: CanonicalProjectState) -> bool` — returns True if coverage is insufficient for confident reasoning

Gap detection rules:
- No signal from any source in 48 hours → gap
- Milestone within 7 days of target_date with no task completion in 72 hours → gap
- Any milestone with status at_risk and no risk mitigation signal in 24 hours → gap

### Step 2.7 — Create signal_quality/source_profiles.py

Implement a SourceProfileManager that loads and updates source reliability profiles per project.
Reads from PostgreSQL. Provides `get_profile(project_id: str) -> SourceReliabilityProfile`.
Initializes new projects with default profile from configs.

### Step 2.8 — Create signal_quality/pipeline.py

Implement the main SignalQualityPipeline class. This is the single entry point for all signal ingestion.

```python
class SignalQualityPipeline:
    def process(self, raw_signal: Dict, source: str, project_id: str) -> QualifiedSignal:
        # 1. Normalize the raw signal
        # 2. Check for duplicates — return early if duplicate
        # 3. Check for low signal — return early with is_low_signal=True flag
        # 4. Score source reliability
        # 5. Calculate confidence decay
        # 6. Detect missing data gaps
        # 7. Determine if sparsity alert is needed
        # 8. Return QualifiedSignal with all quality metadata attached
```

Define QualifiedSignal dataclass:
event (DeliveryEvent), source_reliability (str), confidence_score (float),
is_decayed (bool), gaps (List[str]), sparsity_alert (bool), sparsity_message (Optional[str])

Sparsity alert message format: "Insufficient signal coverage for project {project_id}. Last update: {source} at {time}. Confidence: LOW. Manual PM check-in recommended before escalation."

### Step 2.9 — Write unit tests for infrastructure layer

Create tests/unit/test_signal_quality.py. Test:
- Duplicate events are filtered within the deduplication window
- Confidence decay returns correct score for each source at various ages
- Missing data detection fires correctly for each gap rule
- Sparsity alert is triggered when gaps exceed threshold
- Pipeline processes a valid event end-to-end and returns QualifiedSignal

Create tests/unit/test_canonical_state.py. Test:
- Upsert is idempotent — calling twice with same data produces one record
- Health metrics update without overwriting other fields
- get returns None for unknown project_id

Run: `pytest tests/unit/ -v`
All tests must pass.

**Checkpoint 2 complete when:** All infrastructure classes implemented, all unit tests pass, pipeline processes a sample event from examples/sample_events/ without error.

---

## Phase 3 — Policy Engine

Build the policy engine before building agents. Agents will call it.

### Step 3.1 — Create policy/schemas.py

Define policy Pydantic models:

`ActionPolicy` — action_name (str), outcome (PolicyAction)
`ThresholdPolicy` — metric_name (str), operator (Literal: gt, lt, gte, lte), value (float), outcome (PolicyAction)
`ProjectPolicy` — version (str), scope (str), project_id (str), actions (List[ActionPolicy]), thresholds (List[ThresholdPolicy])
`PolicyEvaluationResult` — action (str), outcome (PolicyAction), policy_version (str), matched_rule (str), timestamp (datetime)

### Step 3.2 — Create policy/engine.py

Implement PolicyEngine class.

`load(config_path: str)` — loads YAML policy file, validates against ProjectPolicy schema
`evaluate(action: str, project_id: str, context: Dict) -> PolicyEvaluationResult`
`evaluate_threshold(metric: str, value: float, project_id: str) -> PolicyEvaluationResult`
`reload()` — hot-reload policies without restart

**Critical failure mode — implement this first and test it before anything else:**
If the policy engine raises any unhandled exception during evaluation, the default outcome is DENY.
This must be enforced by a try/except wrapper at the top level of evaluate().
No exception should ever propagate to the caller as an unhandled error.

Policy precedence order (highest to lowest):
1. Regulatory / compliance (hardcoded — never overridable)
2. Organization-wide
3. Portfolio / program
4. Project-specific
5. Agent / skill-specific

When multiple policies match, the highest-precedence policy wins.

CLI support:
`python -m policy.engine --load configs/policies.yaml` → loads and validates
`python -m policy.engine --validate configs/policies.yaml` → validates without loading
`python -m policy.engine --reload` → hot-reloads running engine

### Step 3.3 — Write policy tests

Create tests/policy/test_policy_engine.py. Test every outcome:

- Action mapped to `allow` returns ALLOW
- Action mapped to `allow_with_audit` returns ALLOW_WITH_AUDIT
- Action mapped to `approval_required` returns APPROVAL_REQUIRED
- Action mapped to `deny` returns DENY
- Unknown action defaults to DENY (fail closed)
- Engine exception during evaluation returns DENY (fail closed)
- Threshold above escalate_if_greater_than returns ESCALATE
- Threshold below notify_if_greater_than returns ALLOW
- Policy reload updates in-memory rules without restart

Run: `pytest tests/policy/ -v`
All tests must pass before building any agent.

**Checkpoint 3 complete when:** Policy engine handles all five outcomes, fail-closed behavior is proven by test, CLI commands work.

---

## Phase 4 — Context Assembly

Agents depend on this. Build it before agents.

### Step 4.1 — Create context_assembly/state_slicer.py

Implement StateSlicer class.
`slice(state: CanonicalProjectState, event_type: str) -> Dict`

Returns only the canonical state fields relevant to the event type.
For task.updated → include milestones, health_metrics, source_reliability_profile
For risk.detected → include milestones, health_metrics, decision_history
For dependency.blocked → include milestones, health_metrics, source_reliability_profile, decision_history
Never return the full state object — always slice.

### Step 4.2 — Create context_assembly/graph_neighborhood.py

Implement GraphNeighborhoodFetcher class.
`fetch(entity_id: str, hops: int = 2) -> Dict`

Returns the graph neighborhood up to the specified hop depth.
Default is 2 hops. Program Director Agent may request 3 hops.
Returns empty dict if graph is unavailable — do not raise. Log the unavailability.
Include a `graph_available: bool` field in the return dict so callers know if the graph was reachable.

### Step 4.3 — Create context_assembly/case_matcher.py

Implement CaseMatcher class.
`match(event: DeliveryEvent, state: CanonicalProjectState, top_k: int = 3) -> List[Dict]`

Matches current delivery context against historical cases stored in pgvector.
Returns top-k most similar past cases with their outcomes.
Returns empty list gracefully if no historical cases exist yet.
Each returned case must include: case_id, similarity_score, event_type, resolution, outcome.

### Step 4.4 — Create context_assembly/assembler.py

Implement ContextAssembler class. This is the single entry point all agents must use.

```python
class ContextAssembler:
    def assemble(
        self,
        event: DeliveryEvent,
        state: CanonicalProjectState,
        qualified_signal: QualifiedSignal,
        agent_name: str,
        policy_context: Dict
    ) -> AgentInput:
        # 1. Slice canonical state to relevant fields only
        # 2. Fetch 2-hop graph neighborhood (3 hops for program_director)
        # 3. Match top-3 historical cases
        # 4. Attach policy context
        # 5. Attach signal quality metadata
        # 6. Return AgentInput — never return raw state or full graph
```

Log context assembly time. If assembly takes longer than 5 seconds, log a warning.

### Step 4.5 — Write unit tests for context assembly

Create tests/unit/test_context_assembly.py. Test:
- StateSlicer returns different slices for different event types
- StateSlicer never returns the full state object
- GraphNeighborhoodFetcher returns empty dict (not an exception) when graph is unavailable
- ContextAssembler returns a valid AgentInput for each event type
- ContextAssembler does not include cross-project data in the assembled context

Run: `pytest tests/unit/test_context_assembly.py -v`

**Checkpoint 4 complete when:** All context assembly classes implemented, assembler returns valid AgentInput for all event types, graph unavailability handled gracefully.

---

## Phase 5 — Audit and Security

Build these before agents touch any data.

### Step 5.1 — Create audit/logger.py

Implement AuditLogger class.

`log(event_type: str, actor: str, action: str, project_id: str, inputs: List[str], outputs: List[str], policy_result: PolicyAction)` — writes an append-only audit record to PostgreSQL

Audit record schema:
event_id (uuid), timestamp (datetime, timezone-aware), actor (str), action (str),
project_id (str), inputs (JSONB), outputs (JSONB), policy_result (str)

Records are append-only. The table must have no UPDATE or DELETE permissions for the application role.

Logged event types (match README.md exactly):
signal_ingested, state_update_proposed, graph_update_proposed, policy_evaluated,
recommendation_generated, human_override, automated_action_executed, evaluation_metric_updated

### Step 5.2 — Create security/rbac.py

Define role enum: EXECUTIVE_SPONSOR, PROGRAM_DIRECTOR, PROJECT_MANAGER, TEAM_MEMBER, AUDITOR, AGENT_RUNTIME

Define permission enum: VIEW_PORTFOLIO, VIEW_PROGRAM, VIEW_PROJECT, VIEW_TASK, VIEW_AUDIT_LOGS, APPROVE_ESCALATION, GENERATE_REPORT, MODIFY_POLICY

Implement has_permission(role: Role, permission: Permission) -> bool using a static permission matrix matching README.md role definitions.

### Step 5.3 — Create security/isolation.py

Implement TenantIsolation class.
`get_project_scope(tenant_id: str, project_id: str) -> Dict` — returns the allowed data scope for this tenant/project combination
`is_cross_project_allowed(tenant_id: str, source_project: str, target_project: str) -> bool` — returns False by default, True only for explicit portfolio-level grants

### Step 5.4 — Create security/secrets.py

Implement a SecretsManager class that loads all credentials from environment variables only.
Raise a clear error at startup if any required secret is missing.
Never log secret values — log only the secret name and whether it was found.
Provide typed accessors: get_anthropic_key(), get_database_url(), get_jira_credentials(), etc.

**Checkpoint 5 complete when:** Audit logger writes append-only records, RBAC matrix covers all roles, SecretsManager raises on missing required secrets.

---

## Phase 6 — Agents (Phase 1A Agents First)

Build agents in dependency order. Execution Monitoring first — it is observation-only and requires no other agent output.

### Step 6.1 — Create agents/execution_monitoring/agent.py

Implement ExecutionMonitoringAgent extending BaseAgent.

Decision type: OBSERVATION — this agent never calls the policy engine directly.

Skills to implement:
- `schedule_variance_detection(state_slice: Dict) -> Dict` — detects milestone slippage, returns variance days and severity
- `throughput_analysis(state_slice: Dict) -> Dict` — analyzes task completion rate vs plan
- `bottleneck_detection(state_slice: Dict, graph_context: Dict) -> Dict` — identifies blocked critical path items

`run(data: AgentInput) -> AgentOutput`:
1. Run all three skills
2. Compute delivery health score (float 0-1)
3. Identify top 3 bottlenecks
4. Return AgentOutput with decision_type=OBSERVATION, policy_action=ALLOW
5. uncertainty_notes must describe confidence level based on signal quality — never empty

The agent must not import from any other agent folder.
The agent must not call context_assembly directly — it receives pre-assembled AgentInput.

### Step 6.2 — Create agents/issue_management/agent.py

Implement IssueManagementAgent extending BaseAgent.

Decision type: OBSERVATION for detection, DECISION_PREPARATION for escalation recommendations.

Skills to implement:
- `blocker_classification(payload: Dict) -> Dict` — classifies blocker by severity: low, medium, high, critical
- `root_cause_pattern_matching(issue: Dict, historical_cases: List) -> Dict` — matches against known root cause patterns
- `severity_estimation(issue: Dict, state_slice: Dict) -> float` — estimates impact score 0-1

`run(data: AgentInput) -> AgentOutput`:
1. Classify the triggering event as a blocker (or not)
2. If blocker: run root cause matching, estimate severity
3. If severity > 0.7: set decision_type=DECISION_PREPARATION, policy_action=APPROVAL_REQUIRED
4. If severity <= 0.7: set decision_type=OBSERVATION, policy_action=ALLOW_WITH_AUDIT
5. uncertainty_notes must include data quality caveats from signal_quality context

### Step 6.3 — Create agents/risk_intelligence/agent.py

Implement RiskIntelligenceAgent extending BaseAgent.

Decision type: DECISION_PREPARATION for material risks.

Skills to implement:
- `risk_scoring(state_slice: Dict, graph_context: Dict) -> Dict` — scores risk by probability and impact
- `risk_propagation_analysis(risk: Dict, graph_context: Dict) -> Dict` — traces risk through dependency graph
- `mitigation_recommendation(risk: Dict, historical_cases: List) -> List[Dict]` — returns ranked mitigation options

`run(data: AgentInput) -> AgentOutput`:
1. Score the risk from the triggering event
2. Run propagation analysis using graph context
3. If risk score > 0.40: set decision_type=DECISION_PREPARATION, policy_action=ESCALATE
4. If risk score 0.20-0.40: set policy_action=APPROVAL_REQUIRED
5. If risk score < 0.20: set decision_type=OBSERVATION, policy_action=ALLOW_WITH_AUDIT
6. Always include mitigation_recommendation in proposed_state_updates
7. uncertainty_notes must state confidence level and any missing graph context

Evaluation targets for this agent (from README.md):
- Precision > 80%, Recall > 70%, False Positive Rate < 15%, Time-to-Detection < 24 hours

### Step 6.4 — Create agents/communication/agent.py

Implement CommunicationAgent extending BaseAgent.

Decision type: EXECUTION — this agent generates outputs, not recommendations.

Skills to implement:
- `executive_summary_generation(state: Dict, risks: List, issues: List) -> str` — plain language executive brief
- `stakeholder_personalization(summary: str, role: str) -> str` — tailors language and detail level to role
- `decision_preparation_brief(risk_output: AgentOutput, issue_output: AgentOutput) -> str` — merged human-ready brief

`run(data: AgentInput) -> AgentOutput`:
1. Extract risk and issue context from canonical state slice
2. Generate decision preparation brief
3. Personalize for the target stakeholder role in policy_context
4. Set decision_type=EXECUTION, policy_action=ALLOW
5. Include the generated brief as a string in proposed_state_updates["brief"]
6. uncertainty_notes must note if any input agents had low confidence

Evaluation targets: Human Acceptance Rate > 90%, Edit Rate < 20%, Latency < 30 seconds.

Use the model assigned to communication_agent in configs/models.yaml — do not hardcode model name in agent code.

### Step 6.5 — Create agents/knowledge/agent.py

Implement KnowledgeAgent extending BaseAgent.

Decision type: OBSERVATION.

Skills to implement:
- `lesson_extraction(outcome: Dict, state: Dict) -> Dict` — extracts a reusable lesson from a resolved issue or completed project
- `cross_project_lesson_retrieval(context: Dict) -> List[Dict]` — retrieves relevant lessons from pgvector
- `mitigation_effectiveness_lookup(mitigation_type: str) -> Dict` — looks up historical effectiveness of a mitigation

`run(data: AgentInput) -> AgentOutput`:
1. Determine if the event produces an extractable lesson
2. Retrieve top-3 relevant cross-project lessons
3. Return OBSERVATION-tier output with lessons in proposed_state_updates
4. uncertainty_notes must state how many historical cases were available for matching

### Step 6.6 — Create agents/planning/agent.py

Implement PlanningAgent extending BaseAgent.

Decision type: DECISION_PREPARATION.

Skills to implement:
- `wbs_generation(objectives: List[str], scope: str) -> Dict` — generates work breakdown structure
- `dependency_mapping(tasks: List[Dict]) -> Dict` — builds dependency graph for the task list
- `resource_need_estimation(wbs: Dict, historical_cases: List) -> Dict` — estimates resource requirements
- `historical_project_similarity(objectives: List[str]) -> List[Dict]` — finds similar past projects for calibration

`run(data: AgentInput) -> AgentOutput`:
1. Generate WBS from objectives in canonical state
2. Map dependencies
3. Estimate resources using historical cases
4. Return DECISION_PREPARATION output with plan in proposed_state_updates
5. uncertainty_notes must state estimation confidence and historical case count

### Step 6.7 — Create agents/program_director/agent.py

Implement ProgramDirectorAgent extending BaseAgent.

This is the orchestrator. It does not implement domain skills — it coordinates other agents.

`run(data: AgentInput) -> AgentOutput`: Routes single events to the appropriate agent.

`merge(outputs: List[AgentOutput]) -> AgentOutput`: Merges parallel agent outputs. When outputs conflict, calls resolve().

`resolve(conflict: List[AgentOutput]) -> AgentOutput`: Implements conflict arbitration.
Compare confidence scores. Compare evidence quality (number of evidence items).
If one output has confidence > 0.2 higher than others, prefer it.
If confidence scores are close (within 0.2), produce a merged statement combining both conclusions.
If irresolvable, set policy_action=ESCALATE and explain the conflict in recommendation.

The merged output must set agent_name="program_director_agent" and include the source agent names in evidence.

### Step 6.8 — Write unit tests for all agents

Create tests/unit/test_agents.py. For each agent test:
- run() returns valid AgentOutput
- uncertainty_notes is never empty
- confidence_score is between 0.0 and 1.0
- decision_type matches the agent's intended tier
- Agent does not import from sibling agent folders (import check)

Create a shared mock AgentInput fixture in tests/unit/conftest.py.

Run: `pytest tests/unit/test_agents.py -v`
All tests must pass.

**Checkpoint 6 complete when:** All seven agents implemented, all unit tests pass, no agent imports from sibling agent folders.

---

## Phase 7 — Orchestrator

### Step 7.1 — Create orchestrator/conflict_resolver.py

Implement ConflictResolver class.

`detect_conflict(outputs: List[AgentOutput]) -> bool` — returns True if outputs contain materially inconsistent conclusions
`resolve(outputs: List[AgentOutput]) -> AgentOutput` — implements the conflict resolution logic from Step 6.7

A conflict exists when: two agents produce opposing risk assessments (one says stable, one says elevated) AND confidence scores are both above 0.5.

The resolved output must include: conflict_detected: true in proposed_state_updates, and a merged_statement in recommendation that honestly represents both conclusions.

### Step 7.2 — Create orchestrator/event_router.py

Implement EventRouter class.

Define coordination pattern for each event type:

`task.updated` → Sequential: Issue Management → Risk Intelligence → Program Director merge → Policy → Communication

`milestone.updated` → Sequential: Execution Monitoring → Risk Intelligence → Program Director merge → Policy → Communication

`dependency.blocked` → Parallel: [Issue Management, Execution Monitoring] → Program Director merge → Risk Intelligence → Policy → Communication

`risk.detected` → Sequential: Risk Intelligence → Program Director → Policy → Communication

`status.reporting_cycle_started` → Execution Monitoring → Communication (no escalation path)

`route(event: DeliveryEvent, agents: Dict[str, BaseAgent]) -> List[AgentOutput]` — executes the correct pattern and returns all agent outputs

### Step 7.3 — Create orchestrator/runtime.py

Implement AgentRuntime class.

`initialize()` — loads all agents, loads model routing from configs/models.yaml, verifies all agent names match config
`get_agent(name: str) -> BaseAgent` — returns initialized agent instance
`health_check() -> Dict` — returns health status for all agents and their dependencies

### Step 7.4 — Create orchestrator/main.py

Implement the LangGraph entry point.

Define the delivery intelligence graph:
- Node: ingest_signal — runs SignalQualityPipeline
- Node: update_state — writes QualifiedSignal to CanonicalStateStore
- Node: route_to_agents — calls EventRouter
- Node: evaluate_policy — calls PolicyEngine for each AgentOutput
- Node: execute_or_queue — executes allowed actions, queues approval_required actions
- Node: generate_brief — calls CommunicationAgent
- Node: log_audit — calls AuditLogger for the full chain

Edge: ingest_signal → update_state → route_to_agents → evaluate_policy → execute_or_queue → generate_brief → log_audit

Conditional edge from evaluate_policy: if outcome is DENY → log_audit (skip execution). If ESCALATE → human review queue.

Subscribe to Redis Streams for events. Process events as they arrive.

### Step 7.5 — Write integration tests for orchestrator

Create tests/integration/test_orchestrator.py.

Test the full flow using mocked agents and a real PostgreSQL instance (use pytest fixtures with a test database):
- task.updated event flows through sequential pattern and produces a decision preparation brief
- dependency.blocked event triggers parallel analysis and conflict resolution if outputs disagree
- Policy engine DENY stops execution and logs the denial
- Audit log contains a complete record for every step in the chain

Run: `pytest tests/integration/ -v`

**Checkpoint 7 complete when:** Orchestrator processes a sample event end-to-end, all integration tests pass, audit log contains complete records.

---

## Phase 8 — Integrations

### Step 8.1 — Create integrations/jira/adapter.py

Implement JiraAdapter class.

`connect()` — authenticates using JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL from environment
`fetch_project(project_key: str) -> Dict` — fetches project metadata
`fetch_issues(project_key: str, updated_since: datetime) -> List[Dict]` — fetches issues updated since timestamp
`to_delivery_event(issue: Dict) -> DeliveryEvent` — converts Jira issue to normalized DeliveryEvent

The adapter emits events — it does not write to canonical state directly.
All output goes to the SignalQualityPipeline first.

### Step 8.2 — Create integrations/jira/bootstrap.py

Implement the bootstrap script.
Verifies connection, fetches active projects, registers them in canonical state with default CanonicalProjectState.
Prints a summary of projects found and initial health metrics.

CLI: `python -m integrations.jira.bootstrap`

### Step 8.3 — Create integrations/jira/webhook.py

Implement a FastAPI webhook endpoint that receives Jira webhook events.
Validate the webhook signature.
Parse the payload into a DeliveryEvent.
Push the event to Redis Streams.

### Step 8.4 — Create integrations/smartsheet/adapter.py

Follow the same pattern as the Jira adapter.
Use SMARTSHEET_ACCESS_TOKEN from environment.
Map Smartsheet rows to tasks and sheets to projects.

### Step 8.5 — Create events/producers/redis_producer.py

Implement RedisEventProducer.
`publish(event: DeliveryEvent)` — serializes and pushes to Redis Streams
Stream name format: `events:{project_id}`

### Step 8.6 — Create events/consumers/redis_consumer.py

Implement RedisEventConsumer.
`consume(project_ids: List[str], handler: Callable)` — reads from Redis Streams for the given project IDs and calls handler for each event
Handles connection drops with exponential backoff retry.
Uses consumer groups for at-least-once delivery.

### Step 8.7 — Create examples/sample_events/task_blocked.json

Create a realistic sample event for a blocked Jira task:

```json
{
  "event_type": "task.updated",
  "event_id": "ev_sample_001",
  "timestamp": "2026-03-15T16:10:00Z",
  "project_id": "proj_demo_001",
  "source": "jira",
  "payload": {
    "task_id": "T-144",
    "old_status": "in_progress",
    "new_status": "blocked",
    "assignee": "team_integration",
    "dependency_ids": ["T-122", "T-131"]
  }
}
```

Create a matching sample canonical state in examples/sample_events/canonical_state_demo.json with two milestones (one at_risk), health metrics showing schedule_health: 0.68, and three open blockers.

**Checkpoint 8 complete when:** Jira adapter connects, bootstrap script runs without error, sample event processes end-to-end through the full orchestrator chain.

---

## Phase 9 — Simulation

### Step 9.1 — Create simulation/scenarios/program_alpha.yaml

Define the reference simulation scenario exactly as specified in README.md:

```yaml
name: program_alpha
description: Reference simulation — 12 projects, 4 shared teams, 120 tasks, 8 milestones
projects: 12
shared_teams: 4
tasks: 120
milestones: 8
injected_failures:
  - type: dependency_failure
    count: 3
    timing: staggered_hours
    hours: [6, 24, 48]
  - type: capacity_overload
    count: 1
    team: team_platform_alpha
    overload_pct: 0.35
    timing_hours: 12
  - type: scope_creep
    count: 2
    signal_type: silent
    timing_hours: [18, 36]
  - type: critical_blocker
    count: 1
    signal_type: late_surfacing
    timing_hours: 72
expected_detections:
  risk_intelligence:
    min_true_positives: 4
    max_false_positives: 1
  issue_management:
    min_true_positives: 3
    max_false_positives: 1
```

### Step 9.2 — Create simulation/harness.py

Implement SimulationHarness class.

`load_scenario(path: str) -> Dict` — loads and validates a scenario YAML
`generate_events(scenario: Dict) -> List[DeliveryEvent]` — generates synthetic events matching the scenario
`run(scenario_path: str)` — runs the full scenario through the orchestrator and collects results
`evaluate(results: List, expected: Dict) -> Dict` — compares detected events to expected detections, reports precision and recall

CLI: `python -m simulation.harness --scenario simulation/scenarios/program_alpha.yaml`

### Step 9.3 — Create tests/simulation/test_program_alpha.py

Run the program_alpha scenario and assert:
- Risk Intelligence Agent detects at least 4 of 5 injected risk signals
- Issue Management Agent detects at least 3 of 4 injected blockers
- False positive rate stays below 15% for risk, below 10% for issues
- At least one decision preparation brief is generated for the capacity overload
- Audit log contains records for every agent action in the chain

Run: `pytest tests/simulation/ -v`

**Checkpoint 9 complete when:** program_alpha scenario runs end-to-end, detection targets met, simulation test passes.

---

## Phase 10 — Knowledge Graph

### Step 10.1 — Create knowledge_graph/graph_store.py

Implement Neo4jGraphStore class.

`connect()` — connects using NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD from environment
`upsert_node(node: GraphNode)` — creates or updates a node with MERGE
`upsert_edge(edge: GraphEdge)` — creates or updates a relationship with MERGE
`health_check() -> bool` — returns True if Neo4j is reachable within 5 seconds

All operations use MERGE — never CREATE without MERGE (prevents duplicates).

### Step 10.2 — Create knowledge_graph/query_service.py

Implement GraphQueryService. All Cypher queries live here — nowhere else.

Implement these query methods matching the patterns in README.md:

`get_projects_sharing_system(system_id: str) -> List[str]`
`get_common_blocker_teams(portfolio_id: str) -> List[Dict]`
`get_stakeholder_approval_delays(portfolio_id: str) -> List[Dict]`
`get_risks_by_root_cause(root_cause_pattern: str) -> List[Dict]`
`get_effective_mitigations(issue_type: str) -> List[Dict]`
`get_team_dependency_exposure(team_id: str, capacity_reduction_pct: float) -> List[Dict]`
`get_neighborhood(entity_id: str, hops: int = 2) -> Dict`

### Step 10.3 — Create knowledge_graph/entity_extractor.py

Implement EntityExtractor class.
`extract(event: DeliveryEvent, state: CanonicalProjectState) -> List[GraphNode]` — extracts entities from a delivery event
`extract_relationships(event: DeliveryEvent, nodes: List[GraphNode]) -> List[GraphEdge]` — infers relationships between extracted entities

Only extract entities and relationships within the defined graph scope (README.md Knowledge Graph section).
Do not create task-level nodes.

### Step 10.4 — Create knowledge_graph/graph_sync.py

Implement GraphSyncPipeline.
`sync(event: DeliveryEvent, state: CanonicalProjectState)` — orchestrates entity extraction → relationship building → graph update → audit log

### Step 10.5 — Update context_assembly/graph_neighborhood.py

Replace the stub with real Neo4j queries using GraphQueryService.
Maintain the graceful fallback to empty dict when Neo4j is unavailable.

**Checkpoint 10 complete when:** Graph store connects, neighborhood queries return valid results, entity extraction produces correct nodes for sample events.

---

## Phase 11 — Evaluation

### Step 11.1 — Create evaluation/metrics.py

Implement MetricsTracker class.

Track per-agent metrics matching README.md evaluation targets:
- precision, recall, false_positive_rate, time_to_detection (Risk Intelligence, Issue Management)
- human_acceptance_rate, edit_rate, report_latency (Communication)

`record_detection(agent: str, was_valid: bool)` — updates precision/recall counters
`record_human_feedback(agent: str, accepted: bool, edited: bool)` — updates acceptance and edit rate
`get_report() -> Dict` — returns current metrics vs targets for all agents
`check_targets() -> List[str]` — returns list of metrics currently below target

CLI: `python -m evaluation.metrics --report`

### Step 11.2 — Create evaluation/labeling.py

Implement FeedbackLabeler class.
`record_acceptance(output_id: str, accepted: bool, edited: bool, override_reason: Optional[str])`
`get_labels(agent: str, since: datetime) -> List[Dict]`

Over-trust detection: if a user accepts > 95% of outputs from a single agent over 30 days, log a warning that their feedback is being down-weighted in calibration.

### Step 11.3 — Create evaluation/calibration.py

Implement CalibrationLoop class.
`run()` — reads labels, recomputes threshold recommendations, writes to a calibration_recommendations table
`get_recommendations() -> List[Dict]` — returns current threshold tuning recommendations

Recommendations are suggestions only — they do not auto-apply. A human must review and apply.

**Checkpoint 11 complete when:** Metrics tracker records feedback, report shows all agents and their target gaps, calibration produces recommendations.

---

## Phase 12 — Demo UI

### Step 12.1 — Create ui/app.py

Build a Streamlit application with four views:

**Portfolio Health** — shows all active projects with health score, open blockers, and milestone status. Color-coded: green (>0.8), yellow (0.5-0.8), red (<0.5).

**Decision Queue** — shows pending approval_required items. Each item shows the decision preparation brief, evidence, confidence score, and approve/reject buttons.

**Risk Feed** — live stream of the last 20 risk and issue detections with agent name, confidence score, and escalation status.

**Explainability Panel** — expandable rationale view for any item showing: triggering evidence, contributing agents, confidence score, uncertainty notes, policy outcome, and recommended next step.

Every output displays a confidence badge: HIGH (>0.75), MEDIUM (0.5-0.75), LOW (<0.5).
LOW confidence outputs are visually distinct (yellow border) and require PM acknowledgment before actioning.

### Step 12.2 — Create examples/run_sample_event.py

Build a demo script that:
1. Loads examples/sample_events/task_blocked.json
2. Loads examples/sample_events/canonical_state_demo.json into canonical state
3. Processes the event through the full orchestrator chain
4. Prints a formatted summary showing: signal quality result, each agent output with confidence, policy decision, and the final decision preparation brief
5. Confirms the audit log entry was written

CLI: `python -m examples.run_sample_event`

**Checkpoint 12 complete when:** Streamlit UI launches, portfolio health view shows demo project, decision queue shows the sample event brief, run_sample_event.py completes end-to-end in under 2 minutes.

---

## Final Verification

Before declaring the build complete, run all of the following:

```bash
# Full test suite
pytest tests/ -v --cov=. --cov-report=term-missing

# Sample event end-to-end
python -m examples.run_sample_event

# Program Alpha simulation
python -m simulation.harness --scenario simulation/scenarios/program_alpha.yaml

# Evaluation report
python -m evaluation.metrics --report

# Policy validation
python -m policy.engine --validate configs/policies.yaml

# Agent metrics
python -m evaluation.metrics --report
```

### Final Checklist

Confirm each item before closing the build:

- [ ] All four test categories pass: unit, integration, policy, simulation
- [ ] program_alpha simulation meets detection targets from README.md
- [ ] Policy engine fail-closed behavior proven by test
- [ ] No agent imports from sibling agent folders (run grep to verify)
- [ ] No hardcoded credentials anywhere (run grep to verify)
- [ ] All AgentOutput instances have populated uncertainty_notes
- [ ] Audit log contains records for every step in the sample event chain
- [ ] Streamlit UI loads and displays demo project health
- [ ] run_sample_event.py completes in under 2 minutes
- [ ] All evaluation targets from README.md are tracked by MetricsTracker

### Final Audit Pass

After all checkpoints pass, run this audit before considering Phase 1 complete:

```
Review every file built in this project.
Flag any violation of the hard rules in CLAUDE.md.
Report: file path, line number, rule violated, and recommended fix.
Do not fix anything yet — produce the audit report first.
```

Review the report, fix violations, re-run the full test suite.

---

## What Comes Next (Phase 2 Scope)

Phase 2 begins after this checklist is clean. Do not start Phase 2 work in Phase 1 sessions.

- Knowledge Graph fully wired to context assembly (stub replaced with live Neo4j)
- Knowledge Agent live with pgvector historical case matching
- Evaluation calibration loop connected to real human feedback
- GitHub and Slack integrations bootstrapped
- Next.js UI replacing Streamlit
- Multi-tenant policy isolation
- Token budget tracking and cost reporting per tenant
