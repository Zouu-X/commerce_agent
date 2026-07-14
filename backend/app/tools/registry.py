from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.types import ToolSpec
from app.commerce.errors import ResourceNotFoundError
from app.tools.context import ToolContext
from app.tools.read_tools import (
    GetAfterSaleStatusArgs,
    GetCustomerOrdersArgs,
    GetOrderDetailsArgs,
    GetProductDetailsArgs,
    ReadToolHandlers,
    SearchProductsArgs,
    TrackShipmentArgs,
)

ToolHandler = Callable[[BaseModel], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.arguments_model.model_json_schema(),
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate_tool:{tool.name}")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return self._error("unknown_tool")
        try:
            validated = tool.arguments_model.model_validate(arguments)
        except ValidationError as error:
            fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
            return self._error("invalid_arguments", details={"fields": fields})
        try:
            data = await tool.handler(validated)
        except ResourceNotFoundError as error:
            return self._error(str(error))
        except Exception:
            return self._error("tool_execution_failed")
        return {"ok": True, "tool": name, "data": data}

    @staticmethod
    def _error(code: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code}
        if details:
            error["details"] = details
        return {"ok": False, "error": error}


def build_read_tool_registry(session: AsyncSession, context: ToolContext) -> ToolRegistry:
    handlers = ReadToolHandlers(session, context)
    registry = ToolRegistry()
    for tool in (
        RegisteredTool(
            "search_products",
            "按关键词、分类、价格和库存查询当前店铺的商品。",
            SearchProductsArgs,
            handlers.search_products,
        ),
        RegisteredTool(
            "get_product_details",
            "按商品 ID 查询当前店铺的商品详情、SKU、价格和库存。",
            GetProductDetailsArgs,
            handlers.get_product_details,
        ),
        RegisteredTool(
            "get_customer_orders",
            "查询当前顾客在当前店铺的订单列表。",
            GetCustomerOrdersArgs,
            handlers.get_customer_orders,
        ),
        RegisteredTool(
            "get_order_details",
            "按订单号查询当前顾客的订单详情。",
            GetOrderDetailsArgs,
            handlers.get_order_details,
        ),
        RegisteredTool(
            "track_shipment",
            "查询当前顾客指定订单的物流节点和异常。",
            TrackShipmentArgs,
            handlers.track_shipment,
        ),
        RegisteredTool(
            "get_after_sale_status",
            "按售后申请 ID 查询当前顾客的售后状态。",
            GetAfterSaleStatusArgs,
            handlers.get_after_sale_status,
        ),
    ):
        registry.register(tool)
    return registry
