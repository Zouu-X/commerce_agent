from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ToolContext:
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    conversation_id: UUID
    trace_id: UUID
