from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.commerce.seed import stable_id
from app.evaluations.retrieval_dataset import RetrievalGoldCase
from app.knowledge.decomposition import QueryDecomposition
from app.knowledge.service import (
    MIN_KEYWORD_RELEVANCE,
    MIN_VECTOR_KEYWORD_SUPPORT,
    RRF_K,
    KnowledgeSearchHit,
    KnowledgeSearchService,
)

DATASET_NAME = "commerce-rag-retrieval"
DATASET_VERSION = "retrieval-gold-v1.2-human-review"
RETRIEVAL_CONFIG_VERSION = "hybrid-rrf-v3-query-decomposition"


@dataclass(frozen=True)
class RetrievalCaseResult:
    case_id: str
    split: str
    query: str
    tenant_key: str
    decomposed: bool
    decomposition_reason: str
    subqueries: list[dict[str, Any]]
    unresolved_subquery_ids: list[str]
    expected_intents: list[str]
    retrieved_intents: list[str]
    missing_intents: list[str]
    unexpected_intents: list[str]
    intent_recall: float | None
    intent_precision: float | None
    latency_ms: int
    retrieved_citations: list[str]
    retrieved_sources: list[str]
    retrieved_scores: dict[str, float]
    expected_relevance: dict[str, int]
    missing_sources: list[str]
    negative_hits: list[str]
    forbidden_hits: list[str]
    scope_violations: list[str]
    content_requirements_met: bool
    recall_at_1: float | None
    recall_at_3: float | None
    recall_at_5: float | None
    precision_at_3: float | None
    reciprocal_rank: float | None
    ndcg_at_5: float | None
    no_answer_correct: bool | None
    passed: bool
    failures: list[str]
    tags: list[str]
    annotation_reason: str


@dataclass(frozen=True)
class RetrievalRunResult:
    started_at: datetime
    completed_at: datetime
    dataset_name: str
    dataset_version: str
    retrieval_config_version: str
    total_cases: int
    passed_cases: int
    metrics: dict[str, Any]
    cases: list[RetrievalCaseResult]
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    min_vector_similarity: float
    min_relative_relevance: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run": {
                "started_at": self.started_at.isoformat(),
                "completed_at": self.completed_at.isoformat(),
                "dataset": {"name": self.dataset_name, "version": self.dataset_version},
                "retrieval": {
                    "strategy": "hybrid_rrf",
                    "config_version": self.retrieval_config_version,
                    "embedding_provider": self.embedding_provider,
                    "embedding_model": self.embedding_model,
                    "embedding_dimensions": self.embedding_dimensions,
                    "rrf_k": RRF_K,
                    "keyword_weight": 2.0,
                    "vector_weight": 1.0,
                    "thresholds": {
                        "min_keyword_relevance": MIN_KEYWORD_RELEVANCE,
                        "min_vector_similarity": self.min_vector_similarity,
                        "min_vector_keyword_support": MIN_VECTOR_KEYWORD_SUPPORT,
                        "min_relative_relevance": self.min_relative_relevance,
                    },
                    "query_decomposition": {
                        "strategy": "deterministic_commerce_intent_planner",
                        "max_subqueries": 3,
                        "merge": "round_robin_with_per_subquery_top_1_guarantee",
                    },
                },
                "total_cases": self.total_cases,
                "passed_cases": self.passed_cases,
            },
            "metrics": self.metrics,
            "cases": [asdict(case) for case in self.cases],
        }


