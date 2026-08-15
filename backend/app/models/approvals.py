from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PendingAction(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('refund', 'issue_coupon', 'cancel_order')",
            name="valid_action_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executing', 'succeeded', 'failed')",
            name="valid_action_status",
        ),
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index(
            "ix_pending_actions_scope_status_created",
            "tenant_id",
            "store_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(160), default="agent")
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failure_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    audit_logs: Mapped[list[ActionAuditLog]] = relationship(
        back_populates="action",
        cascade="all, delete-orphan",
        order_by="ActionAuditLog.event_index",
    )


class ActionAuditLog(Base):
    __tablename__ = "action_audit_logs"
    __table_args__ = (
        UniqueConstraint("action_id", "event_index"),
        Index("ix_action_audit_logs_scope_created", "tenant_id", "store_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pending_actions.id", ondelete="CASCADE"), index=True
    )
    event_index: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(160))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    action: Mapped[PendingAction] = relationship(back_populates="audit_logs")


class RefundTransaction(Base):
    __tablename__ = "refund_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="refund_amount_positive"),
        UniqueConstraint("pending_action_id"),
        Index("ix_refund_transactions_order_created", "order_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    pending_action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pending_actions.id", ondelete="RESTRICT")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="succeeded")
    provider_reference: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CouponGrant(Base):
    __tablename__ = "coupon_grants"
    __table_args__ = (
        CheckConstraint("amount > 0", name="coupon_amount_positive"),
        UniqueConstraint("pending_action_id"),
        Index("ix_coupon_grants_customer_created", "customer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    pending_action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pending_actions.id", ondelete="RESTRICT")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
