from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Shipment, ShipmentEvent

REFUND_WINDOW = timedelta(days=7)


async def refund_window_is_open(
    session: AsyncSession,
    order: Order,
    *,
    as_of: datetime,
) -> bool:
    """Evaluate the customer-facing refund window from fulfilment state.

    A delivery failure has not started the post-receipt return window. The Agent may
    therefore create an approval request, leaving the merchant to confirm that the
    parcel has returned before approving it. Delivered orders use the delivery event;
    legacy orders without that event fall back to their creation time.
    """

    shipment_status = await session.scalar(
        select(Shipment.status).where(Shipment.order_id == order.id)
    )
    if shipment_status == "delivery_failed":
        return True

    delivered_at = await session.scalar(
        select(func.max(ShipmentEvent.occurred_at))
        .join(Shipment, ShipmentEvent.shipment_id == Shipment.id)
        .where(
            Shipment.order_id == order.id,
            ShipmentEvent.status == "delivered",
        )
    )
    window_started_at = delivered_at or order.created_at
    if window_started_at.tzinfo is None:
        window_started_at = window_started_at.replace(tzinfo=UTC)
    return as_of - window_started_at <= REFUND_WINDOW
