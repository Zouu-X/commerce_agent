from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.context import CommerceContext
from app.knowledge.service import KnowledgeSearchService
from app.tools.context import ToolContext


class SearchStorePolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    document_type: Literal["policy", "product_guide", "security_guide"] | None = None
    limit: int = Field(default=3, ge=1, le=5)


class KnowledgeToolHandlers:
    def __init__(self, session: AsyncSession, context: ToolContext) -> None:
        self._service = KnowledgeSearchService(session)
        self._context = CommerceContext(
            tenant_id=context.tenant_id,
            store_id=context.store_id,
            customer_id=context.customer_id,
        )

    async def search_store_policy(self, raw_args: BaseModel) -> dict[str, Any]:
        args = cast(SearchStorePolicyArgs, raw_args)
        hits = await self._service.search(
            self._context,
            args.query,
            document_type=args.document_type,
            limit=args.limit,
        )
        citations = [
            {
                "citation_id": hit.citation_id,
                "document_id": hit.document_id,
                "document_type": hit.document_type,
                "title": hit.title,
                "version": hit.version,
                "content": hit.content,
                "score": hit.score,
                "effective_from": hit.effective_from.isoformat(),
            }
            for hit in hits
        ]
        return {"query": args.query, "citations": citations, "count": len(citations)}
