import json

from app.agent.presentation import (
    contains_internal_details,
    present_tool_result,
    sanitize_customer_response,
)


def test_order_tool_projection_hides_internal_fields_and_enums() -> None:
    presented = present_tool_result(
        "get_order_details",
        {
            "ok": True,
            "tool": "get_order_details",
            "data": {
                "order_number": "AUR-202607-0024",
                "status": "cancelled",
                "payment_status": "paid",
                "total_amount": "454.00",
                "has_shipment": False,
                "items": [
                    {
                        "product_name": "城市慢跑鞋",
                        "quantity": 2,
                        "unit_price": "227.00",
                    }
                ],
            },
        },
    )

    serialized = json.dumps(presented.data, ensure_ascii=False)
    assert "已取消" in serialized
    assert "已付款" in serialized
    assert "暂无物流记录" in serialized
    assert "payment_status" not in serialized
    assert "has_shipment" not in serialized
    assert "cancelled" not in serialized


def test_knowledge_projection_keeps_machine_citation_out_of_model_context() -> None:
    presented = present_tool_result(
        "search_store_policy",
        {
            "ok": True,
            "tool": "search_store_policy",
            "data": {
                "citations": [
                    {
                        "citation_id": "quality-return:v1#chunk-1",
                        "document_id": "internal-id",
                        "title": "质量问题退换政策",
                        "version": "v1",
                        "content": "质量问题可在签收后 30 天内申请退换。",
                    }
                ]
            },
        },
    )

    serialized = json.dumps(presented.data, ensure_ascii=False)
    assert "质量问题退换政策" in serialized
    assert "quality-return" not in serialized
    assert "document_id" not in serialized
    assert presented.sources[0].citation_id == "quality-return:v1#chunk-1"


def test_final_response_guard_removes_internal_protocol_details() -> None:
    response = sanitize_customer_response(
        "订单 payment_status 为 **paid（已付款）**，has_shipment 为 false。"
        "（citation: `quality-return:v1#chunk-1`）"
    )

    assert "已付款" in response
    assert "暂无物流记录" in response
    assert not contains_internal_details(response)
    assert "citation" not in response
    assert "#chunk" not in response
    assert "**" not in response
