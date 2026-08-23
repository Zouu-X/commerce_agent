from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from app.models import KnowledgeChunk, KnowledgeDocument


def _embedding_metadata(provider: EmbeddingProvider) -> dict[str, Any]:
    return {
        "embedding_provider": provider.provider_name,
        "embedding_model": provider.model_name,
        "embedding_dimensions": provider.dimensions,
    }


def _is_current(chunk: KnowledgeChunk, provider: EmbeddingProvider) -> bool:
    expected = _embedding_metadata(provider)
    return (
        chunk.embedding is not None
        and len(chunk.embedding) == provider.dimensions
        and all(chunk.metadata_json.get(key) == value for key, value in expected.items())
    )


async def reindex_knowledge(
    session: AsyncSession,
    *,
    provider: EmbeddingProvider | None = None,
    force: bool = False,
) -> dict[str, Any]:
    selected_provider = provider or get_embedding_provider()
    rows = (
        await session.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index)
        )
    ).all()
    pending = [
        (chunk, document)
        for chunk, document in rows
        if force or not _is_current(chunk, selected_provider)
    ]
    if pending:
        texts = [f"{document.title} {chunk.content}" for chunk, document in pending]
        vectors = await asyncio.to_thread(selected_provider.embed_documents, texts)
        if len(vectors) != len(pending):
            raise ValueError(
                f"embedding count mismatch: expected {len(pending)}, got {len(vectors)}"
            )
        embedding_metadata = _embedding_metadata(selected_provider)
        for (chunk, _document), vector in zip(pending, vectors, strict=True):
            if len(vector) != selected_provider.dimensions:
                raise ValueError(
                    "embedding dimension mismatch during knowledge reindex: "
                    f"expected {selected_provider.dimensions}, got {len(vector)}"
                )
            chunk.embedding = vector
            chunk.metadata_json = {**chunk.metadata_json, **embedding_metadata}
        await session.flush()
    return {
        "provider": selected_provider.provider_name,
        "model": selected_provider.model_name,
        "dimensions": selected_provider.dimensions,
        "total_chunks": len(rows),
        "indexed_chunks": len(pending),
        "skipped_chunks": len(rows) - len(pending),
    }


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild local knowledge embeddings")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    async with SessionFactory() as session, session.begin():
        result = await reindex_knowledge(session, force=args.force)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(async_main())
