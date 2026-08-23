from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_commerce_context
from app.commerce.context import CommerceContext
from app.db.session import get_db_session
from app.knowledge.service import KnowledgeSearchService
from app.schemas.knowledge import (
    KnowledgeSearchHitRead,
    KnowledgeSearchRead,
    QueryDecompositionRead,
    RetrievalSubqueryRead,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
ContextDependency = Annotated[CommerceContext, Depends(get_commerce_context)]


@router.get("/search", response_model=KnowledgeSearchRead)
async def search_knowledge(
    session: SessionDependency,
    context: ContextDependency,
    query: Annotated[str, Query(min_length=1, max_length=500)],
    document_type: Literal["policy", "product_guide", "security_guide"] | None = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> KnowledgeSearchRead:
    result = await KnowledgeSearchService(session).search_with_explanation(
        context,
        query,
        document_type=document_type,
        limit=limit,
    )
    return KnowledgeSearchRead(
        query=query,
        hits=[
            KnowledgeSearchHitRead(
                citation_id=hit.citation_id,
                document_id=hit.document_id,
                document_type=hit.document_type,
                title=hit.title,
                version=hit.version,
                content=hit.content,
                score=hit.score,
                effective_from=hit.effective_from,
                matched_subquery_ids=list(hit.matched_subquery_ids),
                matched_intents=list(hit.matched_intents),
            )
            for hit in result.hits
        ],
        decomposition=QueryDecompositionRead(
            decomposed=result.decomposition.decomposed,
            reason=result.decomposition.reason,
            subqueries=[
                RetrievalSubqueryRead(
                    subquery_id=subquery.subquery_id,
                    intent=subquery.intent,
                    intent_label=subquery.intent_label,
                    query=subquery.query,
                    document_type=subquery.document_type,
                )
                for subquery in result.decomposition.subqueries
            ],
            unresolved_subquery_ids=list(result.unresolved_subquery_ids),
        ),
    )
