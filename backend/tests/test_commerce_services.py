from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.commerce.errors import ResourceNotFoundError
from app.commerce.seed import BASE_TIME, stable_id
from app.commerce.services import (
    AfterSaleService,
    CatalogService,
    OrderPolicyService,
    OrderService,
    ShipmentService,
)


def context(tenant: str, customer_index: int) -> CommerceContext:
    return CommerceContext(
        tenant_id=stable_id(f"tenant:{tenant}"),
        store_id=stable_id(f"store:{tenant}"),
        customer_id=stable_id(f"customer:{tenant}:{customer_index}"),
    )


@pytest.mark.anyio
async def test_catalog_is_scoped_to_tenant_and_store(db_session: AsyncSession) -> None:
    service = CatalogService(db_session)
    aurora_products = await service.search_products(context("aurora", 0), limit=50)
    out_of_stock = await service.search_products(context("aurora", 0), in_stock=False, limit=50)

    assert len(aurora_products) == 12
    assert len(out_of_stock) == 1
    assert all(product.tenant_id == stable_id("tenant:aurora") for product in aurora_products)

    with pytest.raises(ResourceNotFoundError, match="product_not_found"):
        await service.get_product(context("harbor", 0), stable_id("product:aurora:1"))


@pytest.mark.anyio
async def test_customer_cannot_read_another_customers_order(
    db_session: AsyncSession,
) -> None:
    service = OrderService(db_session)
    owner_context = context("aurora", 0)
    other_customer_context = context("aurora", 1)

    order = await service.get_order(owner_context, "AUR-202607-0001")
    assert order.customer_id == owner_context.customer_id

    with pytest.raises(ResourceNotFoundError, match="order_not_found"):
        await service.get_order(other_customer_context, "AUR-202607-0001")


@pytest.mark.anyio
async def test_tenant_cannot_read_another_tenants_order(db_session: AsyncSession) -> None:
    with pytest.raises(ResourceNotFoundError, match="order_not_found"):
        await OrderService(db_session).get_order(context("harbor", 0), "AUR-202607-0001")


@pytest.mark.anyio
async def test_logistics_detects_stale_and_failed_shipments(
    db_session: AsyncSession,
) -> None:
    service = ShipmentService(db_session)
    _, stale_anomaly = await service.track_shipment(
        context("aurora", 4), "AUR-202607-0005", as_of=BASE_TIME
    )
    _, failed_anomaly = await service.track_shipment(
        context("aurora", 5), "AUR-202607-0006", as_of=BASE_TIME
    )

    assert stale_anomaly == "NO_UPDATE_5_DAYS"
    assert failed_anomaly == "DELIVERY_FAILED"


@pytest.mark.anyio
async def test_refund_rules_reject_excess_and_expired_requests(
    db_session: AsyncSession,
) -> None:
    service = OrderPolicyService(db_session)
    eligible = await service.evaluate(
        context("aurora", 2),
        "AUR-202607-0003",
        requested_refund_amount=Decimal("50.00"),
        as_of=BASE_TIME,
    )
    excessive = await service.evaluate(
        context("aurora", 2),
        "AUR-202607-0003",
        requested_refund_amount=Decimal("9999.00"),
        as_of=BASE_TIME,
    )
    expired = await service.evaluate(
        context("aurora", 2),
        "AUR-202607-0003",
        requested_refund_amount=Decimal("50.00"),
        as_of=BASE_TIME + timedelta(days=10),
    )

    assert eligible["can_request_refund"] is True
    assert excessive["refund_reason"] == "REFUND_AMOUNT_EXCEEDS_PAID_AMOUNT"
    assert expired["refund_reason"] == "RETURN_WINDOW_EXPIRED"


@pytest.mark.anyio
async def test_after_sale_is_scoped_to_customer(db_session: AsyncSession) -> None:
    after_sale_id = stable_id("after-sale:aurora:2")
    after_sale = await AfterSaleService(db_session).get_after_sale(
        context("aurora", 2), after_sale_id
    )
    assert after_sale.status == "reviewing"

    with pytest.raises(ResourceNotFoundError, match="after_sale_not_found"):
        await AfterSaleService(db_session).get_after_sale(context("aurora", 3), after_sale_id)
