# DECISIONS.md

Living decisions log for the Autonomous PMO build.
Claude Code appends to this file at the end of every session.
Humans review and confirm entries marked [PENDING CONFIRMATION].

Format for new entries:
```
## YYYY-MM-DD — [Phase] — [Topic]
Decision: [What was decided]
Rationale: [Why]
Alternatives considered: [What else was evaluated]
Consequences: [What this forecloses or enables]
Status: confirmed | pending_confirmation | superseded
```

---

## Foundational Decisions (Pre-Build)

---

## 2026-03-25 — Architecture — Event-Driven Over Polling

**Decision:** The platform uses event-driven signal ingestion via Redis Streams, not scheduled polling of integration sources.

**Rationale:** Polling introduces latency between a delivery signal occurring and the platform detecting it. The 2-minute event-to-observation target in README.md is not achievable with polling at reasonable intervals without excessive API cost. Events are more accurate, cheaper, and align with how Jira and Slack natively emit changes.

**Alternatives considered:** APScheduler-based polling every 5 minutes (simpler, no Redis dependency), webhooks only (reduces infrastructure but misses sources without webhook support).

**Consequences:** Redis is a required infrastructure dependency from Phase 1. The event deduplication window in `signal_quality/noise_filter.py` is necessary to handle webhook retries and duplicate emissions.

**Status:** confirmed

---

## 2026-03-25 — Architecture — Fail Closed on Policy Engine

**Decision:** Any unhandled exception in the policy engine defaults to DENY — never ALLOW.

**Rationale:** In an enterprise governance context, an availability gap in the policy engine must not silently grant permissions. The cost of a false deny (a human must manually approve) is acceptable. The cost of a false allow (an unauthorized action executes silently) is not.

**Alternatives considered:** Fail to last-known-good policy (risky if policy was modified), fail with APPROVAL_REQUIRED (reasonable alternative), fail open (rejected).

**Consequences:** Policy engine outages will queue actions for manual review. Operations team must have a runbook for policy engine recovery. Monitoring and alerting on policy engine health is mandatory.

**Status:** confirmed

---

## 2026-03-25 — Architecture — Seven Agents, Not Micro-Agents

**Decision:** Phase 1 deploys exactly 7 agents with bounded responsibilities, not a larger set of micro-agents.

**Rationale:** Micro-agent architectures (20–30 agents) are harder to test, debug, govern, and operate. Each additional agent adds orchestration complexity, token cost, and latency. Seven agents cover all PM functional areas while remaining deployable and maintainable by a small team.

**Alternatives considered:** CrewAI with 15+ specialized agents (more granular but harder to govern), single monolithic agent (simpler but cannot parallelize analysis), three-agent design (too coarse for the required precision).

**Consequences:** Each of the 7 agents has broader responsibilities than a micro-agent would. Skills within each agent are the unit of modularity — not agents themselves. Adding a new capability means adding a skill to an existing agent, not deploying a new agent, until the scope justifiably warrants it.

**Status:** confirmed

---

## 2026-03-25 — Architecture — LangGraph Over CrewAI

**Decision:** LangGraph is the orchestration framework for multi-agent coordination.

**Rationale:** LangGraph provides native stateful graph execution with explicit node and edge definitions — directly aligned with the coordination patterns (sequential, parallel, conflict arbitration, escalation routing) defined in the architecture. CrewAI provides higher-level abstractions that are harder to control precisely for policy-gated enterprise workflows.

**Alternatives considered:** CrewAI (faster to prototype, less control), custom orchestrator (full control, significant build cost), AutoGen (research-oriented, less production-ready for governance requirements).

**Consequences:** LangGraph's node/edge model maps directly to `orchestrator/event_router.py`. State passing between nodes must use the AgentInput/AgentOutput contracts — no ad hoc dict passing between graph nodes.

**Status:** confirmed

---

## 2026-03-25 — Architecture — Federated State, Not Single Source of Record

**Decision:** Autonomous PMO does not replace Jira, GitHub, or Smartsheet. It creates a federated canonical state by normalizing signals from those systems.

**Rationale:** Enterprise organizations will not migrate their tools. Any platform that requires tool replacement will not be adopted. The platform's value is in reasoning above the tools, not owning the data.

**Alternatives considered:** Full data migration to a single store (too disruptive for adoption), read-only mirroring without normalization (insufficient for cross-source reasoning).

**Consequences:** The Signal Quality Pipeline must handle source disagreement. The canonical state is always a derived view, not the authoritative record. When Jira says one thing and a meeting note says another, the platform must reconcile and disclose — not silently pick one.

**Status:** confirmed

---

## 2026-03-25 — Architecture — Knowledge Graph Scope Limits

**Decision:** The knowledge graph stores cross-project relationships, system ownership, stakeholder approval chains, risk propagation paths, team capacity, and decision/outcome history. It does not store individual task-level relationships, comment threads, or tool-internal metadata.

**Rationale:** Unconstrained graph growth leads to edge explosion, slow queries, and graph drift where the topology no longer reflects operational reality. Scope limits keep the graph useful and maintainable.

**Alternatives considered:** Full task-level graph (maximum fidelity, unmanageable at scale), no graph at all relying on pgvector only (loses relationship reasoning), quarterly pruning of all edges (unpredictable query behavior).

**Consequences:** Some queries that would naturally use task-level data must be approximated at the milestone level. This is an acceptable tradeoff. If a query requires task-level graph traversal, that is a signal that the scope limit may need to be revisited — but only with a documented justification.

**Status:** confirmed

---

