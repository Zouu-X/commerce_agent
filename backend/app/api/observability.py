from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_trace_context
from app.db.session import get_db_session
from app.models import AgentTrace, TraceEvent
from app.observability.context import TraceContext
from app.schemas.observability import (
    AgentTraceRead,
    AgentTraceSummaryRead,
    TraceEventRead,
)

router = APIRouter(prefix="/api/v1/traces", tags=["observability"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ContextDependency = Annotated[TraceContext, Depends(get_trace_context)]


def event_response(event: TraceEvent) -> TraceEventRead:
    return TraceEventRead(
        id=event.id,
        event_index=event.event_index,
        event_type=event.event_type,
        name=event.name,
        status=event.status,
        input=event.input_json,
        output=event.output_json,
        latency_ms=event.latency_ms,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        estimated_cost_usd=event.estimated_cost_usd,
        created_at=event.created_at,
    )


def summary_response(trace: AgentTrace) -> AgentTraceSummaryRead:
    return AgentTraceSummaryRead(
        id=trace.id,
        conversation_id=trace.conversation_id,
        customer_id=trace.customer_id,
        status=trace.status,
        model_provider=trace.model_provider,
        model_name=trace.model_name,
        prompt_version=trace.prompt_version,
        model_calls=trace.model_calls,
        tool_calls=trace.tool_calls,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        estimated_cost_usd=trace.estimated_cost_usd,
        first_model_response_ms=trace.first_model_response_ms,
        total_latency_ms=trace.total_latency_ms,
        final_response_preview=trace.final_response_preview,
        error_code=trace.error_code,
        started_at=trace.started_at,
        completed_at=trace.completed_at,
    )


@router.get("", response_model=list[AgentTraceSummaryRead])
async def list_traces(
    session: SessionDependency,
    context: ContextDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[AgentTraceSummaryRead]:
    traces = list(
        await session.scalars(
            select(AgentTrace)
            .where(
                AgentTrace.tenant_id == context.tenant_id,
                AgentTrace.store_id == context.store_id,
            )
            .order_by(AgentTrace.started_at.desc())
            .limit(limit)
        )
    )
    return [summary_response(trace) for trace in traces]


@router.get("/{trace_id}", response_model=AgentTraceRead)
async def get_trace(
    trace_id: UUID,
    session: SessionDependency,
    context: ContextDependency,
) -> AgentTraceRead:
    trace = await session.scalar(
        select(AgentTrace)
        .options(selectinload(AgentTrace.events))
        .where(
            AgentTrace.id == trace_id,
            AgentTrace.tenant_id == context.tenant_id,
            AgentTrace.store_id == context.store_id,
        )
    )
    if trace is None:
        raise HTTPException(status_code=404, detail="trace_not_found")
    summary = summary_response(trace)
    return AgentTraceRead(
        **summary.model_dump(),
        tenant_id=trace.tenant_id,
        store_id=trace.store_id,
        events=[event_response(event) for event in trace.events],
    )
