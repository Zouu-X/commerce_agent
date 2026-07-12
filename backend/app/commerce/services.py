from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.commerce.context import CommerceContext
from app.commerce.errors import ResourceNotFoundError
from app.models import AfterSale, Order, OrderItem, Product, ProductVariant, Shipment


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_products(
        self,
        context: CommerceContext,
        *,
        query: str | None = None,
        category: str | None = None,
        in_stock: bool | None = None,
        max_price: Decimal | None = None,
        limit: int = 20,
    ) -> list[Product]:
        statement = (
            select(Product)
            .where(
                Product.tenant_id == context.tenant_id,
                Product.store_id == context.store_id,
                Product.status == "active",
            )
            .options(selectinload(Product.variants))
            .order_by(Product.name)
            .limit(limit)
        )
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(Product.name.ilike(pattern), Product.description.ilike(pattern))
            )
        if category:
            statement = statement.where(Product.category == category)
        if in_stock is True:
            statement = statement.where(Product.variants.any(ProductVariant.stock_quantity > 0))
        if in_stock is False:
            statement = statement.where(~Product.variants.any(ProductVariant.stock_quantity > 0))
        if max_price is not None:
            statement = statement.where(Product.variants.any(ProductVariant.price <= max_price))

        return list((await self._session.scalars(statement)).unique().all())

    async def get_product(self, context: CommerceContext, product_id: UUID) -> Product:
        statement = (
            select(Product)
            .where(
                Product.id == product_id,
                Product.tenant_id == context.tenant_id,
                Product.store_id == context.store_id,
                Product.status == "active",
            )
            .options(selectinload(Product.variants))
        )
        product = await self._session.scalar(statement)
        if product is None:
            raise ResourceNotFoundError("product_not_found")
        return product


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _with_details(statement: Select[tuple[Order]]) -> Select[tuple[Order]]:
        return statement.options(
            selectinload(Order.items)
            .selectinload(OrderItem.variant)
            .selectinload(ProductVariant.product),
            selectinload(Order.shipment),
            selectinload(Order.after_sales),
        )

    async def list_orders(self, context: CommerceContext, *, limit: int = 20) -> list[Order]:
        statement = self._with_details(
            select(Order)
            .where(
                Order.tenant_id == context.tenant_id,
                Order.store_id == context.store_id,
                Order.customer_id == context.customer_id,
            )
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).unique().all())

    async def get_order(self, context: CommerceContext, order_number: str) -> Order:
        statement = self._with_details(
            select(Order).where(
                Order.order_number == order_number,
                Order.tenant_id == context.tenant_id,
                Order.store_id == context.store_id,
                Order.customer_id == context.customer_id,
            )
        )
        order = await self._session.scalar(statement)
        if order is None:
            raise ResourceNotFoundError("order_not_found")
        return order


class ShipmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def track_shipment(
        self,
        context: CommerceContext,
        order_number: str,
        *,
        as_of: datetime | None = None,
    ) -> tuple[Shipment, str | None]:
        statement = (
            select(Shipment)
            .join(Order, Shipment.order_id == Order.id)
            .where(
                Order.order_number == order_number,
                Order.tenant_id == context.tenant_id,
                Order.store_id == context.store_id,
                Order.customer_id == context.customer_id,
            )
            .options(selectinload(Shipment.events))
        )
        shipment = await self._session.scalar(statement)
        if shipment is None:
            raise ResourceNotFoundError("shipment_not_found")

        current_time = as_of or datetime.now(UTC)
        last_updated_at = shipment.last_updated_at
        if last_updated_at.tzinfo is None:
            last_updated_at = last_updated_at.replace(tzinfo=UTC)
        anomaly: str | None = None
        if shipment.status == "delivery_failed":
            anomaly = "DELIVERY_FAILED"
        elif shipment.status != "delivered" and current_time - last_updated_at >= timedelta(days=5):
            anomaly = "NO_UPDATE_5_DAYS"

        shipment.events.sort(key=lambda event: event.occurred_at, reverse=True)
        return shipment, anomaly


class AfterSaleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_after_sale(self, context: CommerceContext, after_sale_id: UUID) -> AfterSale:
        statement = (
            select(AfterSale)
            .join(Order, AfterSale.order_id == Order.id)
            .where(
                AfterSale.id == after_sale_id,
                AfterSale.tenant_id == context.tenant_id,
                AfterSale.customer_id == context.customer_id,
                Order.store_id == context.store_id,
            )
            .options(selectinload(AfterSale.order))
        )
        after_sale = await self._session.scalar(statement)
        if after_sale is None:
            raise ResourceNotFoundError("after_sale_not_found")
        return after_sale


class OrderPolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self._orders = OrderService(session)

    async def evaluate(
        self,
        context: CommerceContext,
        order_number: str,
        *,
        requested_refund_amount: Decimal | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, bool | str | Decimal | None]:
        order = await self._orders.get_order(context, order_number)
        can_cancel = order.status in {"pending", "paid"}
        cancel_reason = None if can_cancel else f"ORDER_STATUS_{order.status.upper()}"

        current_time = as_of or datetime.now(UTC)
        created_at = order.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        within_return_window = current_time - created_at <= timedelta(days=7)
        refundable_status = order.status in {"shipped", "delivered"}
        amount_valid = (
            requested_refund_amount is None or requested_refund_amount <= order.total_amount
        )
        can_request_refund = (
            order.payment_status == "paid"
            and refundable_status
            and within_return_window
            and amount_valid
        )
        refund_reason: str | None = None
        if order.payment_status != "paid":
            refund_reason = "PAYMENT_NOT_COMPLETED"
        elif not refundable_status:
            refund_reason = f"ORDER_STATUS_{order.status.upper()}"
        elif not within_return_window:
            refund_reason = "RETURN_WINDOW_EXPIRED"
        elif not amount_valid:
            refund_reason = "REFUND_AMOUNT_EXCEEDS_PAID_AMOUNT"

        return {
            "can_cancel": can_cancel,
            "cancel_reason": cancel_reason,
            "can_request_refund": can_request_refund,
            "refund_reason": refund_reason,
            "max_refund_amount": order.total_amount,
        }