class RetrievalEvaluationService:
    def __init__(self, session: AsyncSession, *, limit: int = 5) -> None:
        self._service = KnowledgeSearchService(session)
        self._limit = limit

    async def run(self, cases: list[RetrievalGoldCase]) -> RetrievalRunResult:
        started_at = datetime.now(UTC)
        results: list[RetrievalCaseResult] = []
        for case in cases:
            context = CommerceContext(
                tenant_id=stable_id(f"tenant:{case.tenant_key}"),
                store_id=stable_id(f"store:{case.tenant_key}"),
                customer_id=stable_id(f"customer:{case.tenant_key}:0"),
            )
            started = perf_counter()
            search_result = await self._service.search_with_explanation(
                context,
                case.query,
                document_type=case.document_type,
                limit=self._limit,
                as_of=case.as_of,
            )
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            results.append(
                evaluate_retrieval_case(
                    case,
                    search_result.hits,
                    latency_ms=latency_ms,
                    decomposition=search_result.decomposition,
                    unresolved_subquery_ids=search_result.unresolved_subquery_ids,
                )
            )
        completed_at = datetime.now(UTC)
        return RetrievalRunResult(
            started_at=started_at,
            completed_at=completed_at,
            dataset_name=DATASET_NAME,
            dataset_version=DATASET_VERSION,
            retrieval_config_version=RETRIEVAL_CONFIG_VERSION,
            total_cases=len(results),
            passed_cases=sum(result.passed for result in results),
            metrics=calculate_retrieval_metrics(results),
            cases=results,
            embedding_provider=self._service.embedding_provider.provider_name,
            embedding_model=self._service.embedding_provider.model_name,
            embedding_dimensions=self._service.embedding_provider.dimensions,
            min_vector_similarity=self._service.min_vector_similarity,
            min_relative_relevance=self._service.min_relative_relevance,
        )


def evaluate_retrieval_case(
    case: RetrievalGoldCase,
    hits: list[KnowledgeSearchHit],
    *,
    latency_ms: int,
    decomposition: QueryDecomposition | None = None,
    unresolved_subquery_ids: tuple[str, ...] = (),
) -> RetrievalCaseResult:
    retrieved_citations = [hit.citation_id for hit in hits]
    retrieved_sources = _unique([_source_version(hit.citation_id) for hit in hits])
    retrieved_scores = {hit.citation_id: hit.score for hit in hits}
    retrieved_intents = (
        [subquery.intent for subquery in decomposition.subqueries]
        if decomposition is not None and decomposition.decomposed
        else []
    )
    expected_intents = set(case.expected_intents)
    missing_intents = sorted(expected_intents - set(retrieved_intents))
    unexpected_intents = sorted(set(retrieved_intents) - expected_intents)
    intent_recall = (
        len(expected_intents & set(retrieved_intents)) / len(expected_intents)
        if expected_intents
        else None
    )
    intent_precision = (
        len(expected_intents & set(retrieved_intents)) / len(retrieved_intents)
        if expected_intents and retrieved_intents
        else (0.0 if expected_intents else None)
    )
    relevant_sources = set(case.relevance)
    missing_sources = sorted(relevant_sources - set(retrieved_sources[:3]))
    negative_hits = sorted(set(case.negative_sources) & set(retrieved_sources))
    forbidden_hits = sorted(set(case.forbidden_sources) & set(retrieved_sources))
    scope_violations = sorted(
        hit.citation_id
        for hit in hits
        if hit.document_id
        != stable_id(
            f"knowledge-document:{case.tenant_key}:{_source_version(hit.citation_id)}"
        )
    )
    combined_content = "\n".join(hit.content for hit in hits)
    content_requirements_met = all(text in combined_content for text in case.must_contain)

    if relevant_sources:
        recall_at_1 = _recall_at_k(retrieved_sources, relevant_sources, 1)
        recall_at_3 = _recall_at_k(retrieved_sources, relevant_sources, 3)
        recall_at_5 = _recall_at_k(retrieved_sources, relevant_sources, 5)
        precision_at_3 = _precision_at_k(retrieved_sources, relevant_sources, 3)
        reciprocal_rank = _reciprocal_rank(retrieved_sources, relevant_sources)
        ndcg_at_5 = _ndcg_at_k(retrieved_sources, case.relevance, 5)
        no_answer_correct = None
        passed = (
            recall_at_3 == 1.0
            and reciprocal_rank == 1.0
            and not negative_hits
            and not forbidden_hits
            and not scope_violations
            and content_requirements_met
        )
        failures = []
        if recall_at_3 < 1.0:
            failures.append("missing_relevant_top_3")
        if reciprocal_rank < 1.0:
            failures.append("relevant_source_not_ranked_first")
        if negative_hits:
            failures.append("hard_negative_hit")
    else:
        recall_at_1 = None
        recall_at_3 = None
        recall_at_5 = None
        precision_at_3 = None
        reciprocal_rank = None
        ndcg_at_5 = None
        no_answer_correct = not retrieved_sources
        passed = no_answer_correct and not forbidden_hits and not scope_violations
        failures = [] if no_answer_correct else ["no_answer_false_positive"]

    if forbidden_hits:
        failures.append("forbidden_source_hit")
    if scope_violations:
        failures.append("scope_violation")
    if not content_requirements_met:
        failures.append("content_requirement_missing")
    if missing_intents:
        failures.append("missing_decomposition_intent")
    if unexpected_intents:
        failures.append("unexpected_decomposition_intent")
    if not case.expected_intents and decomposition is not None and decomposition.decomposed:
        failures.append("unexpected_decomposition")
    if missing_intents or unexpected_intents or "unexpected_decomposition" in failures:
        passed = False

    return RetrievalCaseResult(
        case_id=case.case_id,
        split=case.split,
        query=case.query,
        tenant_key=case.tenant_key,
        decomposed=decomposition.decomposed if decomposition is not None else False,
        decomposition_reason=decomposition.reason if decomposition is not None else "",
        subqueries=(
            [asdict(subquery) for subquery in decomposition.subqueries]
            if decomposition is not None
            else []
        ),
        unresolved_subquery_ids=list(unresolved_subquery_ids),
        expected_intents=case.expected_intents,
        retrieved_intents=retrieved_intents,
        missing_intents=missing_intents,
        unexpected_intents=unexpected_intents,
        intent_recall=intent_recall,
        intent_precision=intent_precision,
        latency_ms=latency_ms,
        retrieved_citations=retrieved_citations,
        retrieved_sources=retrieved_sources,
        retrieved_scores=retrieved_scores,
        expected_relevance=case.relevance,
        missing_sources=missing_sources,
        negative_hits=negative_hits,
        forbidden_hits=forbidden_hits,
        scope_violations=scope_violations,
        content_requirements_met=content_requirements_met,
        recall_at_1=recall_at_1,
        recall_at_3=recall_at_3,
        recall_at_5=recall_at_5,
        precision_at_3=precision_at_3,
        reciprocal_rank=reciprocal_rank,
        ndcg_at_5=ndcg_at_5,
        no_answer_correct=no_answer_correct,
        passed=passed,
        failures=failures,
        tags=case.tags,
        annotation_reason=case.annotation_reason,
    )


