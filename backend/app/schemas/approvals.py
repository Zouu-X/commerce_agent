from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditLogRead(StrictSchema):
    id: UUID
    event_index: int
    event_type: str
    actor_type: str
    actor_id: str
    details: dict[str, Any]
    created_at: datetime


class PendingActionRead(StrictSchema):
    id: UUID
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    conversation_id: UUID
    trace_id: UUID
    action_type: str
    status: str
    payload: dict[str, Any]
    requested_by: str
    reviewed_by: str | None
    rejection_reason: str | None
    result: dict[str, Any] | None
    failure_code: str | None
    created_at: datetime
    reviewed_at: datetime | None
    executed_at: datetime | None
    updated_at: datetime
    audit_logs: list[AuditLogRead]


class RejectActionRequest(StrictSchema):
    reason: str = Field(min_length=1, max_length=500)
