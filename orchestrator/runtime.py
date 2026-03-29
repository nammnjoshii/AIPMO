"""Agent runtime — T-059.

Initializes all 7 agents and verifies they match configs/agents.yaml.
Provides health check for LLM, DB, Redis, and Kafka connectivity.
Integrates per-tenant token budget tracking (orchestrator/token_budget.py).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import yaml

from orchestrator.token_budget import BudgetExceededError, TokenBudgetTracker

logger = logging.getLogger(__name__)


class AgentRuntime:
    """Lifecycle management for all 7 PMO agents.

    Usage:
        runtime = AgentRuntime()
        runtime.initialize()
        health = runtime.health_check()
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path = config_path or os.path.join(
            os.path.dirname(__file__), "..", "configs", "agents.yaml"
        )
        self._agents: Dict[str, Any] = {}
        self._initialized = False
        self.token_budget = TokenBudgetTracker()

    def initialize(self) -> None:
        """Load all 7 agents and verify against configs/agents.yaml.

        Raises:
            ValueError: if an agent name is missing from agents.yaml.
            FileNotFoundError: if agents.yaml doesn't exist.
        """
        from agents.communication.agent import CommunicationAgent
        from agents.execution_monitoring.agent import ExecutionMonitoringAgent
        from agents.issue_management.agent import IssueManagementAgent
        from agents.knowledge.agent import KnowledgeAgent
        from agents.planning.agent import PlanningAgent
        from agents.program_director.agent import ProgramDirectorAgent
        from agents.risk_intelligence.agent import RiskIntelligenceAgent

        agent_instances = {
            "execution_monitoring_agent": ExecutionMonitoringAgent(),
            "issue_management_agent": IssueManagementAgent(),
            "risk_intelligence_agent": RiskIntelligenceAgent(),
            "communication_agent": CommunicationAgent(),
            "knowledge_agent": KnowledgeAgent(),
            "planning_agent": PlanningAgent(),
            "program_director_agent": ProgramDirectorAgent(),
        }

        # Verify against agents.yaml
        config = self._load_agents_config()
        registered = set(config.get("agents", {}).keys())

        for agent_name in agent_instances:
            if agent_name not in registered:
                raise ValueError(
                    f"Agent '{agent_name}' is not registered in configs/agents.yaml. "
                    f"Registered agents: {sorted(registered)}"
                )

        self._agents = agent_instances
        self._initialized = True
        logger.info("AgentRuntime: initialized %d agents", len(agent_instances))

    def get_agent(self, name: str) -> Any:
        """Return a registered agent instance by name."""
        if not self._initialized:
            raise RuntimeError("AgentRuntime.initialize() must be called first.")
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' not found. Registered: {list(self._agents.keys())}")
        return self._agents[name]

    def health_check(self) -> Dict[str, Any]:
        """Test LLM, DB, Redis, and Kafka connectivity.

        Returns:
            Dict with keys: llm, database, redis, kafka, agents_loaded, healthy (overall bool).
        """
        results: Dict[str, Any] = {
            "agents_loaded": len(self._agents),
            "llm": self._check_llm(),
            "database": self._check_database(),
            "redis": self._check_redis(),
            "kafka": self._check_kafka(),
        }
        # kafka is optional — only required when KAFKA_BOOTSTRAP_SERVERS is set
        required_checks = ["llm", "database", "redis"]
        if os.environ.get("KAFKA_BOOTSTRAP_SERVERS"):
            required_checks.append("kafka")

        results["healthy"] = all(
            results.get(k) is True for k in required_checks
        )
        return results

    async def check_token_budget(
        self,
        tenant_id: str,
        agent_name: str,
        estimated_tokens: int = 0,
    ) -> None:
        """Check token budget before an LLM call. Raises BudgetExceededError if over cap.

        Callers should catch BudgetExceededError to skip the LLM call or degrade gracefully.
        """
        await self.token_budget.check(
            tenant_id=tenant_id,
            agent_name=agent_name,
            estimated_tokens=estimated_tokens,
        )

    async def record_token_usage(
        self,
        tenant_id: str,
        agent_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Dict[str, Any]:
        """Record actual token usage after an LLM call. Never raises."""
        return await self.token_budget.record(
            tenant_id=tenant_id,
            agent_name=agent_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # ---- Internal ----

    def _load_agents_config(self) -> Dict[str, Any]:
        config_path = os.path.normpath(self._config_path)
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"agents.yaml not found at {config_path}")
        with open(config_path) as f:
            return yaml.safe_load(f) or {}

    def _check_llm(self) -> bool:
        """Check LLM provider availability."""
        try:
            from llm.provider import get_client
            provider = os.environ.get("LLM_PROVIDER", "ollama")
            if provider == "mock":
                return True
            if provider == "ollama":
                import urllib.request
                url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags"
                urllib.request.urlopen(url, timeout=3)
                return True
            # For other providers, just verify the client can be created
            get_client()
            return True
        except Exception as e:
            logger.warning("AgentRuntime health check — LLM unavailable: %s", e)
            return False

    def _check_database(self) -> bool:
        """Check SQLite DB accessibility."""
        try:
            db_path = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")
            if db_path == ":memory:":
                return True
            db_dir = os.path.dirname(db_path) if os.path.dirname(db_path) else "."
            return os.access(db_dir, os.W_OK)
        except Exception as e:
            logger.warning("AgentRuntime health check — DB unavailable: %s", e)
            return False

    def _check_redis(self) -> bool:
        """Check Redis connectivity."""
        try:
            import redis as redis_lib
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            r = redis_lib.from_url(redis_url, socket_connect_timeout=2)
            r.ping()
            return True
        except Exception as e:
            logger.warning("AgentRuntime health check — Redis unavailable: %s", e)
            return False

    def _check_kafka(self) -> bool:
        """Check Kafka broker connectivity (Phase 2+ only).

        Returns True immediately when KAFKA_BOOTSTRAP_SERVERS is not set
        (Redis Streams mode — Kafka not required).
        """
        bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
        if not bootstrap:
            return True  # Kafka not configured — not required in Phase 1
        try:
            from kafka import KafkaAdminClient  # type: ignore
            admin = KafkaAdminClient(
                bootstrap_servers=bootstrap.split(","),
                request_timeout_ms=3000,
            )
            admin.list_topics()
            admin.close()
            return True
        except Exception as e:
            logger.warning("AgentRuntime health check — Kafka unavailable: %s", e)
            return False
