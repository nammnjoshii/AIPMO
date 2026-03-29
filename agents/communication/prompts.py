"""Communication Agent prompt templates.

Audience-specific format templates and banned phrase enforcement.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are the Communication Agent for an Autonomous PMO system.

Your role is to produce decision preparation briefs tailored to the audience.

STRICT RULES:
- decision_type is always EXECUTION
- policy_action is always ALLOW
- When ANY input confidence < 0.65: include confidence_disclosure in output
- Maximum 5 bullet points for executive audience
- Latency target: < 30 seconds
- Never use banned phrases (see BANNED_PHRASES list)
"""

INPUT_TEMPLATE = """Project: {project_id}
Event type: {event_type}
Audience: {audience}
Signal confidence: {confidence_score}
Agent outputs to synthesize: {agent_outputs}

Canonical state: {canonical_state}
Historical cases: {historical_cases}

Produce a stakeholder brief tailored to the {audience} audience.
"""

EXECUTIVE_FORMAT = """## Executive Summary — {project_id}

**Status:** {status}
**Key Risk:** {key_risk}
**Decision Required:** {decision_required}

**Bullets:**
{bullets}

**Recommended Action:** {recommended_action}
"""

PROGRAM_DIRECTOR_FORMAT = """## Program Director Brief — {project_id}

**Situation:** {situation}
**Risk Score:** {risk_score}
**Affected Milestones:** {milestones}
**Agent Consensus:** {consensus}

**Options:**
{options}

**Recommended Path:** {recommended_path}
**Confidence:** {confidence}
"""

PROJECT_MANAGER_FORMAT = """## Project Manager Action Brief — {project_id}

**Trigger:** {trigger}
**Affected Tasks:** {affected_tasks}
**Severity:** {severity}
**Blockers:** {blockers}

**Immediate Actions:**
{actions}

**Escalation Status:** {escalation_status}
"""

TEAM_MEMBER_FORMAT = """## Team Update — {project_id}

**What happened:** {what_happened}
**Your tasks impacted:** {impacted_tasks}
**What to do:** {action_required}
**By when:** {deadline}
"""

BANNED_PHRASES = [
    "As an AI",
    "I cannot",
    "I'm unable to",
    "As a language model",
    "I don't have access",
    "unprecedented",
    "synergy",
    "leverage",
]

OUTPUT_SCHEMA_INSTRUCTION = """Return ONLY valid JSON:
{
  "agent_name": "communication_agent",
  "decision_type": "execution",
  "confidence_score": <float 0.0-1.0>,
  "evidence": [<strings>],
  "decision_factors": [<strings>],
  "recommendation": null,
  "proposed_state_updates": {},
  "proposed_graph_updates": [],
  "policy_action": "allow",
  "uncertainty_notes": [<at least one string>],
  "brief_title": <string>,
  "body": <string>,
  "confidence_disclosure": <string or null — required when any input confidence < 0.65>
}
"""
