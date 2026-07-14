from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.commerce.services import AfterSaleService, CatalogService, OrderService, ShipmentService
from app.schemas.commerce import (
    AfterSaleRead,
    OrderItemRead,
    OrderRead,
    ProductRead,
    ShipmentEventRead,
    ShipmentRead,
    VariantRead,
)
from app.tools.context import ToolContext


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchProductsArgs(ToolArguments):
    query: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    in_stock: bool | None = None
    max_price: Decimal | None = Field(default=None, ge=0)
    limit: int = Field(default=5, ge=1, le=20)


class GetProductDetailsArgs(ToolArguments):
    product_id: UUID


class GetCustomerOrdersArgs(ToolArguments):
    limit: int = Field(default=10, ge=1, le=20)


class GetOrderDetailsArgs(ToolArguments):
    order_number: str = Field(min_length=1, max_length=40)


class TrackShipmentArgs(ToolArguments):
    order_number: str = Field(min_length=1, max_length=40)


class GetAfterSaleStatusArgs(ToolArguments):
    after_sale_id: UUID


def _commerce_context(context: ToolContext) -> CommerceContext:
    return CommerceContext(
        tenant_id=context.tenant_id,
        store_id=context.store_id,
        customer_id=context.customer_id,
    )


def _variant_data(variant: Any) -> VariantRead:
    return VariantRead(
        id=variant.id,
        sku=variant.sku,
        attributes=variant.attributes_json,
        price=variant.price,
        stock_quantity=variant.stock_quantity,
    )


def _product_data(product: Any) -> ProductRead:
    return ProductRead(
        id=product.id,
        name=product.name,
        description=product.description,
        category=product.category,
        variants=[_variant_data(variant) for variant in product.variants],
    )


def _order_data(order: Any) -> OrderRead:
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


class ReadToolHandlers:
    def __init__(self, session: AsyncSession, context: ToolContext) -> None:
        self._session = session
        self._context = _commerce_context(context)

    async def search_products(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(SearchProductsArgs, raw_args)
        products = await CatalogService(self._session).search_products(
            self._context,
            query=args.query,
            category=args.category,
            in_stock=args.in_stock,
            max_price=args.max_price,
            limit=args.limit,
        )
        summaries = []
        for product in products:
            prices = [variant.price for variant in product.variants]
            summaries.append(
                {
                    "id": str(product.id),
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "min_price": str(min(prices)) if prices else None,
                    "total_stock": sum(variant.stock_quantity for variant in product.variants),
                }
            )
        return {"products": summaries, "count": len(summaries)}

    async def get_product_details(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(GetProductDetailsArgs, raw_args)
        product = await CatalogService(self._session).get_product(self._context, args.product_id)
        return _product_data(product).model_dump(mode="json")

    async def get_customer_orders(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(GetCustomerOrdersArgs, raw_args)
        orders = await OrderService(self._session).list_orders(self._context, limit=args.limit)
        return {
            "orders": [_order_data(order).model_dump(mode="json") for order in orders],
            "count": len(orders),
        }

    async def get_order_details(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(GetOrderDetailsArgs, raw_args)
        order = await OrderService(self._session).get_order(self._context, args.order_number)
        return _order_data(order).model_dump(mode="json")

    async def track_shipment(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(TrackShipmentArgs, raw_args)
        shipment, anomaly = await ShipmentService(self._session).track_shipment(
            self._context, args.order_number
        )
        response = ShipmentRead(
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
        return response.model_dump(mode="json")

    async def get_after_sale_status(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(GetAfterSaleStatusArgs, raw_args)
        after_sale = await AfterSaleService(self._session).get_after_sale(
            self._context, args.after_sale_id
        )
        return AfterSaleRead(
            id=after_sale.id,
            order_number=after_sale.order.order_number,
            type=after_sale.type,
            reason=after_sale.reason,
            status=after_sale.status,
            requested_amount=after_sale.requested_amount,
            created_at=after_sale.created_at,
        ).model_dump(mode="json")
