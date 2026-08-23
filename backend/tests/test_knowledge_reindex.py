import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.reindex import reindex_knowledge
from app.models import KnowledgeChunk

pytestmark = pytest.mark.anyio


class FakeSemanticEmbeddingProvider:
    provider_name = "fake_semantic"
    model_name = "fake-embedding-v1"
    dimensions = 512

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), *([0.0] * 511)] for text in texts]


async def test_reindex_records_model_metadata_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    provider = FakeSemanticEmbeddingProvider()

    first = await reindex_knowledge(db_session, provider=provider)
    second = await reindex_knowledge(db_session, provider=provider)
    chunk = await db_session.scalar(select(KnowledgeChunk).limit(1))

    assert first["total_chunks"] == 28
    assert first["indexed_chunks"] == 28
    assert second["indexed_chunks"] == 0
    assert second["skipped_chunks"] == 28
    assert chunk is not None
    assert chunk.embedding is not None and len(chunk.embedding) == 512
    assert chunk.metadata_json["embedding_provider"] == "fake_semantic"
    assert chunk.metadata_json["embedding_model"] == "fake-embedding-v1"
