from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.provider import ModelProvider
from app.api.dependencies import get_model_provider
from app.core.config import get_settings
from app.db.session import get_db_session
from app.evaluations.runner import EvaluationService, EvaluationSettings
from app.models import EvaluationCaseResult, EvaluationRun
from app.schemas.evaluations import (
    EvaluationCaseResultRead,
    EvaluationRunRead,
    EvaluationRunSummaryRead,
)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ProviderDependency = Annotated[ModelProvider, Depends(get_model_provider)]


def case_response(result: EvaluationCaseResult) -> EvaluationCaseResultRead:
    return EvaluationCaseResultRead(
        case_index=result.case_index,
        case_id=result.case_id,
        category=result.category,
        trace_id=result.trace_id,
        trace_tenant_id=result.trace.tenant_id if result.trace else None,
        trace_store_id=result.trace.store_id if result.trace else None,
        input=result.input_text,
        passed=result.passed,
        latency_ms=result.latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
        actual_tools=result.actual_tools_json,
        checks=result.checks_json,
        failures=result.failures_json,
        evidence=result.evidence_json,
        response_preview=result.response_preview,
    )


def summary_response(run: EvaluationRun) -> EvaluationRunSummaryRead:
    return EvaluationRunSummaryRead(
        id=run.id,
        status=run.status,
        dataset_name=run.dataset_name,
        dataset_version=run.dataset_version,
        provider=run.provider,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        total_cases=run.total_cases,
        passed_cases=run.passed_cases,
        metrics=run.metrics_json,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.post(
    "/runs",
    response_model=EvaluationRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    session: SessionDependency,
    provider: ProviderDependency,
) -> EvaluationRunRead:
    settings = get_settings()
    run = await EvaluationService(
        session,
        provider,
        EvaluationSettings(
            provider_name=settings.model_provider,
            model_name=settings.model_name,
            input_cost_per_million=settings.model_input_cost_per_million,
            output_cost_per_million=settings.model_output_cost_per_million,
        ),
    ).run()
    summary = summary_response(run)
    return EvaluationRunRead(
        **summary.model_dump(),
        cases=[case_response(result) for result in run.cases],
    )


@router.get("/runs", response_model=list[EvaluationRunSummaryRead])
async def list_runs(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[EvaluationRunSummaryRead]:
    runs = list(
        await session.scalars(
            select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
        )
    )
    return [summary_response(run) for run in runs]


@router.get("/runs/{run_id}", response_model=EvaluationRunRead)
async def get_run(run_id: UUID, session: SessionDependency) -> EvaluationRunRead:
    run = await session.scalar(
        select(EvaluationRun)
        .options(
            selectinload(EvaluationRun.cases).selectinload(
                EvaluationCaseResult.trace
            )
        )
        .where(EvaluationRun.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation_run_not_found")
    summary = summary_response(run)
    return EvaluationRunRead(
        **summary.model_dump(),
        cases=[case_response(result) for result in run.cases],
    )
