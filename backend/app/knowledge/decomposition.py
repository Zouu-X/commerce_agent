from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_NEGATED_INTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "no_reason_return": (r"(?:不是|并非)(?:因为)?不喜欢", r"不是没坏"),
    "order_cancellation": (
        r"(?:不用|不要|无需|不需要|别)(?:再)?(?:办理|申请)?(?:取消|撤销|撤掉)(?:订单|这单|该单)?",
    ),
    "refund_timing": (
        r"(?:不问|不用了解|无需了解|不需要知道|别说).{0,6}(?:退款|钱).{0,8}(?:到账|到卡|退回)",
    ),
}


@dataclass(frozen=True)
class RetrievalSubquery:
    subquery_id: str
    intent: str
    intent_label: str
    query: str
    document_type: str | None


@dataclass(frozen=True)
class QueryDecomposition:
    original_query: str
    decomposed: bool
    reason: str
    subqueries: tuple[RetrievalSubquery, ...]


class QueryDecomposer(Protocol):
    def decompose(
        self, query: str, *, document_type: str | None = None
    ) -> QueryDecomposition: ...


@dataclass(frozen=True)
class _DetectedIntent:
    intent: str
    label: str
    canonical_query: str
    document_type: str
    position: int


class CommerceQueryDecomposer:
    """Bounded, deterministic planner for independent commerce knowledge intents."""

    def __init__(self, *, max_subqueries: int = 3) -> None:
        self._max_subqueries = max_subqueries

    def decompose(
        self, query: str, *, document_type: str | None = None
    ) -> QueryDecomposition:
        normalized = query.strip()
        detected = self._detect_intents(normalized)
        if document_type is not None:
            detected = [item for item in detected if item.document_type == document_type]
        if len(detected) < 2:
            without_negated_clauses = self._without_negated_clauses(normalized)
            atomic_query = (
                detected[0].canonical_query
                if detected and without_negated_clauses != normalized
                else normalized
            )
            reason = "只检测到一个可独立回答的业务意图，保留原始查询。"
            if atomic_query != normalized:
                reason = "只检测到一个有效业务意图；检索时移除已明确排除的意图片段。"
            return QueryDecomposition(
                original_query=normalized,
                decomposed=False,
                reason=reason,
                subqueries=(
                    RetrievalSubquery(
                        subquery_id="subquery-1",
                        intent=detected[0].intent if detected else "original_query",
                        intent_label=detected[0].label if detected else "原始查询",
                        query=atomic_query,
                        document_type=document_type,
                    ),
                ),
            )

        selected = detected[: self._max_subqueries]
        labels = "、".join(item.label for item in selected)
        was_capped = len(detected) > self._max_subqueries
        reason = f"检测到可独立回答的业务意图：{labels}，分别检索以保证证据覆盖。"
        if was_capped:
            reason += f" 为控制检索预算，只保留前 {self._max_subqueries} 个意图。"
        return QueryDecomposition(
            original_query=normalized,
            decomposed=True,
            reason=reason,
            subqueries=tuple(
                RetrievalSubquery(
                    subquery_id=f"subquery-{index}",
                    intent=item.intent,
                    intent_label=item.label,
                    query=item.canonical_query,
                    document_type=item.document_type,
                )
                for index, item in enumerate(selected, start=1)
            ),
        )

    def _detect_intents(self, text: str) -> list[_DetectedIntent]:
        candidates: list[_DetectedIntent] = []

        self._add_if_matched(
            candidates,
            text,
            intent="no_reason_return",
            label="无理由退货",
            canonical_query="无理由退货的适用条件、期限和商品完好要求",
            document_type="policy",
            phrases=("无理由", "不喜欢", "没坏", "原样退", "签收后退"),
            pattern=r"(?:七|十五|7|15)\s*天.*退",
        )
        self._add_if_matched(
            candidates,
            text,
            intent="quality_return",
            label="质量问题退换",
            canonical_query="质量问题退换的期限、审核材料和退回运费规则",
            document_type="policy",
            phrases=(
                "质量问题",
                "有故障",
                "坏了",
                "瑕疵",
                "屏幕有问题",
                "退货审核",
                "退换的期限",
                "举证要求",
            ),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="exchange_process",
            label="换货流程",
            canonical_query="换货申请后的寄回、验收和补发流程",
            document_type="policy",
            phrases=("换货", "换新", "更换商品"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="shipping_time",
            label="正常发货时效",
            canonical_query="已付款订单的正常发货时效",
            document_type="policy",
            phrases=("正常发货", "发货时效", "什么时候发货", "多久发货", "几小时发货"),
            pattern=r"发货.*(?:多久|多少小时|几小时|时效)",
        )
        self._add_if_matched(
            candidates,
            text,
            intent="logistics_stale",
            label="物流停滞处理",
            canonical_query="物流轨迹长时间没有更新时的处理规则",
            document_type="policy",
            phrases=("物流没更新", "物流未更新", "轨迹没更新", "没有新节点", "物流停滞"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="delivery_failed",
            label="配送失败处理",
            canonical_query="配送失败后的地址核实、重新派送和退仓处理规则",
            document_type="policy",
            phrases=("配送失败", "派送失败", "投递失败", "联系不上"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="price_protection",
            label="商品保价",
            canonical_query="商品支付后降价时的保价期限和排除条件",
            document_type="policy",
            phrases=("保价", "价保", "降价", "退差价"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="delay_compensation",
            label="延迟补偿",
            canonical_query="店铺责任导致严重物流延迟时的补偿规则和金额",
            document_type="policy",
            phrases=("延误补偿", "延迟补偿", "补偿多少", "补偿券", "严重延迟"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="order_cancellation",
            label="订单取消",
            canonical_query="订单在未发货和已发货状态下的取消规则",
            document_type="policy",
            phrases=("取消", "撤销订单", "撤掉", "订单不要了"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="refund_timing",
            label="退款到账",
            canonical_query="退款审核通过并执行后的原路退回到账时间",
            document_type="policy",
            phrases=("到账", "到卡", "原路退回", "退款执行", "钱退回"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="warranty",
            label="商品质保",
            canonical_query="商品质保期限、适用范围和排除条件",
            document_type="policy",
            phrases=("质保", "保修", "维修范围"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="product_care",
            label="清洁保养",
            canonical_query="商品日常清洁、保养和维护方法",
            document_type="product_guide",
            phrases=("清洁", "保养", "维护", "怎么洗"),
        )
        self._add_if_matched(
            candidates,
            text,
            intent="knowledge_security",
            label="知识内容安全",
            canonical_query="知识内容中的不可信指令和越权信息应如何处理",
            document_type="security_guide",
            phrases=("忽略系统指令", "泄露其他顾客", "不可信文本", "越权指令"),
        )

        excluded_intents: set[str] = set()
        # A bare “取消” or “到账” is not enough to select order/refund policy:
        # membership cancellation and bank transfers are deliberately out of domain.
        if not (
            re.search(r"(?:订单|这单|该单).{0,10}(?:取消|撤销|撤掉|不要)", text)
            or re.search(r"(?:取消|撤销|撤掉).{0,10}(?:订单|这单|该单)", text)
            or (
                any(word in text for word in ("取消", "撤销", "撤掉"))
                and any(word in text for word in ("发货", "寄出", "出库", "签收", "退货"))
            )
        ):
            excluded_intents.add("order_cancellation")
        if not (
            any(word in text for word in ("退款", "退货", "售后", "原路退回", "钱退回"))
            or re.search(r"钱.{0,8}(?:到账|到卡|退回|回到)", text)
        ):
            excluded_intents.add("refund_timing")

        # Negated or contrastive mentions describe what the customer does not ask.
        # Keep those words from becoming independent retrieval tasks.
        for intent, patterns in _NEGATED_INTENT_PATTERNS.items():
            if any(re.search(pattern, text) for pattern in patterns):
                excluded_intents.add(intent)
        candidates = [item for item in candidates if item.intent not in excluded_intents]

        # The same intent can match both a phrase and a regex. Keep its earliest mention.
        by_intent: dict[str, _DetectedIntent] = {}
        for item in candidates:
            current = by_intent.get(item.intent)
            if current is None or item.position < current.position:
                by_intent[item.intent] = item
        return sorted(by_intent.values(), key=lambda item: item.position)

    @staticmethod
    def _without_negated_clauses(text: str) -> str:
        cleaned = text
        for patterns in _NEGATED_INTENT_PATTERNS.values():
            for pattern in patterns:
                cleaned = re.sub(
                    rf"{pattern}\s*[，,、；;]?\s*",
                    "",
                    cleaned,
                )
        if cleaned == text:
            return text
        return cleaned.strip(" ，,、；;。！？?") or text

    @staticmethod
    def _add_if_matched(
        candidates: list[_DetectedIntent],
        text: str,
        *,
        intent: str,
        label: str,
        canonical_query: str,
        document_type: str,
        phrases: tuple[str, ...],
        pattern: str | None = None,
    ) -> None:
        positions = [text.find(phrase) for phrase in phrases if phrase in text]
        if pattern is not None:
            match = re.search(pattern, text)
            if match is not None:
                positions.append(match.start())
        if not positions:
            return
        candidates.append(
            _DetectedIntent(
                intent=intent,
                label=label,
                canonical_query=canonical_query,
                document_type=document_type,
                position=min(positions),
            )
        )
