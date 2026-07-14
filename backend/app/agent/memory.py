from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ConversationNotFoundError, InvalidCommerceContextError
from app.agent.types import ProviderMessage, ToolCall
from app.commerce.context import CommerceContext
from app.models import Conversation, Customer, Message, Store


class ConversationMemory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, context: CommerceContext) -> Conversation:
        valid_context = await self._session.scalar(
            select(Customer.id)
            .join(Store, Store.tenant_id == Customer.tenant_id)
            .where(
                Customer.id == context.customer_id,
                Customer.tenant_id == context.tenant_id,
                Store.id == context.store_id,
                Store.tenant_id == context.tenant_id,
            )
        )
        if valid_context is None:
            raise InvalidCommerceContextError("invalid_commerce_context")
        conversation = Conversation(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
            status="active",
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get(
        self,
        context: CommerceContext,
        conversation_id: UUID,
        *,
        for_update: bool = False,
    ) -> Conversation:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == context.tenant_id,
            Conversation.store_id == context.store_id,
            Conversation.customer_id == context.customer_id,
        )
        if for_update:
            statement = statement.with_for_update()
        conversation = await self._session.scalar(statement)
        if conversation is None:
            raise ConversationNotFoundError("conversation_not_found")
        return conversation

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        messages = list(
            await self._session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence.desc())
                .limit(limit)
            )
        )
        messages.reverse()
        while messages and (
            messages[0].role == "tool"
            or (messages[0].role == "assistant" and messages[0].tool_calls_json)
        ):
            messages.pop(0)
        return messages

    def append(
        self,
        conversation: Conversation,
        *,
        sequence: int,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> Message:
        conversation.updated_at = datetime.now(UTC)
        message = Message(
            conversation_id=conversation.id,
            sequence=sequence,
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_calls_json=(
                [tool_call.to_storage_dict() for tool_call in tool_calls] if tool_calls else None
            ),
        )
        self._session.add(message)
        return message


def to_provider_message(message: Message) -> ProviderMessage:
    tool_calls = [
        ToolCall(
            id=str(raw["id"]),
            name=str(raw["name"]),
            arguments=_arguments(raw.get("arguments")),
        )
        for raw in message.tool_calls_json or []
    ]
    return ProviderMessage(
        role=_provider_role(message.role),
        content=message.content,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        tool_calls=tool_calls,
    )


def _arguments(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"__invalid_arguments__": value}


def _provider_role(role: str) -> Literal["user", "assistant", "tool"]:
    if role not in {"user", "assistant", "tool"}:
        raise ValueError(f"invalid_message_role:{role}")
    return cast(Literal["user", "assistant", "tool"], role)
