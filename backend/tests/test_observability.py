from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ModelProviderError
from app.agent.types import ModelResponse, ProviderMessage
from app.api.dependencies import get_model_provider
from app.commerce.seed import stable_id
from app.db.session import get_db_session
from app.main import app
from app.observability.service import estimate_cost, sanitize


class FailingProvider:
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[dict],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        raise ModelProviderError("demo_provider_failure")


def headers(*, tenant: str = "aurora", customer_index: int = 0) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(stable_id(f"tenant:{tenant}")),
        "X-Store-Id": str(stable_id(f"store:{tenant}")),
        "X-Customer-Id": str(stable_id(f"customer:{tenant}:{customer_index}")),
    }


@pytest.mark.anyio
async def test_trace_api_returns_ordered_agent_timeline(db_session: AsyncSession) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conversation = await client.post("/api/v1/conversations", headers=headers())
            turn = await client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers=headers(),
                json={"content": "推荐有库存的降噪耳机"},
            )
            trace_id = turn.json()["trace_id"]
            trace = await client.get(f"/api/v1/traces/{trace_id}", headers=headers())
            other_store = await client.get(
                f"/api/v1/traces/{trace_id}", headers=headers(tenant="harbor")
            )
    finally:
        app.dependency_overrides.clear()

    assert turn.status_code == 200
    assert trace.status_code == 200
    payload = trace.json()
    assert payload["status"] == "succeeded"
    assert payload["model_calls"] == 2
    assert payload["tool_calls"] == 1
    assert [event["event_index"] for event in payload["events"]] == [1, 2, 3, 4, 5]
    assert [event["event_type"] for event in payload["events"]] == [
        "request",
        "model",
        "tool",
        "model",
        "response",
    ]
    assert other_store.status_code == 404


def test_trace_sanitization_and_cost_estimation() -> None:
    sanitized = sanitize(
        {
            "api_key": "secret",
            "nested": {
                "email": "customer@example.com",
                "query": "手机号 13800138000，联系 guest@example.com",
            },
        }
    )
    assert sanitized == {
        "api_key": "[REDACTED]",
        "nested": {
            "email": "[REDACTED]",
            "query": "手机号 [REDACTED_PHONE]，联系 [REDACTED_EMAIL]",
        },
    }
    assert estimate_cost(
        1_000,
        500,
        input_cost_per_million=Decimal("2"),
        output_cost_per_million=Decimal("8"),
    ) == Decimal("0.00600000")


@pytest.mark.anyio
async def test_failed_agent_request_returns_queryable_error_trace(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_model_provider] = lambda: FailingProvider()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conversation = await client.post("/api/v1/conversations", headers=headers())
            failed_turn = await client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers=headers(),
                json={"content": "推荐有库存的降噪耳机"},
            )
            trace = await client.get(
                f"/api/v1/traces/{failed_turn.json()['trace_id']}",
                headers=headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert failed_turn.status_code == 502
    assert failed_turn.json()["detail"] == "demo_provider_failure"
    assert trace.status_code == 200
    payload = trace.json()
    assert payload["status"] == "failed"
    assert payload["error_code"] == "demo_provider_failure"
    assert payload["started_at"] <= payload["completed_at"]
    assert [event["event_type"] for event in payload["events"]] == [
        "request",
        "error",
    ]
