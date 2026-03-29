"""ClaudeSDKLLMClient — OpenAI-compatible wrapper around the Claude Agent SDK.

Uses the Claude Agent SDK's query() function to interact with Claude Code.
Provides the same .chat.completions.create() interface as OpenAI clients so
all PMO agents can use it without modification.

Activate with: LLM_PROVIDER=claude-sdk

Requirements:
    pip install claude-agent-sdk
    ANTHROPIC_API_KEY must be set in the environment.

The Claude Agent SDK powers Claude Code — it provides:
  - query() for one-shot async queries
  - ClaudeSDKClient for multi-turn streaming conversations
  - AgentDefinition for spawning subagents with specific tools and prompts
  - Hooks for intercepting the agent loop
  - In-process MCP tool servers (no subprocess overhead)

See: https://platform.claude.com/docs/en/agent-sdk/python
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class _SDKMessage:
    content: str
    role: str = "assistant"


@dataclass
class _SDKChoice:
    message: _SDKMessage
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class _SDKCompletion:
    choices: List[_SDKChoice]
    model: str = "claude-sdk"
    id: str = "claude-sdk-completion"


class ClaudeSDKLLMClient:
    """OpenAI-compatible wrapper around the Claude Agent SDK's query() function.

    Usage (same interface as other PMO LLM providers):

        client = ClaudeSDKLLMClient()
        response = client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        text = response.choices[0].message.content

    The SDK streams responses; this wrapper collects all text blocks and
    returns the concatenated content as a single completion.
    """

    def __init__(self) -> None:
        self.chat = self._ChatCompletions(self)

    def _run_async(self, coro) -> Any:
        """Run an async coroutine synchronously, handling nested event loops."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're already inside an async context — use a new thread to avoid deadlock.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return asyncio.run(coro)

    class _ChatCompletions:
        def __init__(self, client: "ClaudeSDKLLMClient") -> None:
            self._client = client

        def create(
            self,
            model: str,
            messages: List[Dict[str, str]],
            **kwargs: Any,
        ) -> _SDKCompletion:
            """Call Claude Agent SDK with the given messages.

            Converts the OpenAI-format messages to a single prompt string,
            runs the async query, and collects text output.
            """
            return self._client._run_async(self._async_create(model, messages, **kwargs))

        async def _async_create(
            self,
            model: str,
            messages: List[Dict[str, str]],
            **kwargs: Any,
        ) -> _SDKCompletion:
            try:
                from claude_agent_sdk import (
                    AssistantMessage,
                    ClaudeAgentOptions,
                    ResultMessage,
                    TextBlock,
                    query,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "claude-agent-sdk is not installed. "
                    "Run: pip install claude-agent-sdk"
                ) from exc

            # Extract system prompt and build user prompt from messages
            system_parts: List[str] = []
            user_parts: List[str] = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_parts.append(content)
                elif role == "user":
                    user_parts.append(content)
                elif role == "assistant":
                    # Include prior assistant turns as context
                    user_parts.append(f"[Previous assistant response]: {content}")

            system_prompt = "\n\n".join(system_parts) if system_parts else None
            user_prompt = "\n\n".join(user_parts) if user_parts else ""

            options = ClaudeAgentOptions(
                system_prompt=system_prompt,
                max_turns=1,  # single shot for agent reasoning
            )

            text_blocks: List[str] = []
            total_cost: Optional[float] = None

            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_blocks.append(block.text)
                elif isinstance(message, ResultMessage):
                    if message.total_cost_usd:
                        total_cost = message.total_cost_usd

            combined = "".join(text_blocks)
            if total_cost is not None:
                logger.debug("Claude Agent SDK call cost: $%.6f", total_cost)

            return _SDKCompletion(
                choices=[_SDKChoice(message=_SDKMessage(content=combined))],
                model=model,
            )
