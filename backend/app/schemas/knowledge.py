from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeSearchHitRead(StrictSchema):
    citation_id: str
    document_id: UUID
    document_type: str
    title: str
    version: str
    content: str
    score: float
    effective_from: datetime
    matched_subquery_ids: list[str]
    matched_intents: list[str]


class RetrievalSubqueryRead(StrictSchema):
    subquery_id: str
    intent: str
    intent_label: str
    query: str
    document_type: str | None


class QueryDecompositionRead(StrictSchema):
    decomposed: bool
    reason: str
    subqueries: list[RetrievalSubqueryRead]
    unresolved_subquery_ids: list[str]


class KnowledgeSearchRead(StrictSchema):
    query: str
    retrieval: Literal["hybrid_rrf"] = "hybrid_rrf"
    hits: list[KnowledgeSearchHitRead]
    decomposition: QueryDecompositionRead
