from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageCreate(StrictSchema):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class MessageRead(StrictSchema):
    id: UUID
    sequence: int
    role: str
    content: str
    tool_call_id: str | None
    tool_name: str | None
    tool_calls: list[dict[str, Any]]
    created_at: datetime


class ConversationRead(StrictSchema):
    id: UUID
    status: str
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRead]


class AgentTurnRead(StrictSchema):
    trace_id: UUID
    conversation_id: UUID
    message: MessageRead
    model_loops: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
