from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CustomerSource:
    citation_id: str
    title: str
    version: str


@dataclass(frozen=True)
class PresentedToolResult:
    data: dict[str, Any]
    sources: list[CustomerSource]


_ORDER_STATUS = {
    "pending": "待付款",
    "paid": "已付款，等待发货",
    "shipped": "已发货",
    "delivered": "已签收",
    "cancelled": "已取消",
}
_PAYMENT_STATUS = {
    "unpaid": "未付款",
    "paid": "已付款",
    "partially_refunded": "已部分退款",
    "refunded": "已退款",
}
_SHIPMENT_STATUS = {
    "pending": "等待发货",
    "shipped": "运输中",
    "in_transit": "运输中",
    "delivered": "已签收",
    "delivery_failed": "配送失败",
}
_AFTER_SALE_STATUS = {
    "pending": "待处理",
    "reviewing": "审核中",
    "approved": "已通过",
    "rejected": "已拒绝",
    "completed": "已完成",
}
_AFTER_SALE_TYPE = {"refund": "退款", "return": "退货", "exchange": "换货"}
_ACTION_LABEL = {
    "cancel_order": "取消订单",
    "refund": "退款",
    "issue_coupon": "补偿券",
}

_CITATION_WRAPPER_RE = re.compile(
    r"[（(]?\s*citation\s*[:：]\s*[`'\"]?[\w-]+:v\d+#chunk-\d+[`'\"]?\s*[)）]?",
    re.IGNORECASE,
)
_CITATION_TOKEN_RE = re.compile(r"[`\[]?[\w-]+:v\d+#chunk-\d+[`\]]?", re.IGNORECASE)
_SNAKE_CASE_RE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b", re.IGNORECASE)
_BOOLEAN_RE = re.compile(r"\b(?:true|false|null)\b", re.IGNORECASE)


def present_tool_result(tool_name: str, result: dict[str, Any]) -> PresentedToolResult:
    if result.get("ok") is not True:
        code = str((result.get("error") or {}).get("code", "tool_error"))
        if code.endswith("not_found"):
            summary = "当前账号下未找到对应记录，请核对信息后再试。"
        elif code == "invalid_arguments":
            summary = "提供的信息还不完整，请补充后再试。"
        else:
            summary = "查询未完成，请稍后重试或联系人工客服。"
        return PresentedToolResult(data={"summary": summary}, sources=[])

    data = result.get("data") or {}
    if tool_name == "search_products":
        products = [
            {
                "name": item.get("name"),
                "description": item.get("description"),
                "category": item.get("category"),
                "price": f"¥{item.get('min_price')} 起",
                "stock": f"现有 {item.get('total_stock')} 件",
            }
            for item in data.get("products", [])
        ]
        summary = "找到以下有库存的商品。" if products else "当前没有找到符合条件的商品。"
        return PresentedToolResult(data={"summary": summary, "products": products}, sources=[])

    if tool_name == "get_product_details":
        variants = [
            {
                "sku": item.get("sku"),
                "attributes": item.get("attributes"),
                "price": f"¥{item.get('price')}",
                "stock": f"现有 {item.get('stock_quantity')} 件",
            }
            for item in data.get("variants", [])
        ]
        return PresentedToolResult(
            data={
                "summary": f"{data.get('name')}：{data.get('description')}",
                "category": data.get("category"),
                "variants": variants,
            },
            sources=[],
        )

    if tool_name == "get_customer_orders":
        orders = [_customer_order(item) for item in data.get("orders", [])]
        summary = "这是当前账号下的订单。" if orders else "当前账号下没有订单记录。"
        return PresentedToolResult(data={"summary": summary, "orders": orders}, sources=[])

    if tool_name == "get_order_details":
        return PresentedToolResult(data=_customer_order(data), sources=[])

    if tool_name == "track_shipment":
        events = data.get("events", [])
        latest = events[0] if events else None
        anomaly = {
            "NO_UPDATE_5_DAYS": "物流已超过 5 天没有更新",
            "DELIVERY_FAILED": "最近一次配送失败",
        }.get(str(data.get("anomaly") or ""), "暂未发现物流异常")
        return PresentedToolResult(
            data={
                "summary": (
                    f"物流状态：{_label(_SHIPMENT_STATUS, data.get('status'))}；{anomaly}。"
                ),
                "carrier": data.get("carrier"),
                "tracking_number": data.get("tracking_number"),
                "last_updated_at": data.get("last_updated_at"),
                "latest_event": (
                    {
                        "time": latest.get("occurred_at"),
                        "location": latest.get("location"),
                        "description": latest.get("description"),
                    }
                    if latest
                    else "暂无物流节点"
                ),
            },
            sources=[],
        )

    if tool_name == "get_after_sale_status":
        return PresentedToolResult(
            data={
                "summary": (
                    f"订单 {data.get('order_number')} 的"
                    f"{_label(_AFTER_SALE_TYPE, data.get('type'))}申请"
                    f"目前为{_label(_AFTER_SALE_STATUS, data.get('status'))}。"
                ),
                "reason": data.get("reason"),
                "requested_amount": (
                    f"¥{data.get('requested_amount')}"
                    if data.get("requested_amount") is not None
                    else None
                ),
            },
            sources=[],
        )

    if tool_name == "search_store_policy":
        sources = [
            CustomerSource(
                citation_id=str(item.get("citation_id", "")),
                title=str(item.get("title", "店铺政策")),
                version=str(item.get("version", "")),
            )
            for item in data.get("citations", [])[:2]
            if item.get("citation_id")
        ]
        evidence = [
            {
                "title": item.get("title"),
                "version": item.get("version"),
                "content": item.get("content"),
            }
            for item in data.get("citations", [])[:2]
        ]
        summary = (
            "请依据以下当前有效的店铺资料回答，并用资料标题说明依据。"
            if evidence
            else "当前有效知识中没有足够证据回答这个问题，建议转人工确认。"
        )
        return PresentedToolResult(
            data={"summary": summary, "sources": evidence}, sources=sources
        )

    if tool_name in {
        "request_order_cancellation",
        "request_refund",
        "request_coupon",
    }:
        action_label = _ACTION_LABEL.get(str(data.get("action_type")), "业务操作")
        payload = data.get("payload") or {}
        target = payload.get("order_number")
        target_text = f"订单 {target} 的" if target else ""
        return PresentedToolResult(
            data={
                "summary": (
                    f"已提交{target_text}{action_label}申请，当前为待人工审批。"
                    "审批前不会修改订单、退款或优惠券数据。"
                )
            },
            sources=[],
        )

    return PresentedToolResult(data={"summary": "查询已完成。"}, sources=[])


