from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.seed import stable_id
from app.db.session import get_db_session
from app.main import app


def headers(store_key: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(stable_id(f"tenant:{store_key}")),
        "X-Store-Id": str(stable_id(f"store:{store_key}")),
        "X-Customer-Id": str(stable_id(f"customer:{store_key}:0")),
    }


@pytest.mark.anyio
async def test_knowledge_api_returns_scoped_citable_results(
    db_session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            aurora = await client.get(
                "/api/v1/knowledge/search",
                headers=headers("aurora"),
                params={"query": "什么时候发货", "document_type": "policy"},
            )
            harbor = await client.get(
                "/api/v1/knowledge/search",
                headers=headers("harbor"),
                params={"query": "什么时候发货", "document_type": "policy"},
            )
    finally:
        app.dependency_overrides.clear()

    assert aurora.status_code == 200
    assert aurora.json()["retrieval"] == "hybrid_rrf"
    assert aurora.json()["hits"][0]["citation_id"].startswith("shipping-time:v1")
    assert "48 小时" in aurora.json()["hits"][0]["content"]
    assert harbor.status_code == 200
    assert "24 小时" in harbor.json()["hits"][0]["content"]
    assert all("极光生活旗舰店" not in hit["content"] for hit in harbor.json()["hits"])
