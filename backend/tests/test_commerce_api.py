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
