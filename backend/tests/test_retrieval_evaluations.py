from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.seed import stable_id
from app.evaluations.retrieval_dataset import RetrievalGoldCase, load_retrieval_gold_set
from app.evaluations.retrieval_runner import (
    RetrievalEvaluationService,
    calculate_retrieval_metrics,
    evaluate_retrieval_case,
)
from app.knowledge.service import KnowledgeSearchHit


def _hit(source: str, *, tenant_key: str = "aurora") -> KnowledgeSearchHit:
    source_key, version = source.split(":", maxsplit=1)
    return KnowledgeSearchHit(
        citation_id=f"{source}#chunk-1",
        document_id=stable_id(f"knowledge-document:{tenant_key}:{source_key}:{version}"),
        document_type="policy",
        title=source_key,
        version=version,
        content=f"evidence for {source}",
        score=0.1,
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_retrieval_gold_set_is_versioned_unique_and_broad() -> None:
    cases = load_retrieval_gold_set()

    assert len(cases) == 100
    assert len({case.case_id for case in cases}) == 100
    assert sum(case.split == "dev" for case in cases) == 75
    assert sum(case.split == "holdout" for case in cases) == 25
    assert sum("no_answer" in case.tags for case in cases) == 7
    assert sum("hard_negative" in case.tags for case in cases) >= 60
    multi_intent = [case for case in cases if "multi_intent" in case.tags]
    assert len(multi_intent) == 7
    assert all(len(case.expected_intents) == 2 for case in multi_intent)
    assert all(not case.expected_intents for case in cases if case not in multi_intent)
    assert any("historical" in case.tags for case in cases)
    assert any("prompt_injection" in case.tags for case in cases)
    assert any(case.tenant_key == "harbor" for case in cases)
    judged_sources = {source for case in cases for source in case.relevance}
    assert judged_sources == {
        "delay-compensation:v1",
        "delivery-failed:v1",
        "exchange-process:v1",
        "logistics-stale:v1",
        "no-reason-return:v0",
        "no-reason-return:v1",
        "order-cancellation:v1",
        "price-protection:v1",
        "product-care:v1",
        "quality-return:v1",
        "refund-timing:v1",
        "shipping-time:v1",
        "untrusted-content-example:v1",
        "warranty:v1",
    }


def test_retrieval_gold_set_can_select_a_frozen_split() -> None:
    dev = load_retrieval_gold_set(split="dev")
    holdout = load_retrieval_gold_set(split="holdout")

    assert len(dev) == 75
    assert len(holdout) == 25
    assert not {case.case_id for case in dev} & {case.case_id for case in holdout}


def test_case_evaluation_distinguishes_recall_from_hard_negative_noise() -> None:
    case = RetrievalGoldCase(
        case_id="graded",
        split="dev",
        query="退货和退款",
        relevance={"refund-timing:v1": 3, "quality-return:v1": 2},
        negative_sources=["no-reason-return:v1"],
        tags=["hard_negative"],
        annotation_reason="Two relevant sources with one known hard negative.",
    )

    result = evaluate_retrieval_case(
        case,
        [
            _hit("refund-timing:v1"),
            _hit("no-reason-return:v1"),
            _hit("quality-return:v1"),
        ],
        latency_ms=4,
    )

    assert result.recall_at_1 == 0.5
    assert result.recall_at_3 == 1
    assert result.precision_at_3 == pytest.approx(2 / 3)
    assert result.reciprocal_rank == 1
    assert result.ndcg_at_5 is not None and result.ndcg_at_5 < 1
    assert result.negative_hits == ["no-reason-return:v1"]
    assert result.retrieved_scores["refund-timing:v1#chunk-1"] == 0.1
    assert result.failures == ["hard_negative_hit"]
    assert result.passed is False


def test_no_answer_case_requires_an_empty_result() -> None:
    case = RetrievalGoldCase(
        case_id="empty",
        split="holdout",
        query="天气怎么样",
        relevance={},
        tags=["no_answer"],
        annotation_reason="Weather is outside the store knowledge base.",
    )

    correct = evaluate_retrieval_case(case, [], latency_ms=1)
    incorrect = evaluate_retrieval_case(
        case, [_hit("shipping-time:v1")], latency_ms=1
    )

    assert correct.no_answer_correct is True
    assert correct.passed is True
    assert incorrect.no_answer_correct is False
    assert incorrect.failures == ["no_answer_false_positive"]
    assert incorrect.passed is False


def test_metrics_keep_security_failures_separate_from_ranking_quality() -> None:
    answerable = RetrievalGoldCase(
        case_id="answerable",
        split="dev",
        query="什么时候发货",
        relevance={"shipping-time:v1": 3},
        tags=[],
        annotation_reason="Shipping policy answers the question.",
    )
    no_answer = RetrievalGoldCase(
        case_id="no-answer",
        split="holdout",
        query="天气",
        relevance={},
        tags=["no_answer"],
        annotation_reason="Weather is outside the corpus.",
    )
    results = [
        evaluate_retrieval_case(answerable, [_hit("shipping-time:v1")], latency_ms=2),
        evaluate_retrieval_case(no_answer, [], latency_ms=3),
    ]

    metrics = calculate_retrieval_metrics(results)

    assert metrics["recall_at_3"] == 1
    assert metrics["mrr"] == 1
    assert metrics["no_answer_accuracy"] == 1
    assert metrics["forbidden_source_hit_rate"] == 0
    assert metrics["scope_violation_rate"] == 0
    assert metrics["quality_gate_passed"] is True
    assert set(metrics["by_split"]) == {"dev", "holdout"}


@pytest.mark.anyio
async def test_full_gold_set_runs_through_real_knowledge_service(
    db_session: AsyncSession,
) -> None:
    run = await RetrievalEvaluationService(db_session).run(load_retrieval_gold_set())

    assert run.total_cases == 100
    assert len(run.cases) == 100
    assert run.metrics["counts"] == {
        "total": 100,
        "answerable": 93,
        "no_answer": 7,
        "hard_negative": sum(
            "hard_negative" in case.tags for case in load_retrieval_gold_set()
        ),
    }
    assert run.metrics["recall_at_3"] is not None
    assert run.metrics["mrr"] is not None
    assert run.metrics["ndcg_at_5"] is not None
    assert run.metrics["scope_violation_rate"] == 0
    assert run.metrics["forbidden_source_hit_rate"] == 0
    assert run.metrics["decomposition_intent_recall"] == 1
    assert run.metrics["decomposition_intent_precision"] == 1
    assert run.metrics["multi_intent_decomposition_rate"] == 1
    assert run.metrics["non_multi_intent_decomposition_rate"] == 0
    assert run.metrics["subquery_resolution_rate"] == 1
    retrieval = run.to_payload()["run"]["retrieval"]
    assert retrieval["embedding_provider"] == "deterministic_hash"
    assert retrieval["embedding_dimensions"] == 512
    assert retrieval["query_decomposition"]["max_subqueries"] == 3
