from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.seed import stable_id
from app.db.session import get_db_session
from app.main import app


def headers(customer_index: int) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(stable_id("tenant:aurora")),
        "X-Store-Id": str(stable_id("store:aurora")),
        "X-Customer-Id": str(stable_id(f"customer:aurora:{customer_index}")),
    }


async def create_conversation(client: httpx.AsyncClient, customer_index: int) -> str:
    response = await client.post("/api/v1/conversations", headers=headers(customer_index))
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.mark.anyio
async def test_product_consultation_persists_complete_tool_call_chain(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conversation_id = await create_conversation(client, 0)
            turn = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=headers(0),
                json={"content": "请推荐有库存的降噪耳机"},
            )
            persisted = await client.get(
                f"/api/v1/conversations/{conversation_id}", headers=headers(0)
            )
    finally:
        app.dependency_overrides.clear()

    assert turn.status_code == 200
    assert turn.json()["model_loops"] == 2
    assert turn.json()["tool_calls"] == 1
    assert "降噪蓝牙耳机" in turn.json()["message"]["content"]
    messages = persisted.json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[1]["tool_calls"][0]["name"] == "search_products"
    assert messages[2]["tool_name"] == "search_products"
    assert messages[2]["tool_call_id"] == messages[1]["tool_calls"][0]["id"]


@pytest.mark.anyio
async def test_agent_completes_order_and_logistics_flows(db_session: AsyncSession) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            order_conversation = await create_conversation(client, 0)
            order_turn = await client.post(
                f"/api/v1/conversations/{order_conversation}/messages",
                headers=headers(0),
                json={"content": "帮我查订单 AUR-202607-0001"},
            )
            logistics_conversation = await create_conversation(client, 4)
            logistics_turn = await client.post(
                f"/api/v1/conversations/{logistics_conversation}/messages",
                headers=headers(4),
                json={"content": "订单 AUR-202607-0005 的物流怎么还没更新？"},
            )
    finally:
        app.dependency_overrides.clear()

    assert order_turn.status_code == 200
    assert "AUR-202607-0001" in order_turn.json()["message"]["content"]
    assert logistics_turn.status_code == 200
    assert "超过 5 天未更新" in logistics_turn.json()["message"]["content"]


@pytest.mark.anyio
async def test_agent_cannot_access_another_customers_order(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conversation_id = await create_conversation(client, 1)
            turn = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers=headers(1),
                json={"content": "帮我查订单 AUR-202607-0001"},
            )
            leaked_conversation = await client.get(
                f"/api/v1/conversations/{conversation_id}", headers=headers(0)
            )
    finally:
        app.dependency_overrides.clear()

    assert turn.status_code == 200
    content = turn.json()["message"]["content"]
    assert "当前账号下未找到" in content
    assert "¥" not in content
    assert leaked_conversation.status_code == 404
