import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommerceContext:
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    customer_id: uuid.UUID
