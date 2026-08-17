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


@pytest.mark.anyio
async def test_api_returns_404_instead_of_leaking_another_customers_order(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            owner_response = await client.get("/api/v1/orders/AUR-202607-0001", headers=headers(0))
            forbidden_response = await client.get(
                "/api/v1/orders/AUR-202607-0001", headers=headers(1)
            )
    finally:
        app.dependency_overrides.clear()

    assert owner_response.status_code == 200
    assert owner_response.json()["order_number"] == "AUR-202607-0001"
    assert forbidden_response.status_code == 404
    assert forbidden_response.json() == {"detail": "order_not_found"}


@pytest.mark.anyio
async def test_demo_contexts_include_customer_scoped_working_prompts(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/demo/contexts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    aurora = next(item for item in response.json() if item["tenant_name"] == "极光生活")
    lin_xiao = next(
        customer for customer in aurora["customers"] if customer["display_name"].startswith("林晓")
    )
    prompts = lin_xiao["sample_prompts"]
    assert "推荐有库存的降噪蓝牙耳机" in prompts
    assert "无理由退货政策是多少天？" in prompts
    assert "帮我查订单 AUR-202607-0001" in prompts
    assert "帮我取消订单 AUR-202607-0001，我不想要了" in prompts


@pytest.mark.anyio
async def test_demo_runtime_discloses_safe_model_and_evaluation_metadata() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/demo/runtime")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "mock",
        "model_name": "mock-commerce-agent",
        "model_mode": "provider-default",
        "uses_external_api": False,
        "evaluation_case_count": 60,
        "input_cost_per_million": "0.14",
        "output_cost_per_million": "0.28",
    }
