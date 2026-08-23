import pytest

from app.knowledge.decomposition import CommerceQueryDecomposer


@pytest.mark.parametrize(
    ("query", "expected_intents"),
    [
        (
            "无理由退货和换货流程分别是什么？",
            ["no_reason_return", "exchange_process"],
        ),
        (
            "配送失败后怎么处理，有店铺责任的延误补偿吗？",
            ["delivery_failed", "delay_compensation"],
        ),
        (
            "正常发货要多久，严重延迟又能补偿多少？",
            ["shipping_time", "delay_compensation"],
        ),
        (
            "订单能不能取消，退款执行后又多久到卡？",
            ["order_cancellation", "refund_timing"],
        ),
        (
            "耳机的质保范围和日常清洁方法都说一下",
            ["warranty", "product_care"],
        ),
        (
            "货已经寄出了，我应该取消还是签收后退？",
            ["order_cancellation", "no_reason_return"],
        ),
        (
            "退货审核和钱到账分别要看什么？",
            ["quality_return", "refund_timing"],
        ),
    ],
)
def test_decomposer_finds_independent_commerce_intents(
    query: str,
    expected_intents: list[str],
) -> None:
    plan = CommerceQueryDecomposer().decompose(query)

    assert plan.decomposed is True
    assert [subquery.intent for subquery in plan.subqueries] == expected_intents
    assert all(subquery.query != query for subquery in plan.subqueries)


@pytest.mark.parametrize(
    "query",
    [
        "质量问题退换的期限和举证要求是什么？",
        "七天无理由需要保留包装和赠品吗？",
        "退款审核通过后多久到账？",
        "未发货订单能取消吗？",
        "今天天气怎么样？",
    ],
)
def test_decomposer_keeps_single_intent_and_out_of_domain_queries_atomic(
    query: str,
) -> None:
    plan = CommerceQueryDecomposer().decompose(query)

    assert plan.decomposed is False
    assert len(plan.subqueries) == 1
    assert plan.subqueries[0].query == query


def test_decomposer_respects_explicit_document_type_scope() -> None:
    plan = CommerceQueryDecomposer().decompose(
        "耳机的质保范围和日常清洁方法都说一下",
        document_type="policy",
    )

    assert plan.decomposed is False
    assert plan.subqueries[0].intent == "warranty"
    assert plan.subqueries[0].document_type == "policy"


def test_decomposer_caps_the_retrieval_budget() -> None:
    plan = CommerceQueryDecomposer(max_subqueries=3).decompose(
        "取消订单、退款到账、无理由退货和换货流程都说一下"
    )

    assert plan.decomposed is True
    assert [subquery.intent for subquery in plan.subqueries] == [
        "order_cancellation",
        "refund_timing",
        "no_reason_return",
    ]
    assert "只保留前 3 个意图" in plan.reason


@pytest.mark.parametrize(
    ("query", "expected_intent"),
    [
        ("取消会员后转账多久到账？", "original_query"),
        ("不用取消订单，只想知道退款多久到账", "refund_timing"),
        ("不问退款多久到账，只说订单取消规则", "order_cancellation"),
    ],
)
def test_decomposer_rejects_ood_and_negated_intent_combinations(
    query: str,
    expected_intent: str,
) -> None:
    plan = CommerceQueryDecomposer().decompose(query)

    assert plan.decomposed is False
    assert plan.subqueries[0].intent == expected_intent
    if expected_intent == "original_query":
        assert plan.subqueries[0].query == query
    else:
        assert plan.subqueries[0].query != query


def test_decomposer_removes_contrastive_no_reason_clause_from_retrieval_query() -> None:
    plan = CommerceQueryDecomposer().decompose(
        "不是不喜欢，是商品有瑕疵，能退吗？"
    )

    assert plan.decomposed is False
    assert plan.subqueries[0].intent == "quality_return"
    assert "不喜欢" not in plan.subqueries[0].query
    assert "质量问题退换" in plan.subqueries[0].query


def test_decomposer_handles_unseen_cancellation_and_refund_paraphrase() -> None:
    plan = CommerceQueryDecomposer().decompose(
        "这笔订单还没寄出，可以撤掉吗？退款多久能到卡？"
    )

    assert [subquery.intent for subquery in plan.subqueries] == [
        "order_cancellation",
        "refund_timing",
    ]
