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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="valid_trace_status",
        ),
        Index(
            "ix_agent_traces_scope_started",
            "tenant_id",
            "store_id",
            "started_at",
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
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="running")
    model_provider: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 8), default=Decimal("0")
    )
    first_model_response_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    final_response_preview: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(160))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[TraceEvent]] = relationship(
        back_populates="trace",
        cascade="all, delete-orphan",
        order_by="TraceEvent.event_index",
    )


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("trace_id", "event_index"),
        CheckConstraint("event_index > 0", name="positive_event_index"),
        Index("ix_trace_events_trace_created", "trace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_traces.id", ondelete="CASCADE"), index=True
    )
    event_index: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    trace: Mapped[AgentTrace] = relationship(back_populates="events")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="valid_evaluation_status",
        ),
        Index("ix_evaluation_runs_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(24), default="running")
    dataset_name: Mapped[str] = mapped_column(String(120))
    dataset_version: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cases: Mapped[list[EvaluationCaseResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvaluationCaseResult.case_index",
    )


class EvaluationCaseResult(Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id"),
        UniqueConstraint(
            "run_id", "case_index", name="uq_evaluation_case_results_run_case_index"
        ),
        CheckConstraint("case_index > 0", name="positive_case_index"),
        Index("ix_evaluation_case_results_run_passed", "run_id", "passed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_traces.id", ondelete="SET NULL"), index=True
    )
    case_index: Mapped[int] = mapped_column(Integer)
    case_id: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80))
    input_text: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 8), default=Decimal("0")
    )
    actual_tools_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    checks_json: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    failures_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_preview: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[EvaluationRun] = relationship(back_populates="cases")
    trace: Mapped[AgentTrace | None] = relationship()
