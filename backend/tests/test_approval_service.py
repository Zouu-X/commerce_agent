from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import ConversationMemory
from app.approvals.context import ApprovalContext
from app.approvals.errors import ActionValidationError, ApprovalNotFoundError
from app.approvals.service import ActionRequestService, ApprovalService
from app.commerce.context import CommerceContext
from app.commerce.seed import BASE_TIME, clear_commerce_data, stable_id
from app.commerce.services import OrderService
from app.models import CouponGrant, PendingAction, RefundTransaction
from app.tools.context import ToolContext

pytestmark = pytest.mark.anyio


def commerce_context(customer_index: int, *, store: str = "aurora") -> CommerceContext:
    return CommerceContext(
        tenant_id=stable_id(f"tenant:{store}"),
        store_id=stable_id(f"store:{store}"),
        customer_id=stable_id(f"customer:{store}:{customer_index}"),
    )


async def tool_context(session: AsyncSession, customer_index: int) -> ToolContext:
    context = commerce_context(customer_index)
    conversation = await ConversationMemory(session).create(context)
    return ToolContext(
        tenant_id=context.tenant_id,
        store_id=context.store_id,
        customer_id=context.customer_id,
        conversation_id=conversation.id,
        trace_id=stable_id(f"trace:approval:{customer_index}"),
    )


def approval_context(*, store: str = "aurora") -> ApprovalContext:
    return ApprovalContext(
        tenant_id=stable_id(f"tenant:{store}"),
        store_id=stable_id(f"store:{store}"),
        approver_id="ops-reviewer@example.com",
    )


async def test_cancellation_requires_approval_and_duplicate_approve_is_idempotent(
    db_session: AsyncSession,
) -> None:
    context = commerce_context(0)
    request = await ActionRequestService(
        db_session, await tool_context(db_session, 0), clock=lambda: BASE_TIME
    ).request_cancellation("AUR-202607-0001", "顾客不再需要")
    order = await OrderService(db_session).get_order(context, "AUR-202607-0001")

    assert request.status == "pending"
    assert order.status == "paid"
    assert [event.event_type for event in request.audit_logs] == ["requested"]

    service = ApprovalService(db_session, approval_context(), clock=lambda: BASE_TIME)
    approved = await service.approve(request.id)
    repeated = await service.approve(request.id)
    order = await OrderService(db_session).get_order(context, "AUR-202607-0001")

    assert approved.id == repeated.id
    assert repeated.status == "succeeded"
    assert order.status == "cancelled"
    assert [event.event_type for event in repeated.audit_logs] == [
        "requested",
        "approved",
        "execution_started",
        "execution_succeeded",
    ]
    action_id = request.id
    db_session.expire_all()
    reloaded = await service.get_action(action_id)
    assert [event.event_index for event in reloaded.audit_logs] == [1, 2, 3, 4]


async def test_rejected_action_never_changes_business_data(db_session: AsyncSession) -> None:
    context = commerce_context(0)
    action = await ActionRequestService(
        db_session, await tool_context(db_session, 0), clock=lambda: BASE_TIME
    ).request_cancellation("AUR-202607-0001", "误拍")

    rejected = await ApprovalService(
        db_session, approval_context(), clock=lambda: BASE_TIME
    ).reject(action.id, "订单已进入人工复核")
    order = await OrderService(db_session).get_order(context, "AUR-202607-0001")

    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "订单已进入人工复核"
    assert order.status == "paid"


