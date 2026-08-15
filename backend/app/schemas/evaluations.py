from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvaluationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationCaseResultRead(EvaluationSchema):
    case_index: int
    case_id: str
    category: str
    trace_id: UUID | None
    trace_tenant_id: UUID | None
    trace_store_id: UUID | None
    input: str
    passed: bool
    latency_ms: int
    estimated_cost_usd: Decimal
    actual_tools: list[str]
    checks: dict[str, bool]
    failures: list[str]
    evidence: dict[str, Any]
    response_preview: str


class EvaluationRunSummaryRead(EvaluationSchema):
    id: UUID
    status: str
    dataset_name: str
    dataset_version: str
    provider: str
    model_name: str
    prompt_version: str
    total_cases: int
    passed_cases: int
    metrics: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None


class EvaluationRunRead(EvaluationRunSummaryRead):
    cases: list[EvaluationCaseResultRead]