## 2026-03-25 — Infrastructure — Redis Streams Over Kafka for Phase 1

**Decision:** Redis Streams is the event bus for Phase 1. Kafka is the Phase 2+ upgrade path.

**Rationale:** Redis is already required for the deduplication window in the Signal Quality Pipeline. Using Redis Streams eliminates a separate infrastructure dependency for Phase 1 while providing at-least-once delivery semantics and consumer group support. Kafka provides better partitioning, replay, and operational tooling at scale — relevant when the event volume justifies the operational overhead.

**Alternatives considered:** Kafka from day one (production-ready, adds Docker complexity and operational burden for a demo-scale Phase 1), RabbitMQ (simpler than Kafka, but lacks native stream replay), in-memory queue (insufficient durability).

**Consequences:** The RedisEventProducer and RedisEventConsumer in `events/` must use consumer groups for at-least-once delivery. The upgrade to Kafka in Phase 2 requires replacing only those two files — no orchestrator changes needed.

**Status:** confirmed

---

## 2026-03-25 — Security — Append-Only Audit Logs

**Decision:** Audit log records are append-only. The application database role has no UPDATE or DELETE permissions on the audit_log table.

**Rationale:** Enterprise compliance and auditability requirements demand tamper-evident logs. An immutable audit trail is the difference between a system stakeholders trust and one they do not.

**Alternatives considered:** Soft-delete with deletion_reason (allows accidental or intentional tampering), external audit log service (adds cost and dependency), file-based logs (not queryable, not reliably immutable).

**Consequences:** There is no "undo" for an audit record. Mistakes in audit records must be corrected by appending a correction record — not by editing the original. Claude Code must never generate code that attempts an UPDATE or DELETE on the audit_log table.

**Status:** confirmed

---

## 2026-03-25 — Agents — uncertainty_notes Always Required

**Decision:** AgentOutput raises a ValueError at instantiation if uncertainty_notes is empty.

**Rationale:** Empty uncertainty notes are the primary failure mode for AI-generated outputs gaining unwarranted trust. Enforcing this at the contract level — not just the prompt level — ensures it cannot be bypassed. Every output must explicitly state what it does not know.

**Alternatives considered:** Linting check (bypassable), code review only (human error), soft warning (ignored in practice).

**Consequences:** Every agent prompt template must instruct the agent to populate uncertainty_notes with at least one item. Agents that cannot identify any uncertainty should state: "No significant uncertainty identified. Signal quality is high and evidence is consistent across sources." This is the only acceptable non-empty uncertainty note that does not describe a limitation.

**Status:** confirmed

---

## 2026-03-25 — Evaluation — Acceptance Rate Is Not Sufficient

**Decision:** Human acceptance rate alone is not a sufficient evaluation metric. The calibration loop also checks whether accepted recommendations improved outcomes, whether rejected recommendations were actually correct, and whether low false positive rates are masking poor recall.

**Rationale:** A PM who trusts the system and approves everything produces a high acceptance rate that means nothing. An agent that flags so few risks that it never gets rejected produces a low false positive rate that conceals dangerous recall failure.

**Alternatives considered:** Acceptance rate only (gameable), fully automated evaluation without human labels (insufficient ground truth), external A/B testing (operationally complex for Phase 1).

**Consequences:** `evaluation/labeling.py` must track over-trust signals and down-weight feedback from users with >95% acceptance rates. `evaluation/calibration.py` must compare accepted outputs against later outcomes — this requires a feedback loop with a 2–4 week lag. Phase 1 evaluation is incomplete until the first 30-day cycle of outcome data is available.

**Status:** confirmed

---

## Open Questions

Track unresolved decisions here. Assign an owner and a resolution deadline.

---

### OQ-001 — Should calibration recommendations auto-apply below a 0.05 delta?

**Context:** When a threshold recalibration is less than 0.05 (e.g., adjusting the risk escalation threshold from 0.40 to 0.42), requiring human review may add friction without adding meaningful governance value.

**Owner:** [Assign before Phase 2 starts]
**Resolution deadline:** Phase 2 kickoff
**Options:**
- A: Auto-apply changes below 0.05 delta — reduces operational friction
- B: All calibration changes require human approval regardless of size — maximum control
- C: Auto-apply below 0.05 with audit log entry — middle path

**Status:** open

---

### OQ-002 — Neo4j local vs Amazon Neptune for Phase 2

**Context:** Phase 1 uses Neo4j locally via Docker. Phase 2 targets cloud deployment. Neptune is AWS-native and managed; Neo4j cloud has better tooling but additional cost.

**Owner:** [Assign before Phase 2 starts]
**Resolution deadline:** Phase 2 kickoff
**Constraint:** The graph query interface (`knowledge_graph/query_service.py`) must be the same regardless of backend — abstraction layer required before Phase 2.

**Status:** open

---

### OQ-003 — Token budget enforcement: hard cap or soft alert?

**Context:** README.md specifies configurable monthly spend caps. Should exceeding the cap hard-stop agent invocations or trigger a soft alert and downgrade to a cheaper model?

**Owner:** [Assign before Phase 3 starts]
**Resolution deadline:** Phase 3 kickoff
**Preference:** Hard cap is safer for enterprise cost control. Soft alert with automatic model downgrade is better for availability.

**Status:** open

---

## Session Log

Append a one-line summary after every Claude Code session.

```
YYYY-MM-DD | Phase X | [What was built] | [Any decisions made or questions opened]
```

2026-03-25 | Phase 0 | Project initialized, all foundational decisions recorded | OQ-001, OQ-002, OQ-003 opened
