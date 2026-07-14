from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import ConversationMemory
from app.agent.provider import ModelProvider
from app.agent.runtime import AgentLimits, AgentRuntime
from app.api.dependencies import get_commerce_context, get_model_provider
from app.commerce.context import CommerceContext
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models import Conversation, Message
from app.schemas.agent import AgentTurnRead, ConversationRead, MessageCreate, MessageRead

router = APIRouter(prefix="/api/v1", tags=["agent"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ContextDependency = Annotated[CommerceContext, Depends(get_commerce_context)]
ProviderDependency = Annotated[ModelProvider, Depends(get_model_provider)]


def message_response(message: Message) -> MessageRead:
    return MessageRead(
        id=message.id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        tool_calls=message.tool_calls_json or [],
        created_at=message.created_at,
    )


def conversation_response(
    conversation: Conversation, messages: list[Message]
) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        status=conversation.status,
        tenant_id=conversation.tenant_id,
        store_id=conversation.store_id,
        customer_id=conversation.customer_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[message_response(message) for message in messages],
    )


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    session: SessionDependency,
    context: ContextDependency,
) -> ConversationRead:
    conversation = await ConversationMemory(session).create(context)
    await session.commit()
    return conversation_response(conversation, [])


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    context: ContextDependency,
) -> ConversationRead:
    memory = ConversationMemory(session)
    conversation = await memory.get(context, conversation_id)
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.sequence)
        )
    )
    return conversation_response(conversation, messages)


@router.post("/conversations/{conversation_id}/messages", response_model=AgentTurnRead)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    session: SessionDependency,
    context: ContextDependency,
    provider: ProviderDependency,
) -> AgentTurnRead:
    conversation = await ConversationMemory(session).get(
        context, conversation_id, for_update=True
    )
    settings = get_settings()
    result = await AgentRuntime(
        session,
        provider,
        limits=AgentLimits(
            max_model_loops=settings.agent_max_model_loops,
            max_tool_calls=settings.agent_max_tool_calls,
            model_timeout_seconds=settings.model_timeout_seconds,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            total_timeout_seconds=settings.agent_total_timeout_seconds,
            history_limit=settings.agent_history_limit,
            tool_result_max_chars=settings.agent_tool_result_max_chars,
        ),
    ).run(conversation, context, payload.content)
    await session.commit()
    return AgentTurnRead(
        trace_id=result.trace_id,
        conversation_id=conversation.id,
        message=message_response(result.message),
        model_loops=result.model_loops,
        tool_calls=result.tool_calls,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )
