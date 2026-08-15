from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.commerce.seed import stable_id
from app.knowledge.service import KnowledgeSearchService

pytestmark = pytest.mark.anyio


def context(store_key: str) -> CommerceContext:
    return CommerceContext(
        tenant_id=stable_id(f"tenant:{store_key}"),
        store_id=stable_id(f"store:{store_key}"),
        customer_id=stable_id(f"customer:{store_key}:0"),
    )


async def test_search_isolates_store_and_excludes_expired_policy(
    db_session: AsyncSession,
) -> None:
    service = KnowledgeSearchService(db_session)

    aurora_hits = await service.search(
        context("aurora"),
        "无理由退货可以申请多少天？",
        document_type="policy",
        limit=3,
    )
    harbor_hits = await service.search(
        context("harbor"),
        "无理由退货可以申请多少天？",
        document_type="policy",
        limit=3,
    )

    assert "支持签收后 7 天内" in aurora_hits[0].content
    assert "极光生活旗舰店" in aurora_hits[0].content
    assert "支持签收后 15 天内" in harbor_hits[0].content
    assert "海港数码专营店" in harbor_hits[0].content
    assert all(hit.version == "v1" for hit in [*aurora_hits, *harbor_hits])
    assert all("30 天无理由退货" not in hit.content for hit in [*aurora_hits, *harbor_hits])


async def test_search_applies_effective_date_before_ranking(
    db_session: AsyncSession,
) -> None:
    service = KnowledgeSearchService(db_session)

    historical_hits = await service.search(
        context("aurora"),
        "30 天无理由退货",
        document_type="policy",
        limit=5,
        as_of=datetime(2024, 7, 13, tzinfo=UTC),
    )
    current_hits = await service.search(
        context("aurora"),
        "30 天无理由退货",
        document_type="policy",
        limit=5,
        as_of=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert historical_hits[0].version == "v0"
    assert "30 天无理由退货" in historical_hits[0].content
    assert all(hit.version == "v1" for hit in current_hits)
    assert all("已经失效" not in hit.content for hit in current_hits)


async def test_hybrid_search_recalls_semantic_alias_and_document_type(
    db_session: AsyncSession,
) -> None:
    service = KnowledgeSearchService(db_session)

    policy_hits = await service.search(
        context("aurora"),
        "付款以后商品降价了能退差额吗？",
        document_type="policy",
        limit=3,
    )
    guide_hits = await service.search(
        context("aurora"),
        "耳机应该怎么维护？",
        document_type="product_guide",
        limit=3,
    )

    assert [hit.citation_id for hit in policy_hits] == [
        "price-protection:v1#chunk-1"
    ]
    assert "保价" in policy_hits[0].title
    assert guide_hits
    assert all(hit.document_type == "product_guide" for hit in guide_hits)
    assert "清洁" in guide_hits[0].content


@pytest.mark.parametrize("query", ["今天天气怎么样", "火星移民政策是什么？"])
async def test_irrelevant_queries_return_no_evidence(
    db_session: AsyncSession,
    query: str,
) -> None:
    hits = await KnowledgeSearchService(db_session).search(
        context("aurora"),
        query,
        document_type="policy",
        limit=5,
    )

    assert hits == []


async def test_order_number_shipping_policy_query_excludes_cancellation_policy(
    db_session: AsyncSession,
) -> None:
    hits = await KnowledgeSearchService(db_session).search(
        context("aurora"),
        "订单 AUR-202607-0001 的发货时效政策是什么？",
        document_type="policy",
        limit=3,
    )

    assert [hit.citation_id for hit in hits] == ["shipping-time:v1#chunk-1"]
