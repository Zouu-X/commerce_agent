import json
from typing import Annotated, Any
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
from app.schemas.agent import (
    AgentTurnRead,
    ConversationRead,
    CustomerSourceRead,
    MessageCreate,
    MessageRead,
)

router = APIRouter(prefix="/api/v1", tags=["agent"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ContextDependency = Annotated[CommerceContext, Depends(get_commerce_context)]
ProviderDependency = Annotated[ModelProvider, Depends(get_model_provider)]


def message_response(
    message: Message, *, sources: list[CustomerSourceRead] | None = None
) -> MessageRead:
    return MessageRead(
        id=message.id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        tool_calls=message.tool_calls_json or [],
        sources=sources or [],
        created_at=message.created_at,
    )


def conversation_response(
    conversation: Conversation, messages: list[Message]
) -> ConversationRead:
    customer_messages: list[MessageRead] = []
    turn_sources: list[CustomerSourceRead] = []
    for message in messages:
        if message.role == "user":
            turn_sources = []
            customer_messages.append(message_response(message))
            continue
        if message.role == "tool":
            turn_sources.extend(_sources_from_tool_message(message.content))
            continue
        if message.role == "assistant" and not message.tool_calls_json:
            customer_messages.append(
                message_response(message, sources=_deduplicate_sources(turn_sources))
            )
    return ConversationRead(
        id=conversation.id,
        status=conversation.status,
        tenant_id=conversation.tenant_id,
        store_id=conversation.store_id,
        customer_id=conversation.customer_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=customer_messages,
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
        model_provider=settings.model_provider,
        model_name=settings.model_name,
        input_cost_per_million=settings.model_input_cost_per_million,
        output_cost_per_million=settings.model_output_cost_per_million,
    ).run(conversation, context, payload.content)
    await session.commit()
    return AgentTurnRead(
        trace_id=result.trace_id,
        conversation_id=conversation.id,
        message=message_response(
            result.message,
            sources=[
                CustomerSourceRead(title=source.title, version=source.version)
                for source in result.sources
            ],
        ),
        model_loops=result.model_loops,
        tool_calls=result.tool_calls,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


def _sources_from_tool_message(content: str) -> list[CustomerSourceRead]:
    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    sources = parsed.get("sources")
    if not isinstance(sources, list):
        return []
    return [
        CustomerSourceRead(title=str(item["title"]), version=str(item.get("version", "")))
        for item in sources
        if isinstance(item, dict) and item.get("title")
    ]


def _deduplicate_sources(sources: list[CustomerSourceRead]) -> list[CustomerSourceRead]:
    unique: dict[tuple[str, str], CustomerSourceRead] = {}
    for source in sources:
        unique.setdefault((source.title, source.version), source)
    return list(unique.values())
