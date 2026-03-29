"""LLM provider abstraction supporting ollama, groq, gemini, and anthropic.

Usage:
    client = get_client()               # uses LLM_PROVIDER env var (default: ollama)
    model  = get_model_for_agent("risk_intelligence_agent")
    response = client.chat.completions.create(model=model, messages=[...])

Switching providers requires only changing LLM_PROVIDER in .env.
No API key is required when LLM_PROVIDER=ollama.
"""
from __future__ import annotations

import os
from typing import Any

import yaml


def _load_models_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "models.yaml")
    config_path = os.path.normpath(config_path)
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_model_for_agent(agent_name: str) -> str:
    """Return the model string for a given agent name, from configs/models.yaml.

    Args:
        agent_name: e.g. "risk_intelligence_agent"

    Returns:
        Model string for the active provider, e.g. "llama3.3"

    Raises:
        ValueError: if agent_name is not registered in configs/models.yaml
    """
    config = _load_models_config()
    provider_name = os.environ.get("LLM_PROVIDER", config.get("default_provider", "ollama"))

    agents_cfg = config.get("agents", {})
    if agent_name not in agents_cfg:
        raise ValueError(
            f"Agent '{agent_name}' not found in configs/models.yaml. "
            f"Registered agents: {list(agents_cfg.keys())}"
        )

    tier = agents_cfg[agent_name].get("tier", "lightweight")
    providers_cfg = config.get("providers", {})

    if provider_name not in providers_cfg:
        raise ValueError(
            f"LLM_PROVIDER='{provider_name}' is not configured in configs/models.yaml. "
            f"Supported: {list(providers_cfg.keys())}"
        )

    model = providers_cfg[provider_name]["models"].get(tier)
    if not model:
        raise ValueError(
            f"No model defined for tier='{tier}' under provider='{provider_name}' "
            "in configs/models.yaml."
        )
    return model


def get_client() -> Any:
    """Return an LLM client for the active provider.

    LLM_PROVIDER env var selects the provider (default: ollama).
    Returns an OpenAI-compatible client for ollama/groq/gemini,
    or the Anthropic client for anthropic.

    Raises:
        ValueError: if LLM_PROVIDER is set to an unknown value
        RuntimeError: if a required API key is missing
    """
    config = _load_models_config()
    provider_name = os.environ.get("LLM_PROVIDER", config.get("default_provider", "ollama"))

    if provider_name == "mock":
        from llm.mock_client import MockLLMClient
        return MockLLMClient()

    if provider_name == "ollama":
        from openai import OpenAI
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OpenAI(base_url=base_url, api_key="ollama")

    if provider_name == "groq":
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. "
                "Sign up at https://console.groq.com for a free API key."
            )
        from groq import Groq
        return Groq(api_key=api_key)

    if provider_name == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        # Gemini via OpenAI-compatible endpoint
        from openai import OpenAI
        return OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key,
        )

    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        import anthropic
        return anthropic.Anthropic(api_key=api_key)

    if provider_name == "claude-sdk":
        # Claude Agent SDK — wraps Claude Code CLI for programmatic agent invocation.
        # Requires: pip install claude-agent-sdk  and  ANTHROPIC_API_KEY set.
        # Provides an OpenAI-compatible interface via ClaudeSDKLLMClient.
        # See: https://platform.claude.com/docs/en/agent-sdk/python
        from llm.claude_sdk_client import ClaudeSDKLLMClient
        return ClaudeSDKLLMClient()

    raise ValueError(
        f"Unknown LLM_PROVIDER='{provider_name}'. "
        "Supported values: ollama | groq | gemini | anthropic | claude-sdk | mock"
    )
