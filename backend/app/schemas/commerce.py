from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VariantRead(StrictSchema):
    id: UUID
    sku: str
    attributes: dict[str, Any]
    price: Decimal
    stock_quantity: int


class ProductRead(StrictSchema):
    id: UUID
    name: str
    description: str
    category: str
    variants: list[VariantRead]


class OrderItemRead(StrictSchema):
    sku: str
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderRead(StrictSchema):
    order_number: str
    status: str
    payment_status: str
    total_amount: Decimal
    created_at: datetime
    items: list[OrderItemRead]
    has_shipment: bool
    after_sale_ids: list[UUID]


class ShipmentEventRead(StrictSchema):
    status: str
    location: str
    description: str
    occurred_at: datetime


class ShipmentRead(StrictSchema):
    carrier: str
    tracking_number: str
    status: str
    last_updated_at: datetime
    anomaly: str | None
    events: list[ShipmentEventRead]


class AfterSaleRead(StrictSchema):
    id: UUID
    order_number: str
    type: str
    reason: str
    status: str
    requested_amount: Decimal
    created_at: datetime


class OrderEligibilityRead(StrictSchema):
    can_cancel: bool
    cancel_reason: str | None
    can_request_refund: bool
    refund_reason: str | None
    max_refund_amount: Decimal


class DemoCustomerRead(StrictSchema):
    id: UUID
    display_name: str
    membership_level: str


class DemoContextRead(StrictSchema):
    tenant_id: UUID
    tenant_name: str
    store_id: UUID
    store_name: str
    customers: list[DemoCustomerRead]
    featured_orders: list[str] = Field(default_factory=list)
