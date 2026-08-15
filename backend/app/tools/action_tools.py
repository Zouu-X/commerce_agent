from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.service import MAX_COUPON_AMOUNT, ActionRequestService
from app.models import PendingAction
from app.tools.context import ToolContext


class ActionToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestOrderCancellationArgs(ActionToolArguments):
    order_number: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=500)


class RequestRefundArgs(ActionToolArguments):
    order_number: str = Field(min_length=1, max_length=40)
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )
    reason: str = Field(min_length=1, max_length=500)


class RequestCouponArgs(ActionToolArguments):
    amount: Decimal = Field(
        gt=0,
        le=MAX_COUPON_AMOUNT,
        max_digits=12,
        decimal_places=2,
    )
    reason: str = Field(min_length=1, max_length=500)
    order_number: str | None = Field(default=None, min_length=1, max_length=40)


def _action_data(action: PendingAction) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_type": action.action_type,
        "status": action.status,
        "requires_approval": True,
        "payload": action.payload_json,
    }


class ActionToolHandlers:
    def __init__(self, session: AsyncSession, context: ToolContext) -> None:
        self._service = ActionRequestService(session, context)

    async def request_order_cancellation(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(RequestOrderCancellationArgs, raw_args)
        action = await self._service.request_cancellation(args.order_number, args.reason)
        return _action_data(action)

    async def request_refund(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(RequestRefundArgs, raw_args)
        action = await self._service.request_refund(args.order_number, args.amount, args.reason)
        return _action_data(action)

    async def request_coupon(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(RequestCouponArgs, raw_args)
        action = await self._service.request_coupon(
            args.amount,
            args.reason,
            order_number=args.order_number,
        )
        return _action_data(action)
