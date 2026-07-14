"""Add Milestone 2 conversation and message persistence.

Revision ID: 20260713_0002
Revises: 20260712_0001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0002"
down_revision: str | None = "20260712_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_conversations_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_conversations_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_customer_id"), "conversations", ["customer_id"])
    op.create_index(op.f("ix_conversations_store_id"), "conversations", ["store_id"])
    op.create_index(op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"])
    op.create_index(
        "ix_conversations_context_created",
        "conversations",
        ["tenant_id", "store_id", "customer_id", "created_at"],
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=160), nullable=True),
        sa.Column("tool_name", sa.String(length=80), nullable=True),
        sa.Column("tool_calls_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool')", name=op.f("ck_messages_valid_role")
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_messages_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name=op.f("uq_messages_conversation_id")
        ),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
