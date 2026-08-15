from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ApprovalContext:
    tenant_id: UUID
    store_id: UUID
    approver_id: str
