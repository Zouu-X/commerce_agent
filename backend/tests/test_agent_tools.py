import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.seed import stable_id
from app.tools.context import ToolContext
from app.tools.registry import build_read_tool_registry

pytestmark = pytest.mark.anyio


def tool_context(customer_index: int) -> ToolContext:
    return ToolContext(
        tenant_id=stable_id("tenant:aurora"),
        store_id=stable_id("store:aurora"),
        customer_id=stable_id(f"customer:aurora:{customer_index}"),
        conversation_id=stable_id(f"conversation:{customer_index}"),
        trace_id=stable_id(f"trace:{customer_index}"),
    )


async def test_tool_schemas_do_not_expose_trusted_identity(db_session: AsyncSession) -> None:
    registry = build_read_tool_registry(db_session, tool_context(0))

    for spec in registry.specs():
        properties = spec.parameters.get("properties", {})
        assert "tenant_id" not in properties
        assert "store_id" not in properties
        assert "customer_id" not in properties

    result = await registry.execute(
        "get_order_details",
        {
            "order_number": "AUR-202607-0001",
            "customer_id": str(stable_id("customer:aurora:1")),
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert "customer_id" in result["error"]["details"]["fields"]


async def test_tool_registry_hides_another_customers_order(db_session: AsyncSession) -> None:
    registry = build_read_tool_registry(db_session, tool_context(1))

    result = await registry.execute(
        "get_order_details", {"order_number": "AUR-202607-0001"}
    )

    assert result == {"ok": False, "error": {"code": "order_not_found"}}


async def test_unknown_tool_returns_stable_error(db_session: AsyncSession) -> None:
    registry = build_read_tool_registry(db_session, tool_context(0))

    assert await registry.execute("drop_all_orders", {}) == {
        "ok": False,
        "error": {"code": "unknown_tool"},
    }


async def test_all_six_read_tool_adapters_return_structured_data(
    db_session: AsyncSession,
) -> None:
    registry = build_read_tool_registry(db_session, tool_context(2))
    calls = (
        ("search_products", {"query": "耳机"}),
        ("get_product_details", {"product_id": str(stable_id("product:aurora:1"))}),
        ("get_customer_orders", {"limit": 5}),
        ("get_order_details", {"order_number": "AUR-202607-0003"}),
        ("track_shipment", {"order_number": "AUR-202607-0003"}),
        (
            "get_after_sale_status",
            {"after_sale_id": str(stable_id("after-sale:aurora:2"))},
        ),
    )

    for tool_name, arguments in calls:
        result = await registry.execute(tool_name, arguments)
        assert result["ok"] is True, (tool_name, result)
        assert result["tool"] == tool_name
        assert result["data"]
