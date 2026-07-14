from __future__ import annotations

import json
import re
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

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model

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


class MockCommerceProvider:
    """Deterministic local provider used for a zero-key demo and end-to-end tests."""

    _order_pattern = re.compile(
        r"(?<![A-Z0-9])[A-Z]{3}-\d{6}-\d{4}(?![A-Z0-9])", re.IGNORECASE
    )
    _uuid_pattern = re.compile(
        r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}"
        r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
        re.IGNORECASE,
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

        if order_number and any(word in user_text for word in ("物流", "快递", "到哪", "发货")):
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
        if any(word in user_text for word in ("订单", "购买记录")):
            return self._call(call_id, "get_customer_orders", {"limit": 10})

        if any(
            word in user_text
            for word in ("商品", "推荐", "耳机", "键盘", "鼠标", "背包", "杯", "鞋", "伞")
        ):
            arguments: dict[str, Any] = {"in_stock": True, "limit": 5}
            for term in ("耳机", "键盘", "鼠标", "背包", "保温杯", "跑鞋", "晴雨伞"):
                if term in user_text:
                    arguments["query"] = term
                    break
            for category in ("数码", "箱包", "家居", "服饰", "运动"):
                if category in user_text:
                    arguments["category"] = category
                    break
            return self._call(call_id, "search_products", arguments)

        return ModelResponse(
            content="我可以帮你查询商品与库存、当前账号的订单、物流异常和售后进度。"
        )

    @staticmethod
    def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
        return ModelResponse(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])

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
        return "查询已完成。"
