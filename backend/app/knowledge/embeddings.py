from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from app.core.config import get_settings

EMBEDDING_DIMENSIONS = 512
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

_TERM_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9]+", re.IGNORECASE)
_CONCEPT_ALIASES = {
    "returns": ("退货", "退款", "退换", "无理由", "退回"),
    "no_reason_return": ("无理由",),
    "exchange": ("换货", "换新", "更换"),
    "shipping": ("发货", "寄出", "出库", "配送"),
    "logistics": ("物流", "快递", "运输", "轨迹"),
    "stale": ("没更新", "未更新", "停滞", "没有动"),
    "failed_delivery": ("派送失败", "配送失败", "联系不上"),
    "price_protection": ("保价", "价保", "降价", "差价"),
    "compensation": ("补偿", "优惠券", "券", "赔付"),
    "cancellation": ("取消", "撤销", "不要了"),
    "refund_timing": ("到账", "原路退回", "退款时间", "多久到账"),
    "warranty": ("质保", "保修", "维修"),
    "product_care": ("保养", "清洁", "使用说明", "维护"),
    "duration": ("多久", "几天", "多少天", "期限", "时限", "时效", "小时", "天内"),
    "security": ("忽略系统指令", "泄露", "越权", "其他顾客"),
}


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def lexical_tokens(text: str) -> list[str]:
    """Create deterministic Chinese-friendly unigram/bigram and Latin tokens."""
    tokens: set[str] = set()
    for term in _TERM_PATTERN.findall(text.lower()):
        if term.isascii():
            tokens.add(term)
            continue
        characters = list(term)
        tokens.update(characters)
        tokens.update(
            "".join(characters[index : index + 2])
            for index in range(max(0, len(characters) - 1))
        )
    for concept, aliases in _CONCEPT_ALIASES.items():
        if any(alias in text.lower() for alias in aliases):
            tokens.add(f"concept_{concept}")
    return sorted(tokens)


def search_document(text: str) -> str:
    return " ".join(lexical_tokens(text))


class DeterministicHashEmbeddingProvider:
    """Fast offline test double; it is deliberately not the runtime default."""

    provider_name = "deterministic_hash"
    model_name = "blake2b-lexical-features-v1"
    _feature_buckets = 64

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in lexical_tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            # Keep the historical 64-bucket baseline stable, then zero-pad it to
            # the database's real-model dimension so old evaluation comparisons hold.
            bucket = int.from_bytes(digest[:4], "big") % min(
                self._feature_buckets, self.dimensions
            )
            sign = 1.0 if digest[4] & 1 else -1.0
            weight = 4.0 if token.startswith("concept_") else 1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class FastEmbedEmbeddingProvider:
    """Lazy, CPU-first ONNX embedding provider for the local demo runtime."""

    provider_name = "fastembed"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self._cache_dir = str(Path(cache_dir).expanduser()) if cache_dir else None
        self._threads = threads
        self._model: Any | None = None
        self._load_lock = Lock()

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._get_model().query_embed(text)))
        return self._validate_vector(vector.tolist())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [
            self._validate_vector(vector.tolist())
            for vector in self._get_model().passage_embed(texts)
        ]

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:  # pragma: no cover - packaging failure
                    raise RuntimeError(
                        "fastembed is required for EMBEDDING_PROVIDER=fastembed; "
                        "install the embedding optional dependency"
                    ) from exc
                self._model = TextEmbedding(
                    self.model_name,
                    cache_dir=self._cache_dir,
                    threads=self._threads,
                )
        return self._model

    def _validate_vector(self, vector: list[float]) -> list[float]:
        if len(vector) != self.dimensions:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}"
            )
        return vector


@lru_cache
def _build_embedding_provider(
    provider_name: str,
    model_name: str,
    cache_dir: str | None,
    threads: int | None,
) -> EmbeddingProvider:
    if provider_name == "hash":
        return DeterministicHashEmbeddingProvider()
    if provider_name == "fastembed":
        return FastEmbedEmbeddingProvider(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
        )
    raise ValueError(f"unsupported embedding provider: {provider_name}")


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    return _build_embedding_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_cache_dir,
        settings.embedding_threads,
    )