def sanitize_customer_response(content: str) -> str:
    sanitized = _CITATION_WRAPPER_RE.sub("", content)
    sanitized = _CITATION_TOKEN_RE.sub("", sanitized)
    sanitized = re.sub(
        r"has_shipment\s*(?:为|[:=])\s*(?:\*\*)?false(?:\*\*)?",
        "暂无物流记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"has_shipment\s*(?:为|[:=])\s*(?:\*\*)?true(?:\*\*)?",
        "已有物流记录",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = sanitized.replace("payment_status", "支付状态")
    sanitized = sanitized.replace("has_shipment", "物流记录")
    for raw, label in {
        "partially_refunded": "已部分退款",
        "delivery_failed": "配送失败",
        "in_transit": "运输中",
        "cancelled": "已取消",
        "delivered": "已签收",
        "shipped": "已发货",
        "refunded": "已退款",
        "reviewing": "审核中",
        "pending": "待处理",
        "unpaid": "未付款",
        "paid": "已付款",
    }.items():
        sanitized = re.sub(rf"\b{raw}\b", label, sanitized, flags=re.IGNORECASE)
    sanitized = _SNAKE_CASE_RE.sub("相关状态", sanitized)
    sanitized = _BOOLEAN_RE.sub("相关状态", sanitized)
    sanitized = sanitized.replace("`", "").replace("**", "")
    sanitized = re.sub(r"[（(]\s*[)）]", "", sanitized)
    sanitized = re.sub(r"[ \t]+\n", "\n", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    sanitized = re.sub(r" {2,}", " ", sanitized)
    sanitized = sanitized.strip()
    if not sanitized or contains_internal_details(sanitized):
        return "查询已完成，但回复中包含无法安全展示的内部信息，请联系人工客服确认。"
    return sanitized


def contains_internal_details(content: str) -> bool:
    return bool(
        _CITATION_WRAPPER_RE.search(content)
        or _CITATION_TOKEN_RE.search(content)
        or _SNAKE_CASE_RE.search(content)
        or _BOOLEAN_RE.search(content)
    )


def _customer_order(data: dict[str, Any]) -> dict[str, Any]:
    items = [
        {
            "product": item.get("product_name"),
            "quantity": item.get("quantity"),
            "unit_price": f"¥{item.get('unit_price')}",
        }
        for item in data.get("items", [])
    ]
    shipment = "已有物流记录" if data.get("has_shipment") else "暂无物流记录"
    return {
        "summary": (
            f"订单 {data.get('order_number')}："
            f"{_label(_ORDER_STATUS, data.get('status'))}，"
            f"{_label(_PAYMENT_STATUS, data.get('payment_status'))}，{shipment}。"
        ),
        "order_number": data.get("order_number"),
        "amount": f"¥{data.get('total_amount')}",
        "items": items,
    }


def _label(mapping: dict[str, str], value: Any) -> str:
    raw = str(value or "")
    return mapping.get(raw, "状态待确认")
