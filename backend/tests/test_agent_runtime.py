import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentLimitError, AgentTimeoutError
from app.agent.memory import ConversationMemory
from app.agent.provider import ModelProvider, OpenAICompatibleProvider
from app.agent.runtime import AgentLimits, AgentRuntime
from app.agent.types import ModelResponse, ProviderMessage, ToolCall, ToolSpec
from app.commerce.context import CommerceContext
from app.commerce.seed import stable_id
from app.models import Message
from app.tools.registry import ToolRegistry


def context() -> CommerceContext:
    return CommerceContext(
        tenant_id=stable_id("tenant:aurora"),
        store_id=stable_id("store:aurora"),
        customer_id=stable_id("customer:aurora:0"),
    )


class AlwaysCallsToolProvider:
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        del messages, tools, timeout_seconds
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    id="repeating-call",
                    name="get_customer_orders",
                    arguments={"limit": 1},
                )
            ]
        )


class SlowProvider:
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        del messages, tools, timeout_seconds
        await asyncio.sleep(1)
        return ModelResponse(content="too late")


class TwoToolsProvider:
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        del tools, timeout_seconds
        if messages[-1].role == "tool":
            return ModelResponse(content="两个查询都已完成")
        return ModelResponse(
            tool_calls=[
                ToolCall(id="first-call", name="get_customer_orders", arguments={"limit": 1}),
                ToolCall(id="second-call", name="search_products", arguments={"limit": 1}),
            ]
        )


class ConcurrencyDetectingRuntime(AgentRuntime):
    def __init__(self, session: AsyncSession, provider: ModelProvider) -> None:
        super().__init__(session, provider)
        self.active_tool_calls = 0
        self.max_active_tool_calls = 0

    async def _execute_tool(self, registry: ToolRegistry, call: ToolCall) -> str:
        self.active_tool_calls += 1
        self.max_active_tool_calls = max(self.max_active_tool_calls, self.active_tool_calls)
        try:
            await asyncio.sleep(0)
            return await super()._execute_tool(registry, call)
        finally:
            self.active_tool_calls -= 1


@pytest.mark.anyio
async def test_runtime_stops_at_tool_call_budget(db_session: AsyncSession) -> None:
    commerce_context = context()
    conversation = await ConversationMemory(db_session).create(commerce_context)
    runtime = AgentRuntime(
        db_session,
        AlwaysCallsToolProvider(),
        limits=AgentLimits(max_model_loops=6, max_tool_calls=1),
    )

    with pytest.raises(AgentLimitError, match="agent_tool_call_limit_exceeded"):
        await runtime.run(conversation, commerce_context, "循环调用工具")


@pytest.mark.anyio
async def test_runtime_enforces_model_timeout(db_session: AsyncSession) -> None:
    commerce_context = context()
    conversation = await ConversationMemory(db_session).create(commerce_context)
    runtime = AgentRuntime(
        db_session,
        SlowProvider(),
        limits=AgentLimits(model_timeout_seconds=0.01, total_timeout_seconds=1),
    )

    with pytest.raises(AgentTimeoutError, match="model_timeout"):
        await runtime.run(conversation, commerce_context, "慢请求")


@pytest.mark.anyio
async def test_runtime_executes_multiple_tool_calls_sequentially(
    db_session: AsyncSession,
) -> None:
    commerce_context = context()
    conversation = await ConversationMemory(db_session).create(commerce_context)
    runtime = ConcurrencyDetectingRuntime(db_session, TwoToolsProvider())

    result = await runtime.run(conversation, commerce_context, "同时查询订单和商品")

    assert result.model_loops == 2
    assert result.tool_calls == 2
    assert runtime.max_active_tool_calls == 1


@pytest.mark.anyio
async def test_openai_compatible_provider_maps_http_timeout_to_model_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_timeout(*_args: object, **_kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", raise_timeout)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
    )

    with pytest.raises(AgentTimeoutError, match="model_timeout"):
        await provider.complete([], [], timeout_seconds=0.01)


@pytest.mark.anyio
async def test_failed_runtime_is_not_committed_by_itself(db_session: AsyncSession) -> None:
    commerce_context = context()
    conversation = await ConversationMemory(db_session).create(commerce_context)
    runtime = AgentRuntime(
        db_session,
        AlwaysCallsToolProvider(),
        limits=AgentLimits(max_model_loops=1, max_tool_calls=0),
    )

    with pytest.raises(AgentLimitError):
        await runtime.run(conversation, commerce_context, "不要提交")
    await db_session.rollback()

    messages = list(await db_session.scalars(select(Message)))
    assert messages == []
