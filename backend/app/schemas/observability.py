from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ObservabilitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceEventRead(ObservabilitySchema):
    id: UUID
    event_index: int
    event_type: str
    name: str
    status: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: Decimal | None
    created_at: datetime


class AgentTraceSummaryRead(ObservabilitySchema):
    id: UUID
    conversation_id: UUID
    customer_id: UUID
    status: str
    model_provider: str
    model_name: str
    prompt_version: str
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    first_model_response_ms: int | None
    total_latency_ms: int | None
    final_response_preview: str | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


class AgentTraceRead(AgentTraceSummaryRead):
    tenant_id: UUID
    store_id: UUID
    events: list[TraceEventRead]
