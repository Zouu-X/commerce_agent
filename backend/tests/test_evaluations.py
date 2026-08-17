import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ModelProviderError
from app.agent.provider import MockCommerceProvider
from app.agent.types import ModelResponse, ProviderMessage, ToolSpec
from app.api.evaluations import case_response
from app.evaluations.dataset import EvaluationCase, load_dataset
from app.evaluations.runner import EvaluationService, EvaluationSettings, evaluate_case
from app.models import PendingAction


class FailingProvider:
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        raise ModelProviderError("demo_provider_failure")


def test_milestone_five_dataset_has_unique_broad_coverage() -> None:
    cases = load_dataset()

    assert len(cases) == 60
    assert len({case.case_id for case in cases}) == 60
    assert {case.category for case in cases} == {
        "product",
        "order",
        "logistics",
        "after_sale",
        "knowledge",
        "action",
        "security",
    }
    assert any("cross_tenant" in case.tags for case in cases)
    assert any("prompt_injection" in case.tags for case in cases)
    assert any(case.requires_pending_action for case in cases)
    assert all(
        case.expected_citations
        for case in cases
        if case.requires_citation
    )


@pytest.mark.anyio
async def test_full_mock_evaluation_produces_structured_metrics(
    db_session: AsyncSession,
) -> None:
    run = await EvaluationService(
        db_session,
        MockCommerceProvider(),
        EvaluationSettings(provider_name="mock", model_name="mock-commerce-agent"),
    ).run()

    assert run.status == "succeeded"
    assert run.total_cases == 60
    assert run.passed_cases == 58, [
        result.case_id for result in run.cases if not result.passed
    ]
    assert len(run.cases) == 60
    assert all(result.trace_id is not None for result in run.cases)
    assert all(result.trace is not None for result in run.cases)
    first_case = case_response(run.cases[0])
    assert first_case.trace_tenant_id is not None
    assert first_case.trace_store_id is not None
    assert run.metrics_json["necessary_tool_recall"] >= 0.9
    assert run.metrics_json["tool_parameter_validity"] >= 0.95
    assert run.metrics_json["citation_coverage"] == 1
    assert run.metrics_json["citation_correctness"] == 0.8333
    assert run.metrics_json["quality_gate_passed"] is False
    assert run.metrics_json["cross_scope_leakage_rate"] == 0
    assert run.metrics_json["unapproved_write_execution_rate"] == 0
    citation_failures = {
        result.case_id: result.evidence_json["unexpected_citations"]
        for result in run.cases
        if not result.checks_json["citation"]
    }
    assert citation_failures == {
        "knowledge_006": ["logistics-stale:v1#chunk-1"],
        "knowledge_007": ["quality-return:v1#chunk-1"],
    }
    assert list(await db_session.scalars(select(PendingAction))) == []


def test_citation_check_explains_present_but_wrong_evidence() -> None:
    case = EvaluationCase(
        case_id="wrong-citation",
        category="knowledge",
        input="退款多久到账？",
        requires_citation=True,
        expected_citations=["refund-timing:v1#chunk-1"],
    )

    evaluation = evaluate_case(
        case,
        "支持七天无理由退货。[no-reason-return:v1#chunk-1]",
        [],
    )

    assert evaluation.checks["citation_presence"] is True
    assert evaluation.checks["citation_correctness"] is False
    assert evaluation.checks["citation"] is False
    assert evaluation.evidence["actual_citations"] == [
        "no-reason-return:v1#chunk-1"
    ]
    assert evaluation.evidence["missing_citations"] == [
        "refund-timing:v1#chunk-1"
    ]
    assert evaluation.evidence["unexpected_citations"] == [
        "no-reason-return:v1#chunk-1"
    ]


def test_citation_check_rejects_correct_citation_mixed_with_wrong_evidence() -> None:
    case = EvaluationCase(
        case_id="mixed-citations",
        category="knowledge",
        input="退款多久到账？",
        requires_citation=True,
        expected_citations=["refund-timing:v1#chunk-1"],
    )

    evaluation = evaluate_case(
        case,
        "退款通常需要 1 至 3 个工作日。"
        "[refund-timing:v1#chunk-1] [no-reason-return:v1#chunk-1]",
        [],
    )

    assert evaluation.checks["citation_presence"] is True
    assert evaluation.checks["citation_correctness"] is False
    assert evaluation.evidence["missing_citations"] == []
    assert evaluation.evidence["unexpected_citations"] == [
        "no-reason-return:v1#chunk-1"
    ]


@pytest.mark.anyio
async def test_quality_gate_ignores_targets_not_applicable_to_a_subset(
    db_session: AsyncSession,
) -> None:
    case = EvaluationCase(
        case_id="product-subset",
        category="product",
        input="推荐有库存的降噪耳机",
        expected_tools=["search_products"],
        must_contain=["降噪蓝牙耳机"],
    )
    run = await EvaluationService(
        db_session,
        MockCommerceProvider(),
        EvaluationSettings(provider_name="mock", model_name="mock-commerce-agent"),
    ).run([case])

    assert run.status == "succeeded"
    assert run.passed_cases == 1
    assert run.metrics_json["quality_gate_passed"] is True
    assert run.metrics_json["targets"]["citation_coverage_eq_1"] is None
    assert "citation_coverage_eq_1" in run.metrics_json["not_applicable_targets"]


@pytest.mark.anyio
async def test_execution_failure_marks_run_failed_and_links_failed_trace(
    db_session: AsyncSession,
) -> None:
    case = EvaluationCase(
        case_id="provider-failure",
        category="product",
        input="推荐耳机",
        expected_tools=["search_products"],
    )
    run = await EvaluationService(
        db_session,
        FailingProvider(),
        EvaluationSettings(provider_name="failing", model_name="demo-failure"),
    ).run([case])

    result = run.cases[0]
    assert run.status == "failed"
    assert run.error_message == "1 case(s) failed during execution"
    assert run.metrics_json["execution_success_rate"] == 0
    assert run.metrics_json["quality_gate_passed"] is False
    assert result.trace_id is not None
    assert result.trace is not None
    assert result.trace.status == "failed"
    assert result.checks_json["execution"] is False
    assert result.evidence_json["failed_trace_id"] == str(result.trace_id)
