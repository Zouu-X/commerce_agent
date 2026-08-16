from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TraceContext:
    tenant_id: UUID
    store_id: UUID
