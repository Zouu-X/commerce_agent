"""Add approval-gated write actions and audit records.

Revision ID: 20260815_0004
Revises: 20260714_0003
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0004"
down_revision: str | None = "20260714_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=160), nullable=False),
        sa.Column("reviewed_by", sa.String(length=160), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "action_type IN ('refund', 'issue_coupon', 'cancel_order')",
            name=op.f("ck_pending_actions_valid_action_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executing', 'succeeded', 'failed')",
            name=op.f("ck_pending_actions_valid_action_status"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_pending_actions_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_pending_actions_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_pending_actions_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_pending_actions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_actions")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name=op.f("uq_pending_actions_tenant_id"),
        ),
    )
    for column in ("tenant_id", "store_id", "customer_id", "conversation_id", "trace_id"):
        op.create_index(op.f(f"ix_pending_actions_{column}"), "pending_actions", [column])
    op.create_index(
        "ix_pending_actions_scope_status_created",
        "pending_actions",
        ["tenant_id", "store_id", "status", "created_at"],
    )

    op.create_table(
        "action_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["pending_actions.id"],
            name=op.f("fk_action_audit_logs_action_id_pending_actions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_action_audit_logs_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_action_audit_logs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_audit_logs")),
        sa.UniqueConstraint(
            "action_id",
            "event_index",
            name=op.f("uq_action_audit_logs_action_id"),
        ),
    )
    op.create_index(op.f("ix_action_audit_logs_action_id"), "action_audit_logs", ["action_id"])
    op.create_index(op.f("ix_action_audit_logs_store_id"), "action_audit_logs", ["store_id"])
    op.create_index(op.f("ix_action_audit_logs_tenant_id"), "action_audit_logs", ["tenant_id"])
    op.create_index(
        "ix_action_audit_logs_scope_created",
        "action_audit_logs",
        ["tenant_id", "store_id", "created_at"],
    )

    op.create_table(
        "refund_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("pending_action_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "amount > 0",
            name=op.f("ck_refund_transactions_refund_amount_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_refund_transactions_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_refund_transactions_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_action_id"],
            ["pending_actions.id"],
            name=op.f("fk_refund_transactions_pending_action_id_pending_actions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_refund_transactions_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_refund_transactions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_transactions")),
        sa.UniqueConstraint(
            "pending_action_id",
            name=op.f("uq_refund_transactions_pending_action_id"),
        ),
        sa.UniqueConstraint(
            "provider_reference",
            name=op.f("uq_refund_transactions_provider_reference"),
        ),
    )
    for column in ("tenant_id", "store_id", "customer_id", "order_id"):
        op.create_index(op.f(f"ix_refund_transactions_{column}"), "refund_transactions", [column])
    op.create_index(
        "ix_refund_transactions_order_created",
        "refund_transactions",
        ["order_id", "created_at"],
    )

    op.create_table(
        "coupon_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("pending_action_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_coupon_grants_coupon_amount_positive")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_coupon_grants_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_coupon_grants_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_action_id"],
            ["pending_actions.id"],
            name=op.f("fk_coupon_grants_pending_action_id_pending_actions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_coupon_grants_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_coupon_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupon_grants")),
        sa.UniqueConstraint("code", name=op.f("uq_coupon_grants_code")),
        sa.UniqueConstraint("pending_action_id", name=op.f("uq_coupon_grants_pending_action_id")),
    )
    for column in ("tenant_id", "store_id", "customer_id", "order_id"):
        op.create_index(op.f(f"ix_coupon_grants_{column}"), "coupon_grants", [column])
    op.create_index(
        "ix_coupon_grants_customer_created",
        "coupon_grants",
        ["customer_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("coupon_grants")
    op.drop_table("refund_transactions")
    op.drop_table("action_audit_logs")
    op.drop_table("pending_actions")
