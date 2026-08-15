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


class KnowledgeSearchRead(StrictSchema):
    query: str
    retrieval: Literal["hybrid_rrf"] = "hybrid_rrf"
    hits: list[KnowledgeSearchHitRead]
