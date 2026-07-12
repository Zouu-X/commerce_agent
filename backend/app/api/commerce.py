from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_commerce_context
from app.commerce.context import CommerceContext
from app.commerce.services import (
    AfterSaleService,
    CatalogService,
    OrderPolicyService,
    OrderService,
    ShipmentService,
)
from app.db.session import get_db_session
from app.models import Customer, Order, Product, Store, Tenant
from app.schemas.commerce import (
    AfterSaleRead,
    DemoContextRead,
    DemoCustomerRead,
    OrderEligibilityRead,
    OrderItemRead,
    OrderRead,
    ProductRead,
    ShipmentEventRead,
    ShipmentRead,
    VariantRead,
)

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ContextDependency = Annotated[CommerceContext, Depends(get_commerce_context)]


def product_response(product: Product) -> ProductRead:
    return ProductRead(
        id=product.id,
        name=product.name,
        description=product.description,
        category=product.category,
        variants=[
            VariantRead(
                id=variant.id,
                sku=variant.sku,
                attributes=variant.attributes_json,
                price=variant.price,
                stock_quantity=variant.stock_quantity,
            )
            for variant in product.variants
        ],
    )


def order_response(order: Order) -> OrderRead:
    return OrderRead(
        order_number=order.order_number,
        status=order.status,
        payment_status=order.payment_status,
        total_amount=order.total_amount,
        created_at=order.created_at,
        items=[
            OrderItemRead(
                sku=item.variant.sku,
                product_name=item.variant.product.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in order.items
        ],
        has_shipment=order.shipment is not None,
        after_sale_ids=[after_sale.id for after_sale in order.after_sales],
    )


@router.get("/demo/contexts", response_model=list[DemoContextRead], tags=["demo"])
async def list_demo_contexts(session: SessionDependency) -> list[DemoContextRead]:
    tenant_stores = (
        await session.execute(
            select(Tenant, Store)
            .join(Store, Store.tenant_id == Tenant.id)
            .order_by(Tenant.name, Store.name)
        )
    ).all()
    contexts: list[DemoContextRead] = []
    for tenant, store in tenant_stores:
        customers = list(
            await session.scalars(
                select(Customer)
                .where(Customer.tenant_id == tenant.id)
                .order_by(Customer.display_name)
            )
        )
        featured_orders = list(
            await session.scalars(
                select(Order.order_number)
                .where(Order.tenant_id == tenant.id, Order.store_id == store.id)
                .order_by(Order.created_at.desc())
                .limit(5)
            )
        )
        contexts.append(
            DemoContextRead(
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                store_id=store.id,
                store_name=store.name,
                customers=[
                    DemoCustomerRead(
                        id=customer.id,
                        display_name=customer.display_name,
                        membership_level=customer.membership_level,
                    )
                    for customer in customers
                ],
                featured_orders=featured_orders,
            )
        )
    return contexts


@router.get("/catalog/products", response_model=list[ProductRead], tags=["catalog"])
async def search_products(
    session: SessionDependency,
    context: ContextDependency,
    query: str | None = None,
    category: str | None = None,
    in_stock: bool | None = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ProductRead]:
    products = await CatalogService(session).search_products(
        context,
        query=query,
        category=category,
        in_stock=in_stock,
        max_price=max_price,
        limit=limit,
    )
    return [product_response(product) for product in products]


@router.get("/catalog/products/{product_id}", response_model=ProductRead, tags=["catalog"])
async def get_product(
    product_id: UUID, session: SessionDependency, context: ContextDependency
) -> ProductRead:
    return product_response(await CatalogService(session).get_product(context, product_id))


@router.get("/orders", response_model=list[OrderRead], tags=["orders"])
async def list_orders(
    session: SessionDependency,
    context: ContextDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[OrderRead]:
    orders = await OrderService(session).list_orders(context, limit=limit)
    return [order_response(order) for order in orders]


@router.get("/orders/{order_number}", response_model=OrderRead, tags=["orders"])
async def get_order(
    order_number: str, session: SessionDependency, context: ContextDependency
) -> OrderRead:
    return order_response(await OrderService(session).get_order(context, order_number))


@router.get(
    "/orders/{order_number}/eligibility", response_model=OrderEligibilityRead, tags=["orders"]
)
async def get_order_eligibility(
    order_number: str,
    session: SessionDependency,
    context: ContextDependency,
    requested_refund_amount: Annotated[Decimal | None, Query(ge=0)] = None,
) -> OrderEligibilityRead:
    decision = await OrderPolicyService(session).evaluate(
        context,
        order_number,
        requested_refund_amount=requested_refund_amount,
    )
    return OrderEligibilityRead.model_validate(decision)


@router.get("/orders/{order_number}/shipment", response_model=ShipmentRead, tags=["logistics"])
async def track_shipment(
    order_number: str, session: SessionDependency, context: ContextDependency
) -> ShipmentRead:
    shipment, anomaly = await ShipmentService(session).track_shipment(context, order_number)
    return ShipmentRead(
        carrier=shipment.carrier,
        tracking_number=shipment.tracking_number,
        status=shipment.status,
        last_updated_at=shipment.last_updated_at,
        anomaly=anomaly,
        events=[
            ShipmentEventRead(
                status=event.status,
                location=event.location,
                description=event.description,
                occurred_at=event.occurred_at,
            )
            for event in shipment.events
        ],
    )


@router.get("/after-sales/{after_sale_id}", response_model=AfterSaleRead, tags=["after-sales"])
async def get_after_sale(
    after_sale_id: UUID, session: SessionDependency, context: ContextDependency
) -> AfterSaleRead:
    after_sale = await AfterSaleService(session).get_after_sale(context, after_sale_id)
    return AfterSaleRead(
        id=after_sale.id,
        order_number=after_sale.order.order_number,
        type=after_sale.type,
        reason=after_sale.reason,
        status=after_sale.status,
        requested_amount=after_sale.requested_amount,
        created_at=after_sale.created_at,
    )
