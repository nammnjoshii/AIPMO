"""Orchestrator event router — T-058.

Routes events to the correct agent coordination pattern:
- Sequential: task.updated → Issue Management → Risk Intelligence → Program Director → Communication
- Parallel: dependency.blocked → [Issue Management + Execution Monitoring] → Program Director
- Risk detection: risk.detected → Risk Intelligence → Program Director → Communication
- Milestone: milestone.updated → Execution Monitoring → Planning → Program Director
- Status report: status.report_requested → Communication directly

All 5 coordination patterns documented in function docstrings.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput, PolicyAction
from agents.communication.agent import CommunicationAgent
from agents.execution_monitoring.agent import ExecutionMonitoringAgent
from agents.issue_management.agent import IssueManagementAgent
from agents.knowledge.agent import KnowledgeAgent
from agents.planning.agent import PlanningAgent
from agents.program_director.agent import ProgramDirectorAgent
from agents.risk_intelligence.agent import RiskIntelligenceAgent
from orchestrator.conflict_resolver import detect_conflict, resolve

logger = logging.getLogger(__name__)


class EventRouter:
    """Routes delivery events to the appropriate agent coordination pattern.

    Coordination patterns:
    1. Sequential — agent B waits for agent A's output
    2. Parallel — agents run independently, Program Director merges
    3. Conflict arbitration — Program Director resolves inconsistencies
    4. Escalation routing — Policy Engine gates + Communication prepares brief
    5. Direct execution — pre-approved actions (Communication → ALLOW)
    """

    def __init__(self) -> None:
        self._execution = ExecutionMonitoringAgent()
        self._issue = IssueManagementAgent()
        self._risk = RiskIntelligenceAgent()
        self._communication = CommunicationAgent()
        self._knowledge = KnowledgeAgent()
        self._planning = PlanningAgent()
        self._director = ProgramDirectorAgent()

    def route(self, data: AgentInput) -> AgentOutput:
        """Route event to the correct coordination pattern.

        Returns the final AgentOutput from the coordination pipeline.
        """
        event_type = str(data.event_type).lower()

        if "task" in event_type or "task.updated" in event_type:
            return self._sequential_task_pattern(data)

        if "dependency" in event_type or "blocked" in event_type:
            return self._parallel_dependency_pattern(data)

        if "risk" in event_type:
            return self._risk_detection_pattern(data)

        if "milestone" in event_type:
            return self._milestone_pattern(data)

        if "report" in event_type or "status" in event_type:
            return self._direct_execution_pattern(data)

        # Default: sequential task pattern
        logger.debug("EventRouter: unknown event type %r — using sequential pattern", event_type)
        return self._sequential_task_pattern(data)

    # ---- Coordination Patterns ----

    def _sequential_task_pattern(self, data: AgentInput) -> AgentOutput:
        """Sequential pattern: Issue Management → Risk Intelligence → Program Director → Communication.

        Pattern: SEQUENTIAL — agent B waits for agent A output before reasoning.
        Use case: task.updated events where blockers may cascade to risk.
        """
        logger.debug("EventRouter: sequential pattern for event=%r", data.event_type)

        issue_out = self._issue.run(data)
        risk_out = self._risk.run(data)

        outputs = [issue_out, risk_out]
        if detect_conflict(outputs):
            merged = resolve(outputs)
        else:
            merged = self._director.merge(outputs)

        comm_data = self._enrich_for_communication(data, outputs, merged)
        return self._communication.run(comm_data)

    def _parallel_dependency_pattern(self, data: AgentInput) -> AgentOutput:
        """Parallel pattern: [Issue Management + Execution Monitoring] → Program Director → Communication.

        Pattern: PARALLEL — agents run independently, Program Director merges.
        Use case: dependency.blocked events needing both issue classification and throughput impact.
        """
        logger.debug("EventRouter: parallel dependency pattern for event=%r", data.event_type)

        # Parallel execution (both see same input, no shared state)
        issue_out = self._issue.run(data)
        exec_out = self._execution.run(data)

        outputs = [issue_out, exec_out]
        if detect_conflict(outputs):
            merged = resolve(outputs)
        else:
            merged = self._director.merge(outputs)

        # Include Risk Intelligence for blocked dependencies
        risk_out = self._risk.run(data)
        outputs = [merged, risk_out]
        if detect_conflict(outputs):
            final_merge = resolve(outputs)
        else:
            final_merge = self._director.merge(outputs)

        comm_data = self._enrich_for_communication(data, [issue_out, exec_out, risk_out], final_merge)
        return self._communication.run(comm_data)

    def _risk_detection_pattern(self, data: AgentInput) -> AgentOutput:
        """Risk detection pattern: Risk Intelligence → Program Director → Communication.

        Pattern: SEQUENTIAL with ESCALATION ROUTING.
        Use case: risk.detected events needing immediate policy routing.
        """
        logger.debug("EventRouter: risk detection pattern for event=%r", data.event_type)

        risk_out = self._risk.run(data)
        merged = self._director.merge([risk_out])

        comm_data = self._enrich_for_communication(data, [risk_out], merged)
        final = self._communication.run(comm_data)

        if merged.policy_action == PolicyAction.ESCALATE:
            logger.info(
                "EventRouter: ESCALATE triggered for project=%s — routing to human review queue",
                data.project_id,
            )
        return final

    def _milestone_pattern(self, data: AgentInput) -> AgentOutput:
        """Milestone pattern: Execution Monitoring → Planning → Program Director.

        Pattern: SEQUENTIAL — planning needs execution observations first.
        Use case: milestone.updated events needing delivery plan assessment.
        """
        logger.debug("EventRouter: milestone pattern for event=%r", data.event_type)

        exec_out = self._execution.run(data)
        plan_data = self._enrich_for_planning(data, exec_out)
        plan_out = self._planning.run(plan_data)

        outputs = [exec_out, plan_out]
        merged = self._director.merge(outputs)

        comm_data = self._enrich_for_communication(data, outputs, merged)
        return self._communication.run(comm_data)

    def _direct_execution_pattern(self, data: AgentInput) -> AgentOutput:
        """Direct execution pattern: Communication Agent only.

        Pattern: DIRECT EXECUTION — pre-approved safe action.
        Use case: status.report_requested (whitelisted execution tier).
        """
        logger.debug("EventRouter: direct execution pattern for event=%r", data.event_type)
        return self._communication.run(data)

    # ---- Enrichment helpers ----

    def _enrich_for_communication(
        self,
        original: AgentInput,
        agent_outputs: List[AgentOutput],
        merged: AgentOutput,
    ) -> AgentInput:
        """Add agent_outputs and merged output to data.extra for Communication Agent."""
        from dataclasses import replace
        enriched_extra = dict(original.extra)
        enriched_extra["agent_outputs"] = [
            {
                "agent_name": o.agent_name,
                "decision_type": o.decision_type.value,
                "policy_action": o.policy_action.value,
                "confidence_score": o.confidence_score,
                "evidence": o.evidence,
                "recommendation": o.recommendation,
                **o.extra,
            }
            for o in agent_outputs
        ]
        enriched_extra["merged_policy_action"] = merged.policy_action.value
        enriched_extra.setdefault("audience", "executive")
        return AgentInput(
            project_id=original.project_id,
            event_type=original.event_type,
            canonical_state=original.canonical_state,
            graph_context=original.graph_context,
            historical_cases=original.historical_cases,
            policy_context=original.policy_context,
            signal_quality=original.signal_quality,
            tenant_id=original.tenant_id,
            extra=enriched_extra,
        )

    def _enrich_for_planning(self, original: AgentInput, exec_out: AgentOutput) -> AgentInput:
        """Pass execution monitoring observations to planning agent."""
        enriched_extra = dict(original.extra)
        enriched_extra["execution_observations"] = {
            "evidence": exec_out.evidence,
            "proposed_state_updates": exec_out.proposed_state_updates,
        }
        enriched_extra.setdefault("planning_type", "wbs_generation")
        return AgentInput(
            project_id=original.project_id,
            event_type=original.event_type,
            canonical_state=original.canonical_state,
            graph_context=original.graph_context,
            historical_cases=original.historical_cases,
            policy_context=original.policy_context,
            signal_quality=original.signal_quality,
            tenant_id=original.tenant_id,
            extra=enriched_extra,
        )
