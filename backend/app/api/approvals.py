from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_approval_context
from app.approvals.context import ApprovalContext
from app.approvals.service import ApprovalService
from app.db.session import get_db_session
from app.models import PendingAction
from app.schemas.approvals import AuditLogRead, PendingActionRead, RejectActionRequest

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ApprovalContextDependency = Annotated[ApprovalContext, Depends(get_approval_context)]


def action_response(action: PendingAction) -> PendingActionRead:
    return PendingActionRead(
        id=action.id,
        tenant_id=action.tenant_id,
        store_id=action.store_id,
        customer_id=action.customer_id,
        conversation_id=action.conversation_id,
        trace_id=action.trace_id,
        action_type=action.action_type,
        status=action.status,
        payload=action.payload_json,
        requested_by=action.requested_by,
        reviewed_by=action.reviewed_by,
        rejection_reason=action.rejection_reason,
        result=action.result_json,
        failure_code=action.failure_code,
        created_at=action.created_at,
        reviewed_at=action.reviewed_at,
        executed_at=action.executed_at,
        updated_at=action.updated_at,
        audit_logs=[
            AuditLogRead(
                id=log.id,
                event_index=log.event_index,
                event_type=log.event_type,
                actor_type=log.actor_type,
                actor_id=log.actor_id,
                details=log.details_json,
                created_at=log.created_at,
            )
            for log in action.audit_logs
        ],
    )


@router.get("", response_model=list[PendingActionRead])
async def list_approvals(
    session: SessionDependency,
    context: ApprovalContextDependency,
    action_status: Annotated[
        Literal["pending", "approved", "rejected", "executing", "succeeded", "failed"]
        | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PendingActionRead]:
    actions = await ApprovalService(session, context).list_actions(
        status=action_status,
        limit=limit,
    )
    return [action_response(action) for action in actions]


@router.get("/{action_id}", response_model=PendingActionRead)
async def get_approval(
    action_id: UUID,
    session: SessionDependency,
    context: ApprovalContextDependency,
) -> PendingActionRead:
    return action_response(await ApprovalService(session, context).get_action(action_id))


@router.post("/{action_id}/approve", response_model=PendingActionRead)
async def approve_action(
    action_id: UUID,
    session: SessionDependency,
    context: ApprovalContextDependency,
) -> PendingActionRead:
    action = await ApprovalService(session, context).approve(action_id)
    await session.commit()
    return action_response(action)


@router.post("/{action_id}/reject", response_model=PendingActionRead)
async def reject_action(
    action_id: UUID,
    payload: RejectActionRequest,
    session: SessionDependency,
    context: ApprovalContextDependency,
) -> PendingActionRead:
    action = await ApprovalService(session, context).reject(action_id, payload.reason)
    await session.commit()
    return action_response(action)
