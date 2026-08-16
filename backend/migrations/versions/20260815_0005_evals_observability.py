"""Add Agent traces and persisted evaluation results.

Revision ID: 20260815_0005
Revises: 20260815_0004
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0005"
down_revision: str | None = "20260815_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("model_provider", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column("first_model_response_ms", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("final_response_preview", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_agent_traces_valid_trace_status"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"],
            name=op.f("fk_agent_traces_conversation_id_conversations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"],
            name=op.f("fk_agent_traces_customer_id_customers"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"],
            name=op.f("fk_agent_traces_store_id_stores"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_agent_traces_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_traces")),
    )
    for column in ("tenant_id", "store_id", "customer_id", "conversation_id"):
        op.create_index(op.f(f"ix_agent_traces_{column}"), "agent_traces", [column])
    op.create_index(
        "ix_agent_traces_scope_started",
        "agent_traces",
        ["tenant_id", "store_id", "started_at"],
    )

    op.create_table(
        "trace_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=14, scale=8), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_index > 0", name=op.f("ck_trace_events_positive_event_index")
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["agent_traces.id"],
            name=op.f("fk_trace_events_trace_id_agent_traces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_events")),
        sa.UniqueConstraint("trace_id", "event_index", name=op.f("uq_trace_events_trace_id")),
    )
    op.create_index(op.f("ix_trace_events_trace_id"), "trace_events", ["trace_id"])
    op.create_index(
        "ix_trace_events_trace_created", "trace_events", ["trace_id", "created_at"]
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("dataset_name", sa.String(length=120), nullable=False),
        sa.Column("dataset_version", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_evaluation_runs_valid_evaluation_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index("ix_evaluation_runs_started", "evaluation_runs", ["started_at"])

    op.create_table(
        "evaluation_case_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column("case_index", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column("actual_tools_json", sa.JSON(), nullable=False),
        sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("failures_json", sa.JSON(), nullable=False),
        sa.Column("response_preview", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "case_index > 0",
            name=op.f("ck_evaluation_case_results_positive_case_index"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["evaluation_runs.id"],
            name=op.f("fk_evaluation_case_results_run_id_evaluation_runs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["agent_traces.id"],
            name=op.f("fk_evaluation_case_results_trace_id_agent_traces"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_case_results")),
        sa.UniqueConstraint("run_id", "case_id", name=op.f("uq_evaluation_case_results_run_id")),
        sa.UniqueConstraint(
            "run_id", "case_index", name="uq_evaluation_case_results_run_case_index"
        ),
    )
    op.create_index(
        op.f("ix_evaluation_case_results_run_id"), "evaluation_case_results", ["run_id"]
    )
    op.create_index(
        op.f("ix_evaluation_case_results_trace_id"), "evaluation_case_results", ["trace_id"]
    )
    op.create_index(
        "ix_evaluation_case_results_run_passed",
        "evaluation_case_results",
        ["run_id", "passed"],
    )


def downgrade() -> None:
    op.drop_table("evaluation_case_results")
    op.drop_table("evaluation_runs")
    op.drop_table("trace_events")
    op.drop_table("agent_traces")
