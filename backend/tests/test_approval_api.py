from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import ConversationMemory
from app.approvals.service import ActionRequestService
from app.commerce.context import CommerceContext
from app.commerce.seed import BASE_TIME, stable_id
from app.db.session import get_db_session
from app.main import app
from app.tools.context import ToolContext

pytestmark = pytest.mark.anyio


def customer_context() -> CommerceContext:
    return CommerceContext(
        tenant_id=stable_id("tenant:aurora"),
        store_id=stable_id("store:aurora"),
        customer_id=stable_id("customer:aurora:0"),
    )


def approval_headers(*, store: str = "aurora") -> dict[str, str]:
    return {
        "X-Tenant-Id": str(stable_id(f"tenant:{store}")),
        "X-Store-Id": str(stable_id(f"store:{store}")),
        "X-Approver-Id": "ops-reviewer@example.com",
    }


async def pending_cancellation(session: AsyncSession) -> str:
    context = customer_context()
    conversation = await ConversationMemory(session).create(context)
    action = await ActionRequestService(
        session,
        ToolContext(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
            conversation_id=conversation.id,
            trace_id=stable_id("trace:approval-api"),
        ),
        clock=lambda: BASE_TIME,
    ).request_cancellation("AUR-202607-0001", "顾客误拍")
    await session.commit()
    return str(action.id)


async def test_approval_api_lists_and_approves_scoped_action(
    db_session: AsyncSession,
) -> None:
    action_id = await pending_cancellation(db_session)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pending = await client.get(
                "/api/v1/approvals?status=pending", headers=approval_headers()
            )
            hidden = await client.get(
                f"/api/v1/approvals/{action_id}", headers=approval_headers(store="harbor")
            )
            approved = await client.post(
                f"/api/v1/approvals/{action_id}/approve", headers=approval_headers()
            )
            repeated = await client.post(
                f"/api/v1/approvals/{action_id}/approve", headers=approval_headers()
            )
    finally:
        app.dependency_overrides.clear()

    assert pending.status_code == 200
    assert [item["id"] for item in pending.json()] == [action_id]
    assert hidden.status_code == 404
    assert approved.status_code == 200
    assert approved.json()["status"] == "succeeded"
    assert repeated.status_code == 200
    assert repeated.json()["result"] == approved.json()["result"]