async def test_refund_and_coupon_execute_once(db_session: AsyncSession) -> None:
    refund_action = await ActionRequestService(
        db_session, await tool_context(db_session, 2), clock=lambda: BASE_TIME
    ).request_refund("AUR-202607-0003", Decimal("50.00"), "商品与描述不符")
    refund_service = ApprovalService(db_session, approval_context(), clock=lambda: BASE_TIME)

    refund = await refund_service.approve(refund_action.id)
    await refund_service.approve(refund_action.id)
    refund_count = await db_session.scalar(select(func.count()).select_from(RefundTransaction))

    coupon_action = await ActionRequestService(
        db_session, await tool_context(db_session, 4), clock=lambda: BASE_TIME
    ).request_coupon(
        Decimal("10.00"),
        "物流超过五天未更新",
        order_number="AUR-202607-0005",
    )
    coupon_service = ApprovalService(db_session, approval_context(), clock=lambda: BASE_TIME)
    coupon = await coupon_service.approve(coupon_action.id)
    await coupon_service.approve(coupon_action.id)
    coupon_count = await db_session.scalar(select(func.count()).select_from(CouponGrant))

    assert refund.status == "succeeded"
    assert refund.result_json is not None
    assert refund.result_json["amount"] == "50.00"
    assert refund_count == 1
    assert coupon.status == "succeeded"
    assert coupon.result_json is not None
    assert coupon.result_json["code"].startswith("CARE-")
    assert coupon_count == 1


async def test_approval_scope_hides_other_store_actions(db_session: AsyncSession) -> None:
    action = await ActionRequestService(
        db_session, await tool_context(db_session, 0), clock=lambda: BASE_TIME
    ).request_cancellation("AUR-202607-0001", "误拍")

    with pytest.raises(ApprovalNotFoundError, match="approval_not_found"):
        await ApprovalService(db_session, approval_context(store="harbor")).get_action(action.id)


async def test_partial_refund_can_be_followed_by_another_refund(
    db_session: AsyncSession,
) -> None:
    context = await tool_context(db_session, 2)
    request_service = ActionRequestService(db_session, context, clock=lambda: BASE_TIME)
    approval_service = ApprovalService(db_session, approval_context(), clock=lambda: BASE_TIME)

    first = await request_service.request_refund(
        "AUR-202607-0003", Decimal("20.00"), "先退部分金额"
    )
    await approval_service.approve(first.id)
    second = await request_service.request_refund(
        "AUR-202607-0003", Decimal("10.00"), "再次退部分金额"
    )
    await approval_service.approve(second.id)

    refunds = await db_session.scalar(
        select(func.count()).select_from(RefundTransaction)
    )
    assert refunds == 2


async def test_reset_demo_can_clear_completed_actions(db_session: AsyncSession) -> None:
    action = await ActionRequestService(
        db_session, await tool_context(db_session, 4), clock=lambda: BASE_TIME
    ).request_coupon(Decimal("10.00"), "物流补偿")
    await ApprovalService(
        db_session, approval_context(), clock=lambda: BASE_TIME
    ).approve(action.id)

    await clear_commerce_data(db_session)

    assert await db_session.scalar(select(func.count()).select_from(CouponGrant)) == 0


async def test_idempotency_is_scoped_to_one_agent_turn(db_session: AsyncSession) -> None:
    context = await tool_context(db_session, 4)
    first_service = ActionRequestService(db_session, context, clock=lambda: BASE_TIME)
    first = await first_service.request_coupon(Decimal("10.00"), "物流补偿")
    retry = await first_service.request_coupon(Decimal("10.00"), "物流补偿")

    next_turn = replace(context, trace_id=uuid4())
    second = await ActionRequestService(
        db_session, next_turn, clock=lambda: BASE_TIME
    ).request_coupon(Decimal("10.00"), "物流补偿")
    action_count = await db_session.scalar(select(func.count()).select_from(PendingAction))

    assert retry.id == first.id
    assert second.id != first.id
    assert action_count == 2


@pytest.mark.parametrize("amount", [Decimal("10.001"), Decimal("0.001")])
async def test_service_rejects_amounts_the_database_would_round(
    db_session: AsyncSession,
    amount: Decimal,
) -> None:
    service = ActionRequestService(
        db_session, await tool_context(db_session, 4), clock=lambda: BASE_TIME
    )

    with pytest.raises(ActionValidationError, match="AMOUNT_PRECISION_INVALID"):
        await service.request_coupon(amount, "金额精度测试")
