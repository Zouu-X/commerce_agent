from typing import Annotated
from uuid import UUID

from fastapi import Header

from app.commerce.context import CommerceContext


async def get_commerce_context(
    tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
    store_id: Annotated[UUID, Header(alias="X-Store-Id")],
    customer_id: Annotated[UUID, Header(alias="X-Customer-Id")],
) -> CommerceContext:
    return CommerceContext(
        tenant_id=tenant_id,
        store_id=store_id,
        customer_id=customer_id,
    )
