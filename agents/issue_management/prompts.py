"""Issue Management Agent prompt templates."""
from __future__ import annotations

SYSTEM_PROMPT = """You are the Issue Management Agent for an Autonomous PMO system.

Your role is to classify blockers, identify root cause patterns, and estimate severity.

STRICT RULES:
- severity > 0.70 → decision_type = decision_preparation + policy_action = approval_required
- severity ≤ 0.70 → decision_type = observation
- sparsity_alert present → cap severity at 0.60
- Always populate uncertainty_notes with specific evidence gaps.
"""

INPUT_TEMPLATE = """Project: {project_id}
Event type: {event_type}
Signal confidence: {confidence_score}
Sparsity alert: {sparsity_alert}

Canonical state: {canonical_state}
Graph context: {graph_context}
Historical cases: {historical_cases}
Policy context: {policy_context}

Classify the issue, identify root cause pattern, and estimate severity (0.0-1.0).
"""

OUTPUT_SCHEMA_INSTRUCTION = """Return ONLY valid JSON:
{
  "agent_name": "issue_management_agent",
  "decision_type": <"observation" or "decision_preparation">,
  "confidence_score": <float 0.0-1.0>,
  "evidence": [<strings>],
  "decision_factors": [<strings>],
  "recommendation": <string or null>,
  "proposed_state_updates": {<dict>},
  "proposed_graph_updates": [<list>],
  "policy_action": <"allow"|"allow_with_audit"|"approval_required">,
  "uncertainty_notes": [<at least one string>]
}
"""

BLOCKER_CLASSIFICATIONS = [
    "external_dependency",
    "internal_capacity",
    "technical_debt",
    "scope_ambiguity",
    "resource_conflict",
    "approval_bottleneck",
    "unknown",
]

ROOT_CAUSE_PATTERNS = [
    "third_party_api_delay",
    "team_capacity_overload",
    "unclear_requirements",
    "infrastructure_failure",
    "integration_failure",
    "process_gap",
    "unknown_pattern",
]
