from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentLimitError, AgentTimeoutError, ModelProviderError
from app.agent.memory import ConversationMemory, to_provider_message
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.provider import ModelProvider
from app.agent.types import ModelResponse, ModelUsage, ProviderMessage, ToolCall
from app.commerce.context import CommerceContext
from app.models import Conversation, Message
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry, build_read_tool_registry


@dataclass(frozen=True)
class AgentLimits:
    max_model_loops: int = 6
    max_tool_calls: int = 8
    model_timeout_seconds: float = 30.0
    tool_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 45.0
    history_limit: int = 50
    tool_result_max_chars: int = 12_000


@dataclass(frozen=True)
class AgentTurnResult:
    trace_id: UUID
    message: Message
    model_loops: int
    tool_calls: int
    usage: ModelUsage


class AgentRuntime:
    def __init__(
        self,
        session: AsyncSession,
        provider: ModelProvider,
        *,
        limits: AgentLimits | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._limits = limits or AgentLimits()
        self._memory = ConversationMemory(session)

    async def run(
        self,
        conversation: Conversation,
        context: CommerceContext,
        user_content: str,
    ) -> AgentTurnResult:
        try:
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                return await self._run(conversation, context, user_content)
        except TimeoutError as error:
            raise AgentTimeoutError("agent_total_timeout") from error

    async def _run(
        self,
        conversation: Conversation,
        context: CommerceContext,
        user_content: str,
    ) -> AgentTurnResult:
        history = await self._memory.recent_messages(
            conversation.id, limit=self._limits.history_limit
        )
        sequence = history[-1].sequence + 1 if history else 1
        user_message = self._memory.append(
            conversation,
            sequence=sequence,
            role="user",
            content=user_content,
        )
        sequence += 1

        provider_messages = [ProviderMessage(role="system", content=SYSTEM_PROMPT)]
        provider_messages.extend(to_provider_message(message) for message in history)
        provider_messages.append(to_provider_message(user_message))

        trace_id = uuid4()
        tool_context = ToolContext(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
            conversation_id=conversation.id,
            trace_id=trace_id,
        )
        registry = build_read_tool_registry(self._session, tool_context)
        total_tool_calls = 0
        input_tokens = 0
        output_tokens = 0

        for model_loop in range(1, self._limits.max_model_loops + 1):
            response = await self._complete(provider_messages, registry)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens

            if not response.tool_calls:
                if not response.content.strip():
                    raise ModelProviderError("model_returned_empty_response")
                assistant_message = self._memory.append(
                    conversation,
                    sequence=sequence,
                    role="assistant",
                    content=response.content,
                )
                await self._session.flush()
                return AgentTurnResult(
                    trace_id=trace_id,
                    message=assistant_message,
                    model_loops=model_loop,
                    tool_calls=total_tool_calls,
                    usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                )

            total_tool_calls += len(response.tool_calls)
            if total_tool_calls > self._limits.max_tool_calls:
                raise AgentLimitError("agent_tool_call_limit_exceeded")

            self._memory.append(
                conversation,
                sequence=sequence,
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            sequence += 1
            provider_messages.append(
                ProviderMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            results = [
                await self._execute_tool(registry, call) for call in response.tool_calls
            ]
            for call, content in zip(response.tool_calls, results, strict=True):
                self._memory.append(
                    conversation,
                    sequence=sequence,
                    role="tool",
                    content=content,
                    tool_call_id=call.id,
                    tool_name=call.name,
                )
                sequence += 1
                provider_messages.append(
                    ProviderMessage(
                        role="tool",
                        content=content,
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )

        raise AgentLimitError("agent_model_loop_limit_exceeded")

    async def _complete(
        self, messages: list[ProviderMessage], registry: ToolRegistry
    ) -> ModelResponse:
        try:
            return await asyncio.wait_for(
                self._provider.complete(
                    messages,
                    registry.specs(),
                    timeout_seconds=self._limits.model_timeout_seconds,
                ),
                timeout=self._limits.model_timeout_seconds,
            )
        except TimeoutError as error:
            raise AgentTimeoutError("model_timeout") from error

    async def _execute_tool(self, registry: ToolRegistry, call: ToolCall) -> str:
        try:
            result = await asyncio.wait_for(
                registry.execute(call.name, call.arguments),
                timeout=self._limits.tool_timeout_seconds,
            )
        except TimeoutError:
            result = {"ok": False, "error": {"code": "tool_timeout"}}
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(content) > self._limits.tool_result_max_chars:
            return json.dumps(
                {"ok": False, "error": {"code": "tool_result_too_large"}},
                separators=(",", ":"),
            )
        return content
