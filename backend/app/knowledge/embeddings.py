from __future__ import annotations

import hashlib
import math
import re

EMBEDDING_DIMENSIONS = 64

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


def embed_text(text: str) -> list[float]:
    """Hash lexical and semantic features into a local, normalized demo embedding."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in lexical_tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 4.0 if token.startswith("concept_") else 1.0
        vector[bucket] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
