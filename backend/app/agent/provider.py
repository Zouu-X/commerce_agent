from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.errors import AgentTimeoutError, ModelProviderError
from app.agent.types import ModelResponse, ModelUsage, ProviderMessage, ToolCall, ToolSpec


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse: ...


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _FunctionCall(_StrictResponse):
    name: str
    arguments: str


class _ProviderToolCall(_StrictResponse):
    id: str
    function: _FunctionCall


class _AssistantMessage(_StrictResponse):
    content: str | None = None
    tool_calls: list[_ProviderToolCall] = Field(default_factory=list)


class _Choice(_StrictResponse):
    message: _AssistantMessage


class _Usage(_StrictResponse):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _ChatCompletion(_StrictResponse):
    choices: list[_Choice] = Field(min_length=1)
    usage: _Usage = Field(default_factory=_Usage)


class OpenAICompatibleProvider:
    """Minimal Chat Completions adapter; the Agent runtime does not depend on an SDK."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        request_options: dict[str, Any] | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._request_options = request_options or {}

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        payload = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            **self._request_options,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            parsed = _ChatCompletion.model_validate(response.json())
        except httpx.TimeoutException as error:
            raise AgentTimeoutError("model_timeout") from error
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise ModelProviderError("model_provider_failed") from error

        message = parsed.choices[0].message
        calls: list[ToolCall] = []
        for call in message.tool_calls:
            try:
                arguments = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                arguments = {"__invalid_json__": call.function.arguments}
            if not isinstance(arguments, dict):
                arguments = {"__invalid_arguments__": arguments}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return ModelResponse(
            content=message.content or "",
            tool_calls=calls,
            usage=ModelUsage(
                input_tokens=parsed.usage.prompt_tokens,
                output_tokens=parsed.usage.completion_tokens,
            ),
        )

    @staticmethod
    def _message_payload(message: ProviderMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role == "tool":
            payload["tool_call_id"] = message.tool_call_id
        return payload


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek V4 adapter using its OpenAI-compatible tool-calling API."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        # V4 defaults to thinking mode. Thinking tool calls require reasoning_content
        # round-tripping; customer support favors the lower-latency non-thinking path.
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            request_options={"thinking": {"type": "disabled"}},
        )


class MockCommerceProvider:
    """Deterministic provider retained for tests and reproducible evaluation baselines."""

    _order_pattern = re.compile(
        r"(?<![A-Z0-9])[A-Z]{3}-\d{6}-\d{4}(?![A-Z0-9])", re.IGNORECASE
    )
    _uuid_pattern = re.compile(
        r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}"
        r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
        re.IGNORECASE,
    )
    _amount_pattern = re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)\s*元")
    _explicit_knowledge_terms = ("政策", "规则", "时效")
    _knowledge_terms = (
        "政策",
        "无理由",
        "退货",
        "退款多久到账",
        "到账",
        "发货时效",
        "什么时候发货",
        "发货",
        "物流",
        "配送失败",
        "没更新",
        "保价",
        "价保",
        "降价",
        "差价",
        "差额",
        "补偿",
        "换货",
        "质保",
        "保修",
        "保养",
        "清洁",
        "忽略系统指令",
    )

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolSpec],
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        del tools, timeout_seconds
        if not messages:
            return ModelResponse(content="请告诉我你想查询的商品、订单或物流信息。")
        if messages[-1].role == "tool":
            return ModelResponse(content=self._render_tool_results(messages))

        user_text = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        order_match = self._order_pattern.search(user_text.upper())
        order_number = order_match.group(0).upper() if order_match else None
        call_id = f"mock_{uuid4().hex}"

        # Explicit policy/rule questions describe the requested evidence source,
        # even when an order number or a logistics keyword is also present.
        if any(word in user_text for word in self._explicit_knowledge_terms):
            return self._knowledge_call(call_id, user_text)
        if order_number and any(
            phrase in user_text for phrase in ("取消订单", "帮我取消", "不想要了", "不要了")
        ):
            return self._call(
                call_id,
                "request_order_cancellation",
                {"order_number": order_number, "reason": user_text},
            )
        if order_number and any(
            phrase in user_text for phrase in ("申请退款", "帮我退款", "退我", "我要退款")
        ):
            refund_arguments: dict[str, Any] = {
                "order_number": order_number,
                "reason": user_text,
            }
            amount = self._amount(user_text)
            if amount is not None:
                refund_arguments["amount"] = str(amount)
            return self._call(call_id, "request_refund", refund_arguments)
        if any(word in user_text for word in ("发优惠券", "发券", "补偿券", "补偿优惠券")):
            amount = self._amount(user_text)
            if amount is None:
                return ModelResponse(content="请说明希望申请的补偿券金额，例如 10 元。")
            coupon_arguments: dict[str, Any] = {
                "amount": str(amount),
                "reason": user_text,
            }
            if order_number:
                coupon_arguments["order_number"] = order_number
            return self._call(call_id, "request_coupon", coupon_arguments)
        if order_number and any(
            word in user_text for word in ("物流", "快递", "到哪", "发货", "配送")
        ):
            return self._call(call_id, "track_shipment", {"order_number": order_number})
        if order_number:
            return self._call(call_id, "get_order_details", {"order_number": order_number})

        after_sale_id = self._uuid_pattern.search(user_text)
        if after_sale_id and any(word in user_text for word in ("售后", "退款", "退货")):
            return self._call(
                call_id,
                "get_after_sale_status",
                {"after_sale_id": after_sale_id.group(0)},
            )
        if any(word in user_text for word in self._knowledge_terms):
            return self._knowledge_call(call_id, user_text)
        if any(word in user_text for word in ("订单", "购买记录")):
            return self._call(call_id, "get_customer_orders", {"limit": 10})

        if any(
            word in user_text
            for word in (
                "商品",
                "推荐",
                "现货",
                "耳机",
                "键盘",
                "鼠标",
                "背包",
                "杯",
                "鞋",
                "伞",
            )
        ):
            product_arguments: dict[str, Any] = {"in_stock": True, "limit": 5}
            for term in ("耳机", "键盘", "鼠标", "背包", "保温杯", "跑鞋", "晴雨伞"):
                if term in user_text:
                    product_arguments["query"] = term
                    break
            for category in ("数码", "箱包", "家居", "服饰", "运动"):
                if category in user_text:
                    product_arguments["category"] = category
                    break
            return self._call(call_id, "search_products", product_arguments)

        return ModelResponse(
            content="我可以帮你查询商品与库存、当前账号的订单、物流异常和售后进度。"
        )

    @staticmethod
    def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
        return ModelResponse(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])

    @classmethod
    def _amount(cls, user_text: str) -> Decimal | None:
        match = cls._amount_pattern.search(user_text)
        return Decimal(match.group(1)) if match else None

    @classmethod
    def _knowledge_call(cls, call_id: str, user_text: str) -> ModelResponse:
        arguments: dict[str, Any] = {"query": user_text, "limit": 3}
        if any(word in user_text for word in ("保养", "清洁", "使用说明")):
            arguments["document_type"] = "product_guide"
        elif "忽略系统指令" in user_text:
            arguments["document_type"] = "security_guide"
        else:
            arguments["document_type"] = "policy"
        return cls._call(call_id, "search_store_policy", arguments)

    @staticmethod
    def _render_tool_results(messages: list[ProviderMessage]) -> str:
        tool_messages: list[ProviderMessage] = []
        for message in reversed(messages):
            if message.role != "tool":
                break
            tool_messages.append(message)
        rendered = [
            MockCommerceProvider._render_one(message) for message in reversed(tool_messages)
        ]
        return "\n".join(rendered)

    @staticmethod
    def _render_one(message: ProviderMessage) -> str:
        try:
            result = json.loads(message.content)
        except json.JSONDecodeError:
            return "查询结果暂时无法解析，请稍后重试。"
        if "summary" in result:
            return MockCommerceProvider._render_customer_safe_result(message.tool_name, result)
        if not result.get("ok"):
            code = result.get("error", {}).get("code", "tool_error")
            if code.endswith("not_found"):
                return "当前账号下未找到对应记录，请核对信息后再试。"
            return f"查询未完成（{code}），请核对参数后再试。"

        data = result.get("data", {})
        if message.tool_name == "search_products":
            products = data.get("products", [])
            if not products:
                return "当前没有找到符合条件且有库存的商品。"
            items = [
                f"{item['name']}（最低 ¥{item['min_price']}，库存 {item['total_stock']}）"
                for item in products
            ]
            return "为你找到：" + "；".join(items) + "。"
        if message.tool_name == "get_product_details":
            return (
                f"{data['name']}：{data['description']}，"
                f"可选 SKU 共 {len(data['variants'])} 个。"
            )
        if message.tool_name == "get_customer_orders":
            orders = data.get("orders", [])
            if not orders:
                return "当前账号下没有订单记录。"
            items = [
                f"{item['order_number']}（{item['status']}，¥{item['total_amount']}）"
                for item in orders
            ]
            return "你的订单包括：" + "；".join(items) + "。"
        if message.tool_name == "get_order_details":
            return (
                f"订单 {data['order_number']} 当前状态为 {data['status']}，"
                f"支付状态为 {data['payment_status']}，订单金额 ¥{data['total_amount']}。"
            )
        if message.tool_name == "track_shipment":
            anomaly = data.get("anomaly")
            anomaly_text = {
                "NO_UPDATE_5_DAYS": "物流已超过 5 天未更新",
                "DELIVERY_FAILED": "配送失败",
            }.get(anomaly, "暂未发现物流异常")
            events = data.get("events", [])
            latest = events[0]["description"] if events else "暂无物流节点"
            return f"物流状态为 {data['status']}；{anomaly_text}。最近节点：{latest}。"
        if message.tool_name == "get_after_sale_status":
            return (
                f"售后申请 {data['id']} 当前状态为 {data['status']}，"
                f"申请类型为 {data['type']}，金额 ¥{data['requested_amount']}。"
            )
        if message.tool_name == "search_store_policy":
            citations = data.get("citations", [])
            if not citations:
                return "当前有效知识中没有足够证据回答这个问题，建议转人工确认。"
            if any("忽略系统指令" in item.get("content", "") for item in citations):
                citation = citations[0]["citation_id"]
                return f"检索内容含有指令性文本，已按不可信资料处理，不会执行。[{citation}]"
            evidence = citations[:2]
            return "；".join(
                f"{item['content']} [{item['citation_id']}]" for item in evidence
            )
        if message.tool_name in {
            "request_order_cancellation",
            "request_refund",
            "request_coupon",
        }:
            return (
                f"已创建待人工审批的 {data['action_type']} 请求，"
                f"审批编号 {data['action_id']}。审批前不会修改订单、退款或发券。"
            )
        return "查询已完成。"

    @staticmethod
    def _render_customer_safe_result(tool_name: str | None, data: dict[str, Any]) -> str:
        summary = str(data.get("summary", "查询已完成。"))
        if tool_name == "search_products":
            products = data.get("products", [])
            if not products:
                return summary
            items = [
                f"{item['name']}（{item['price']}，{item['stock']}）" for item in products
            ]
            return "为你找到：" + "；".join(items) + "。"
        if tool_name == "get_customer_orders":
            orders = data.get("orders", [])
            if not orders:
                return summary
            return "你的订单包括：" + "；".join(
                str(item.get("summary", "")) for item in orders
            )
        if tool_name == "get_order_details":
            return summary
        if tool_name == "track_shipment":
            latest = data.get("latest_event")
            if isinstance(latest, dict):
                return f"{summary}最近节点：{latest.get('description', '暂无物流节点')}。"
            return summary
        if tool_name == "get_after_sale_status":
            return summary
        if tool_name == "search_store_policy":
            sources = data.get("sources", [])
            if not sources:
                return summary
            if any("忽略系统指令" in str(item.get("content", "")) for item in sources):
                return "检索资料含有指令性文本，已按不可信资料处理，不会执行。"
            return "；".join(
                f"根据《{item.get('title', '店铺政策')}》：{item.get('content', '')}"
                for item in sources[:2]
            )
        return summary
