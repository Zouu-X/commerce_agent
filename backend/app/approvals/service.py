from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.approvals.context import ApprovalContext
from app.approvals.errors import (
    ActionExecutionError,
    ActionValidationError,
    ApprovalNotFoundError,
    InvalidActionTransitionError,
)
from app.commerce.context import CommerceContext
from app.commerce.refund_policy import refund_window_is_open
from app.commerce.services import OrderService
from app.models import ActionAuditLog, CouponGrant, Order, PendingAction, RefundTransaction
from app.tools.context import ToolContext

Clock = Callable[[], datetime]
MAX_COUPON_AMOUNT = Decimal("50.00")
MONEY_QUANTUM = Decimal("0.01")
ACTIVE_ACTION_STATUSES = ("pending", "approved", "executing")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_money(amount: Decimal) -> Decimal:
    """Return a two-decimal amount or reject values the database would round."""
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or not isinstance(exponent, int) or exponent < -2:
        raise ValueError("AMOUNT_PRECISION_INVALID")
    try:
        return amount.quantize(MONEY_QUANTUM)
    except InvalidOperation as error:
        raise ValueError("AMOUNT_PRECISION_INVALID") from error


class ActionRequestService:
    """Validate an Agent request and persist intent without changing business data."""

    def __init__(
        self,
        session: AsyncSession,
        context: ToolContext,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._session = session
        self._tool_context = context
        self._context = CommerceContext(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
        )
        self._clock = clock

    async def request_cancellation(self, order_number: str, reason: str) -> PendingAction:
        order = await OrderService(self._session).get_order(self._context, order_number)
        existing = await self._find_active_order_action(
            "cancel_order", order.order_number
        )
        if existing is not None:
            return existing
        if order.status not in {"pending", "paid"}:
            raise ActionValidationError(f"ORDER_STATUS_{order.status.upper()}")
        return await self._create(
            "cancel_order",
            {"order_number": order.order_number, "reason": reason.strip()},
        )

    async def request_refund(
        self,
        order_number: str,
        amount: Decimal | None,
        reason: str,
    ) -> PendingAction:
        order = await OrderService(self._session).get_order(self._context, order_number)
        try:
            refund_amount = normalize_money(
                amount if amount is not None else order.total_amount
            )
        except ValueError as error:
            raise ActionValidationError(str(error)) from error
        await self._validate_refund(order, refund_amount)
        return await self._create(
            "refund",
            {
                "order_number": order.order_number,
                "amount": str(refund_amount),
                "reason": reason.strip(),
            },
        )

    async def request_coupon(
        self,
        amount: Decimal,
        reason: str,
        *,
        order_number: str | None = None,
    ) -> PendingAction:
        try:
            amount = normalize_money(amount)
        except ValueError as error:
            raise ActionValidationError(str(error)) from error
        if amount <= 0 or amount > MAX_COUPON_AMOUNT:
            raise ActionValidationError("COUPON_AMOUNT_OUT_OF_RANGE")
        normalized_order: str | None = None
        if order_number:
            order = await OrderService(self._session).get_order(self._context, order_number)
            normalized_order = order.order_number
        return await self._create(
            "issue_coupon",
            {
                "order_number": normalized_order,
                "amount": str(amount),
                "reason": reason.strip(),
            },
        )

    async def _validate_refund(self, order: Order, amount: Decimal) -> None:
        if amount <= 0:
            raise ActionValidationError("REFUND_AMOUNT_EXCEEDS_PAID_AMOUNT")
        if order.payment_status not in {"paid", "partially_refunded"}:
            raise ActionValidationError("PAYMENT_NOT_COMPLETED")
        if order.status not in {"shipped", "delivered"}:
            raise ActionValidationError(f"ORDER_STATUS_{order.status.upper()}")
        if not await refund_window_is_open(
            self._session, order, as_of=self._clock()
        ):
            raise ActionValidationError("RETURN_WINDOW_EXPIRED")
        refunded = await self._session.scalar(
            select(func.coalesce(func.sum(RefundTransaction.amount), 0)).where(
                RefundTransaction.order_id == order.id,
                RefundTransaction.status == "succeeded",
            )
        )
        if Decimal(str(refunded or 0)) + amount > order.total_amount:
            raise ActionValidationError("REFUND_AMOUNT_EXCEEDS_REMAINING")

    async def _create(self, action_type: str, payload: dict[str, Any]) -> PendingAction:
        idempotency_key = self._idempotency_key(action_type, payload)
        existing = await self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        action = PendingAction(
            tenant_id=self._tool_context.tenant_id,
            store_id=self._tool_context.store_id,
            customer_id=self._tool_context.customer_id,
            conversation_id=self._tool_context.conversation_id,
            trace_id=self._tool_context.trace_id,
            action_type=action_type,
            status="pending",
            payload_json=payload,
            idempotency_key=idempotency_key,
            requested_by=f"agent:{self._tool_context.conversation_id}",
        )
        action.audit_logs.append(
            self._audit(
                action,
                event_type="requested",
                actor_type="agent",
                actor_id=str(self._tool_context.conversation_id),
                details={"trace_id": str(self._tool_context.trace_id), "payload": payload},
            )
        )
        try:
            async with self._session.begin_nested():
                self._session.add(action)
                await self._session.flush()
        except IntegrityError:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing
        return action

    async def _find_by_idempotency_key(self, key: str) -> PendingAction | None:
        action: PendingAction | None = await self._session.scalar(
            select(PendingAction)
            .where(
                PendingAction.tenant_id == self._tool_context.tenant_id,
                PendingAction.idempotency_key == key,
            )
            .options(selectinload(PendingAction.audit_logs))
        )
        return action

    async def _find_active_order_action(
        self,
        action_type: str,
        order_number: str,
    ) -> PendingAction | None:
        actions = list(
            await self._session.scalars(
                select(PendingAction)
                .where(
                    PendingAction.tenant_id == self._tool_context.tenant_id,
                    PendingAction.store_id == self._tool_context.store_id,
                    PendingAction.customer_id == self._tool_context.customer_id,
                    PendingAction.action_type == action_type,
                    PendingAction.status.in_(ACTIVE_ACTION_STATUSES),
                )
                .order_by(PendingAction.created_at)
            )
        )
        return next(
            (
                action
                for action in actions
                if action.payload_json.get("order_number") == order_number
            ),
            None,
        )

    def _idempotency_key(self, action_type: str, payload: dict[str, Any]) -> str:
        material = json.dumps(
            {
                "tenant_id": str(self._tool_context.tenant_id),
                "store_id": str(self._tool_context.store_id),
                "customer_id": str(self._tool_context.customer_id),
                "conversation_id": str(self._tool_context.conversation_id),
                "trace_id": str(self._tool_context.trace_id),
                "action_type": action_type,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()

    @staticmethod
    def _audit(
        action: PendingAction,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str,
        details: dict[str, Any],
    ) -> ActionAuditLog:
        return ActionAuditLog(
            tenant_id=action.tenant_id,
            store_id=action.store_id,
            event_type=event_type,
            event_index=1,
            actor_type=actor_type,
            actor_id=actor_id,
            details_json=details,
        )


class ApprovalService:
    """Review pending actions and execute approved side effects exactly once."""

    def __init__(
        self,
        session: AsyncSession,
        context: ApprovalContext,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._session = session
        self._context = context
        self._clock = clock

    async def list_actions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[PendingAction]:
        statement = (
            select(PendingAction)
            .where(
                PendingAction.tenant_id == self._context.tenant_id,
                PendingAction.store_id == self._context.store_id,
            )
            .options(selectinload(PendingAction.audit_logs))
            .order_by(PendingAction.created_at.desc())
            .limit(limit)
        )
        if status:
            statement = statement.where(PendingAction.status == status)
        return list((await self._session.scalars(statement)).unique().all())

    async def get_action(self, action_id: UUID, *, for_update: bool = False) -> PendingAction:
        statement = (
            select(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.tenant_id == self._context.tenant_id,
                PendingAction.store_id == self._context.store_id,
            )
            .options(selectinload(PendingAction.audit_logs))
        )
        if for_update:
            statement = statement.with_for_update()
        action = await self._session.scalar(statement)
        if action is None:
            raise ApprovalNotFoundError("approval_not_found")
        return action

    async def approve(self, action_id: UUID) -> PendingAction:
        action = await self.get_action(action_id, for_update=True)
        if action.status == "succeeded":
            return action
        if action.status != "pending":
            raise InvalidActionTransitionError(f"cannot_approve_{action.status}")

        now = self._clock()
        action.status = "approved"
        action.reviewed_by = self._context.approver_id
        action.reviewed_at = now
        action.updated_at = now
        self._append_audit(action, "approved", "approver", self._context.approver_id)
        action.status = "executing"
        self._append_audit(action, "execution_started", "system", "approval-worker")
        try:
            result = await self._execute(action)
        except ActionExecutionError as error:
            action.status = "failed"
            action.failure_code = str(error)
            action.executed_at = now
            self._append_audit(
                action,
                "execution_failed",
                "system",
                "approval-worker",
                {"failure_code": str(error)},
            )
        else:
            action.status = "succeeded"
            action.result_json = result
            action.executed_at = now
            self._append_audit(
                action,
                "execution_succeeded",
                "system",
                "approval-worker",
                {"result": result},
            )
        await self._session.flush()
        return action

    async def reject(self, action_id: UUID, reason: str) -> PendingAction:
        action = await self.get_action(action_id, for_update=True)
        if action.status == "rejected":
            return action
        if action.status != "pending":
            raise InvalidActionTransitionError(f"cannot_reject_{action.status}")
        action.status = "rejected"
        action.reviewed_by = self._context.approver_id
        action.reviewed_at = self._clock()
        action.updated_at = action.reviewed_at
        action.rejection_reason = reason.strip()
        self._append_audit(
            action,
            "rejected",
            "approver",
            self._context.approver_id,
            {"reason": action.rejection_reason},
        )
        await self._session.flush()
        return action

    async def _execute(self, action: PendingAction) -> dict[str, Any]:
        if action.action_type == "cancel_order":
            return await self._execute_cancellation(action)
        if action.action_type == "refund":
            return await self._execute_refund(action)
        if action.action_type == "issue_coupon":
            return await self._execute_coupon(action)
        raise ActionExecutionError("UNKNOWN_ACTION_TYPE")

    async def _locked_order(self, action: PendingAction, order_number: str) -> Order:
        order = await self._session.scalar(
            select(Order)
            .where(
                Order.order_number == order_number,
                Order.tenant_id == action.tenant_id,
                Order.store_id == action.store_id,
                Order.customer_id == action.customer_id,
            )
            .with_for_update()
        )
        if order is None:
            raise ActionExecutionError("ORDER_NOT_FOUND")
        return order

    async def _execute_cancellation(self, action: PendingAction) -> dict[str, Any]:
        order_number = str(action.payload_json["order_number"])
        order = await self._locked_order(action, order_number)
        if order.status not in {"pending", "paid"}:
            raise ActionExecutionError(f"ORDER_STATUS_{order.status.upper()}")
        order.status = "cancelled"
        return {"order_number": order.order_number, "status": order.status}

    async def _execute_refund(self, action: PendingAction) -> dict[str, Any]:
        order_number = str(action.payload_json["order_number"])
        try:
            amount = normalize_money(Decimal(str(action.payload_json["amount"])))
        except (ValueError, InvalidOperation) as error:
            raise ActionExecutionError("AMOUNT_PRECISION_INVALID") from error
        order = await self._locked_order(action, order_number)
        if order.status not in {"shipped", "delivered"}:
            raise ActionExecutionError(f"ORDER_STATUS_{order.status.upper()}")
        if order.payment_status not in {"paid", "partially_refunded"}:
            raise ActionExecutionError("PAYMENT_NOT_REFUNDABLE")
        if not await refund_window_is_open(
            self._session, order, as_of=self._clock()
        ):
            raise ActionExecutionError("RETURN_WINDOW_EXPIRED")

        refunded = await self._session.scalar(
            select(func.coalesce(func.sum(RefundTransaction.amount), 0)).where(
                RefundTransaction.order_id == order.id,
                RefundTransaction.status == "succeeded",
            )
        )
        refunded_amount = Decimal(str(refunded or 0))
        if amount <= 0 or refunded_amount + amount > order.total_amount:
            raise ActionExecutionError("REFUND_AMOUNT_EXCEEDS_REMAINING")

        refund = RefundTransaction(
            id=uuid4(),
            tenant_id=action.tenant_id,
            store_id=action.store_id,
            customer_id=action.customer_id,
            pending_action_id=action.id,
            order_id=order.id,
            amount=amount,
            status="succeeded",
            provider_reference=f"refund-{action.id.hex}",
        )
        self._session.add(refund)
        total_refunded = refunded_amount + amount
        order.payment_status = (
            "refunded" if total_refunded == order.total_amount else "partially_refunded"
        )
        return {
            "refund_id": str(refund.id),
            "order_number": order.order_number,
            "amount": str(amount),
            "payment_status": order.payment_status,
        }

    async def _execute_coupon(self, action: PendingAction) -> dict[str, Any]:
        try:
            amount = normalize_money(Decimal(str(action.payload_json["amount"])))
        except (ValueError, InvalidOperation) as error:
            raise ActionExecutionError("AMOUNT_PRECISION_INVALID") from error
        if amount <= 0 or amount > MAX_COUPON_AMOUNT:
            raise ActionExecutionError("COUPON_AMOUNT_OUT_OF_RANGE")
        order_id: UUID | None = None
        order_number = action.payload_json.get("order_number")
        if order_number:
            order = await self._locked_order(action, str(order_number))
            order_id = order.id
        coupon = CouponGrant(
            id=uuid4(),
            tenant_id=action.tenant_id,
            store_id=action.store_id,
            customer_id=action.customer_id,
            pending_action_id=action.id,
            order_id=order_id,
            code=f"CARE-{action.id.hex[:10].upper()}",
            amount=amount,
            reason=str(action.payload_json["reason"]),
            status="active",
        )
        self._session.add(coupon)
        return {
            "coupon_id": str(coupon.id),
            "code": coupon.code,
            "amount": str(amount),
            "status": coupon.status,
        }

    def _append_audit(
        self,
        action: PendingAction,
        event_type: str,
        actor_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        action.audit_logs.append(
            ActionAuditLog(
                tenant_id=action.tenant_id,
                store_id=action.store_id,
                event_type=event_type,
                event_index=len(action.audit_logs) + 1,
                actor_type=actor_type,
                actor_id=actor_id,
                details_json=details or {},
            )
        )
