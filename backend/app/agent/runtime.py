from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentLimitError, AgentTimeoutError, ModelProviderError
from app.agent.memory import ConversationMemory, to_provider_message
from app.agent.presentation import (
    CustomerSource,
    present_tool_result,
    sanitize_customer_response,
)
from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.agent.provider import ModelProvider
from app.agent.types import ModelResponse, ModelUsage, ProviderMessage, ToolCall
from app.commerce.context import CommerceContext
from app.models import AgentTrace, Conversation, Message
from app.observability.service import TraceRecorder, persist_failed_trace
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry, build_tool_registry


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
    sources: list[CustomerSource]


class AgentRuntime:
    def __init__(
        self,
        session: AsyncSession,
        provider: ModelProvider,
        *,
        limits: AgentLimits | None = None,
        model_provider: str = "unknown",
        model_name: str = "unknown",
        prompt_version: str = PROMPT_VERSION,
        input_cost_per_million: Decimal = Decimal("0"),
        output_cost_per_million: Decimal = Decimal("0"),
    ) -> None:
        self._session = session
        self._provider = provider
        self._limits = limits or AgentLimits()
        self._memory = ConversationMemory(session)
        self._model_provider = model_provider
        self._model_name = model_name
        self._prompt_version = prompt_version
        self._input_cost_per_million = input_cost_per_million
        self._output_cost_per_million = output_cost_per_million

    async def run(
        self,
        conversation: Conversation,
        context: CommerceContext,
        user_content: str,
    ) -> AgentTurnResult:
        started = perf_counter()
        trace = AgentTrace(
            id=uuid4(),
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
            conversation_id=conversation.id,
            status="running",
            model_provider=self._model_provider,
            model_name=self._model_name,
            prompt_version=self._prompt_version,
            model_calls=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0"),
            started_at=datetime.now(UTC),
        )
        self._session.add(trace)
        recorder = TraceRecorder(
            trace,
            input_cost_per_million=self._input_cost_per_million,
            output_cost_per_million=self._output_cost_per_million,
        )
        recorder.request_received(user_content)
        try:
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                return await self._run(
                    conversation,
                    context,
                    user_content,
                    recorder=recorder,
                    started=started,
                )
        except TimeoutError as error:
            recorder.fail("agent_total_timeout", total_latency_ms=_elapsed_ms(started))
            trace_id = await persist_failed_trace(self._session, trace)
            raise AgentTimeoutError("agent_total_timeout", trace_id=trace_id) from error
        except Exception as error:
            recorder.fail(str(error) or type(error).__name__, total_latency_ms=_elapsed_ms(started))
            trace_id = await persist_failed_trace(self._session, trace)
            if isinstance(error, (AgentLimitError, AgentTimeoutError, ModelProviderError)):
                error.trace_id = trace_id
            raise

    async def _run(
        self,
        conversation: Conversation,
        context: CommerceContext,
        user_content: str,
        *,
        recorder: TraceRecorder,
        started: float,
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

        tool_context = ToolContext(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
            conversation_id=conversation.id,
            trace_id=recorder.trace.id,
        )
        registry = build_tool_registry(self._session, tool_context)
        total_tool_calls = 0
        input_tokens = 0
        output_tokens = 0
        customer_sources: list[CustomerSource] = []

        for model_loop in range(1, self._limits.max_model_loops + 1):
            model_started = perf_counter()
            response = await self._complete(provider_messages, registry)
            model_latency_ms = _elapsed_ms(model_started)
            if recorder.trace.first_model_response_ms is None:
                recorder.trace.first_model_response_ms = _elapsed_ms(started)
            recorder.model_completed(
                model_loop,
                response,
                latency_ms=model_latency_ms,
                message_count=len(provider_messages),
                tool_count=len(registry.specs()),
            )
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens

            if not response.tool_calls:
                if not response.content.strip():
                    raise ModelProviderError("model_returned_empty_response")
                customer_content = sanitize_customer_response(response.content)
                assistant_message = self._memory.append(
                    conversation,
                    sequence=sequence,
                    role="assistant",
                    content=customer_content,
                )
                await self._session.flush()
                recorder.complete(customer_content, total_latency_ms=_elapsed_ms(started))
                await self._session.flush()
                return AgentTurnResult(
                    trace_id=recorder.trace.id,
                    message=assistant_message,
                    model_loops=model_loop,
                    tool_calls=total_tool_calls,
                    usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                    sources=_deduplicate_sources(customer_sources),
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

            results: list[str] = []
            for call in response.tool_calls:
                tool_started = perf_counter()
                raw_content = await self._execute_tool(registry, call)
                try:
                    parsed_result = json.loads(raw_content)
                except json.JSONDecodeError:
                    parsed_result = {"ok": False, "error": {"code": "invalid_tool_result"}}
                recorder.tool_completed(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    result=parsed_result,
                    latency_ms=_elapsed_ms(tool_started),
                )
                presented = present_tool_result(call.name, parsed_result)
                customer_sources.extend(presented.sources)
                results.append(
                    json.dumps(
                        presented.data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                )
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


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _deduplicate_sources(sources: list[CustomerSource]) -> list[CustomerSource]:
    unique: dict[str, CustomerSource] = {}
    for source in sources:
        unique.setdefault(source.citation_id, source)
    return list(unique.values())