def calculate_retrieval_metrics(
    results: list[RetrievalCaseResult], *, include_splits: bool = True
) -> dict[str, Any]:
    answerable = [result for result in results if result.recall_at_1 is not None]
    no_answer = [result for result in results if result.no_answer_correct is not None]
    hard_negative = [result for result in results if "hard_negative" in result.tags]
    multi_intent = [result for result in results if "multi_intent" in result.tags]
    non_multi_intent = [result for result in results if "multi_intent" not in result.tags]
    decomposed = [result for result in results if result.decomposed]
    judged_intents = [result for result in results if result.intent_recall is not None]
    subquery_count = sum(len(result.subqueries) for result in decomposed)
    unresolved_subquery_count = sum(
        len(result.unresolved_subquery_ids) for result in decomposed
    )
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)

    metrics: dict[str, Any] = {
        "pass_rate": _ratio(sum(result.passed for result in results), len(results)),
        "recall_at_1": _mean_metric(answerable, "recall_at_1"),
        "recall_at_3": _mean_metric(answerable, "recall_at_3"),
        "recall_at_5": _mean_metric(answerable, "recall_at_5"),
        "precision_at_3": _mean_metric(answerable, "precision_at_3"),
        "mrr": _mean_metric(answerable, "reciprocal_rank"),
        "ndcg_at_5": _mean_metric(answerable, "ndcg_at_5"),
        "no_answer_accuracy": _ratio(
            sum(result.no_answer_correct is True for result in no_answer), len(no_answer)
        ),
        "no_answer_false_positive_rate": _ratio(
            sum(result.no_answer_correct is False for result in no_answer), len(no_answer)
        ),
        "hard_negative_case_hit_rate": _ratio(
            sum(bool(result.negative_hits) for result in hard_negative), len(hard_negative)
        ),
        "decomposition_rate": _ratio(len(decomposed), len(results)),
        "multi_intent_decomposition_rate": _ratio(
            sum(result.decomposed for result in multi_intent), len(multi_intent)
        ),
        "non_multi_intent_decomposition_rate": _ratio(
            sum(result.decomposed for result in non_multi_intent), len(non_multi_intent)
        ),
        "subquery_resolution_rate": _ratio(
            subquery_count - unresolved_subquery_count, subquery_count
        ),
        "decomposition_intent_recall": _mean_metric(judged_intents, "intent_recall"),
        "decomposition_intent_precision": _mean_metric(
            judged_intents, "intent_precision"
        ),
        "forbidden_source_hit_rate": _ratio(
            sum(bool(result.forbidden_hits) for result in results), len(results)
        ),
        "scope_violation_rate": _ratio(
            sum(bool(result.scope_violations) for result in results), len(results)
        ),
        "content_requirement_failure_rate": _ratio(
            sum(not result.content_requirements_met for result in results), len(results)
        ),
        "p95_latency_ms": latencies[p95_index] if latencies else 0,
        "counts": {
            "total": len(results),
            "answerable": len(answerable),
            "no_answer": len(no_answer),
            "hard_negative": len(hard_negative),
        },
    }
    metrics["targets"] = {
        "recall_at_3_gte_0_95": _meets(metrics["recall_at_3"], 0.95),
        "mrr_gte_0_90": _meets(metrics["mrr"], 0.90),
        "ndcg_at_5_gte_0_95": _meets(metrics["ndcg_at_5"], 0.95),
        "no_answer_false_positive_rate_lte_0_05": _at_most(
            metrics["no_answer_false_positive_rate"], 0.05
        ),
        "forbidden_source_hit_rate_eq_0": metrics["forbidden_source_hit_rate"] == 0,
        "scope_violation_rate_eq_0": metrics["scope_violation_rate"] == 0,
        "non_multi_intent_decomposition_rate_eq_0": (
            metrics["non_multi_intent_decomposition_rate"] == 0
        ),
    }
    if multi_intent:
        metrics["targets"].update(
            {
                "decomposition_intent_recall_eq_1": (
                    metrics["decomposition_intent_recall"] == 1
                ),
                "decomposition_intent_precision_eq_1": (
                    metrics["decomposition_intent_precision"] == 1
                ),
                "subquery_resolution_rate_gte_0_90": _meets(
                    metrics["subquery_resolution_rate"], 0.90
                ),
            }
        )
    metrics["quality_gate_passed"] = all(metrics["targets"].values())
    if include_splits:
        split_names = sorted({result.split for result in results})
        if len(split_names) > 1:
            metrics["by_split"] = {
                split: calculate_retrieval_metrics(
                    [result for result in results if result.split == split],
                    include_splits=False,
                )
                for split in split_names
            }
    return metrics


def _source_version(citation_id: str) -> str:
    return citation_id.partition("#chunk-")[0]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def _precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    return len(set(top_k) & relevant) / len(top_k)


def _reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for rank, source in enumerate(retrieved, start=1):
        if source in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(retrieved: list[str], relevance: dict[str, int], k: int) -> float:
    gains = [relevance.get(source, 0) for source in retrieved[:k]]
    dcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(gains, 1))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def _mean_metric(results: list[RetrievalCaseResult], name: str) -> float | None:
    values = [cast(float | None, getattr(result, name)) for result in results]
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _meets(value: float | None, target: float) -> bool:
    return value is not None and value >= target


def _at_most(value: float | None, target: float) -> bool:
    return value is not None and value <= target
