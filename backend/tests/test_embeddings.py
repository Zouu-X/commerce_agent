import math

from app.knowledge.embeddings import (
    EMBEDDING_DIMENSIONS,
    DeterministicHashEmbeddingProvider,
)


def test_hash_provider_is_deterministic_normalized_and_schema_compatible() -> None:
    provider = DeterministicHashEmbeddingProvider()

    first = provider.embed_query("商品降价了可以申请保价吗？")
    second = provider.embed_documents(["商品降价了可以申请保价吗？"])[0]

    assert first == second
    assert len(first) == EMBEDDING_DIMENSIONS == 512
    assert math.isclose(sum(value * value for value in first), 1.0)
    assert all(value == 0 for value in first[64:])
