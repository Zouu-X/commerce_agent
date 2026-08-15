from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.knowledge.embeddings import embed_text, lexical_tokens
from app.models import KnowledgeChunk, KnowledgeDocument

RRF_K = 60
MIN_KEYWORD_RELEVANCE = 0.12
MIN_VECTOR_SIMILARITY = 0.35
MIN_VECTOR_KEYWORD_SUPPORT = 0.05
MIN_RELATIVE_RELEVANCE = 0.9


@dataclass(frozen=True)
class KnowledgeSearchHit:
    citation_id: str
    document_id: uuid.UUID
    document_type: str
    title: str
    version: str
    content: str
    score: float
    effective_from: datetime


class KnowledgeSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        context: CommerceContext,
        query: str,
        *,
        document_type: str | None = None,
        limit: int = 5,
        as_of: datetime | None = None,
    ) -> list[KnowledgeSearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        effective_at = as_of or datetime.now(UTC)
        candidate_limit = max(20, limit * 4)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            ranked_ids = await self._postgres_rankings(
                context,
                normalized_query,
                document_type=document_type,
                as_of=effective_at,
                candidate_limit=candidate_limit,
            )
        else:
            ranked_ids = await self._python_rankings(
                context,
                normalized_query,
                document_type=document_type,
                as_of=effective_at,
                candidate_limit=candidate_limit,
            )
        if not ranked_ids:
            return []

        rows = (
            await self._session.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .where(
                    KnowledgeChunk.id.in_(
                        [uuid.UUID(chunk_id) for chunk_id in ranked_ids]
                    )
                )
            )
        ).all()
        by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}
        query_tokens = set(lexical_tokens(normalized_query))
        query_embedding = embed_text(normalized_query)
        relevance_by_id = {
            chunk_id: self._absolute_relevance(
                query_tokens,
                query_embedding,
                chunk,
            )
            for chunk_id, (chunk, _document) in by_id.items()
        }
        best_keyword_relevance = max(
            (scores[0] for scores in relevance_by_id.values()),
            default=0.0,
        )
        best_vector_similarity = max(
            (scores[1] for scores in relevance_by_id.values()),
            default=0.0,
        )
        best_concept_coverage = max(
            (scores[3] for scores in relevance_by_id.values()),
            default=0.0,
        )
        hits: list[KnowledgeSearchHit] = []
        for chunk_id, score in ranked_ids.items():
            row = by_id.get(chunk_id)
            if row is None:
                continue
            chunk, document = row
            (
                keyword_relevance,
                vector_similarity,
                has_concept_match,
                concept_coverage,
            ) = relevance_by_id[chunk_id]
            if not self._passes_relevance_gate(
                keyword_relevance,
                vector_similarity,
                has_concept_match=has_concept_match,
                concept_coverage=concept_coverage,
                best_keyword_relevance=best_keyword_relevance,
                best_vector_similarity=best_vector_similarity,
                best_concept_coverage=best_concept_coverage,
            ):
                continue
            hits.append(
                KnowledgeSearchHit(
                    citation_id=(
                        f"{document.source_key}:{document.version}#chunk-{chunk.chunk_index + 1}"
                    ),
                    document_id=document.id,
                    document_type=document.document_type,
                    title=document.title,
                    version=document.version,
                    content=chunk.content,
                    score=round(score, 6),
                    effective_from=document.effective_from,
                )
            )
            if len(hits) == limit:
                break
        return hits

    @staticmethod
    def _absolute_relevance(
        query_tokens: set[str],
        query_embedding: list[float],
        chunk: KnowledgeChunk,
    ) -> tuple[float, float, bool, float]:
        document_tokens = set(chunk.search_tokens.split())
        shared_tokens = query_tokens & document_tokens
        query_concepts = {
            token for token in query_tokens if token.startswith("concept_")
        }
        shared_concepts = {
            token for token in shared_tokens if token.startswith("concept_")
        }
        has_concept_match = bool(shared_concepts)
        concept_coverage = (
            len(shared_concepts) / len(query_concepts) if query_concepts else 0.0
        )
        keyword_relevance = 0.0
        if query_tokens and document_tokens and shared_tokens:
            weighted_overlap = sum(
                6.0 if token.startswith("concept_") else 1.0
                for token in shared_tokens
            )
            keyword_relevance = weighted_overlap / math.sqrt(
                len(query_tokens) * len(document_tokens)
            )
        vector_similarity = sum(
            left * right
            for left, right in zip(query_embedding, chunk.embedding, strict=True)
        )
        return keyword_relevance, vector_similarity, has_concept_match, concept_coverage

    @staticmethod
    def _passes_relevance_gate(
        keyword_relevance: float,
        vector_similarity: float,
        *,
        has_concept_match: bool,
        concept_coverage: float,
        best_keyword_relevance: float,
        best_vector_similarity: float,
        best_concept_coverage: float,
    ) -> bool:
        concept_is_competitive = best_concept_coverage == 0.0 or concept_coverage >= (
            best_concept_coverage * MIN_RELATIVE_RELEVANCE
        )
        if not concept_is_competitive:
            return False
        keyword_is_competitive = keyword_relevance >= (
            best_keyword_relevance * MIN_RELATIVE_RELEVANCE
        )
        if keyword_is_competitive and (
            has_concept_match or keyword_relevance >= MIN_KEYWORD_RELEVANCE
        ):
            return True
        return (
            vector_similarity >= MIN_VECTOR_SIMILARITY
            and vector_similarity
            >= best_vector_similarity * MIN_RELATIVE_RELEVANCE
            and keyword_relevance >= MIN_VECTOR_KEYWORD_SUPPORT
        )

    def _scope(
        self,
        statement: Select[Any],
        context: CommerceContext,
        *,
        document_type: str | None,
        as_of: datetime,
    ) -> Select[Any]:
        scoped = statement.where(
            KnowledgeDocument.tenant_id == context.tenant_id,
            KnowledgeDocument.store_id == context.store_id,
            KnowledgeDocument.status == "published",
            KnowledgeDocument.effective_from <= as_of,
            or_(
                KnowledgeDocument.effective_to.is_(None),
                KnowledgeDocument.effective_to > as_of,
            ),
        )
        if document_type:
            scoped = scoped.where(KnowledgeDocument.document_type == document_type)
        return scoped

    async def _postgres_rankings(
        self,
        context: CommerceContext,
        query: str,
        *,
        document_type: str | None,
        as_of: datetime,
        candidate_limit: int,
    ) -> dict[str, float]:
        query_tokens = lexical_tokens(query)
        ts_query = func.to_tsquery("simple", " | ".join(query_tokens))
        keyword_score = func.ts_rank_cd(
            func.to_tsvector("simple", KnowledgeChunk.search_tokens), ts_query
        )
        keyword_statement = self._scope(
            select(KnowledgeChunk.id, keyword_score.label("score")).join(
                KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id
            ),
            context,
            document_type=document_type,
            as_of=as_of,
        ).where(keyword_score > 0)
        keyword_rows = (
            await self._session.execute(
                keyword_statement.order_by(keyword_score.desc()).limit(candidate_limit)
            )
        ).all()

        query_embedding = embed_text(query)
        distance = cast(Any, KnowledgeChunk.embedding).cosine_distance(query_embedding)
        vector_statement = self._scope(
            select(KnowledgeChunk.id, distance.label("distance")).join(
                KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id
            ),
            context,
            document_type=document_type,
            as_of=as_of,
        ).where(distance < 0.9)
        vector_rows = (
            await self._session.execute(
                vector_statement.order_by(distance).limit(candidate_limit)
            )
        ).all()
        return self._rrf(
            [str(row.id) for row in keyword_rows],
            [str(row.id) for row in vector_rows],
        )

    async def _python_rankings(
        self,
        context: CommerceContext,
        query: str,
        *,
        document_type: str | None,
        as_of: datetime,
        candidate_limit: int,
    ) -> dict[str, float]:
        statement = self._scope(
            select(KnowledgeChunk, KnowledgeDocument).join(
                KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id
            ),
            context,
            document_type=document_type,
            as_of=as_of,
        )
        rows = (await self._session.execute(statement)).all()
        query_tokens = set(lexical_tokens(query))
        query_embedding = embed_text(query)
        keyword_scores: list[tuple[str, float]] = []
        vector_scores: list[tuple[str, float]] = []
        for chunk, _document in rows:
            (
                keyword_relevance,
                vector_similarity,
                _has_concept_match,
                _concept_coverage,
            ) = (
                self._absolute_relevance(query_tokens, query_embedding, chunk)
            )
            if keyword_relevance > 0:
                keyword_scores.append((str(chunk.id), keyword_relevance))
            if vector_similarity > 0.1:
                vector_scores.append((str(chunk.id), vector_similarity))
        keyword_scores.sort(key=lambda item: item[1], reverse=True)
        vector_scores.sort(key=lambda item: item[1], reverse=True)
        return self._rrf(
            [item[0] for item in keyword_scores[:candidate_limit]],
            [item[0] for item in vector_scores[:candidate_limit]],
        )

    @staticmethod
    def _rrf(keyword_ids: list[str], vector_ids: list[str]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for weight, ranked_ids in ((2.0, keyword_ids), (1.0, vector_ids)):
            for rank, chunk_id in enumerate(ranked_ids, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + rank)
        return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))
