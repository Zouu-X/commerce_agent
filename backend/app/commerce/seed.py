from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.models import (
    AfterSale,
    Customer,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Shipment,
    ShipmentEvent,
    Store,
    Tenant,
)

SEED_NAMESPACE = uuid.UUID("432f38f6-e958-4fa0-a6d8-b76cfd31be71")
BASE_TIME = datetime(2026, 7, 12, 8, 0, tzinfo=UTC)


def stable_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


async def clear_commerce_data(session: AsyncSession) -> None:
    for model in (
        ShipmentEvent,
        AfterSale,
        Shipment,
        OrderItem,
        Order,
        ProductVariant,
        Product,
        Customer,
        Store,
        Tenant,
    ):
        await session.execute(delete(model))


def build_seed_objects() -> tuple[list[object], dict[str, int]]:
    objects: list[object] = []
    counts = {
        "tenants": 0,
        "stores": 0,
        "customers": 0,
        "products": 0,
        "variants": 0,
        "orders": 0,
        "shipments": 0,
        "shipment_events": 0,
        "after_sales": 0,
    }
    tenant_specs = [
        ("aurora", "极光生活", "极光生活旗舰店", "AUR"),
        ("harbor", "海港数码", "海港数码专营店", "HBR"),
    ]
    customer_names = ["林晓", "陈嘉", "周宁", "王晨", "赵敏", "刘洋"]
    product_specs = [
        ("轻量通勤双肩包", "箱包", "防泼水通勤背包，适合日常和短途出行"),
        ("恒温保温杯", "家居", "316 不锈钢内胆，长效保温"),
        ("降噪蓝牙耳机", "数码", "主动降噪和通透模式"),
        ("人体工学鼠标", "数码", "静音按键和多档 DPI"),
        ("有机棉基础T恤", "服饰", "亲肤透气的基础款上衣"),
        ("旅行收纳套装", "箱包", "六件套分类旅行收纳袋"),
        ("桌面氛围灯", "家居", "无级调光和三档色温"),
        ("便携机械键盘", "数码", "三模连接的紧凑机械键盘"),
        ("速干运动毛巾", "运动", "轻量吸汗，附带收纳袋"),
        ("折叠晴雨伞", "家居", "防晒涂层与抗风伞骨"),
        ("城市慢跑鞋", "运动", "缓震中底和透气鞋面"),
        ("羊毛混纺围巾", "服饰", "柔软保暖的秋冬围巾"),
    ]

    for tenant_key, tenant_name, store_name, prefix in tenant_specs:
        tenant_id = stable_id(f"tenant:{tenant_key}")
        store_id = stable_id(f"store:{tenant_key}")
        objects.extend(
            [
                Tenant(
                    id=tenant_id,
                    name=tenant_name,
                    status="active",
                    created_at=BASE_TIME - timedelta(days=365),
                ),
                Store(
                    id=store_id,
                    tenant_id=tenant_id,
                    name=store_name,
                    business_hours={"weekdays": "09:00-21:00", "weekends": "10:00-20:00"},
                    timezone="Asia/Shanghai",
                ),
            ]
        )
        counts["tenants"] += 1
        counts["stores"] += 1

        customer_ids: list[uuid.UUID] = []
        for customer_index, display_name in enumerate(customer_names):
            customer_id = stable_id(f"customer:{tenant_key}:{customer_index}")
            customer_ids.append(customer_id)
            objects.append(
                Customer(
                    id=customer_id,
                    tenant_id=tenant_id,
                    display_name=f"{display_name}（{tenant_name}）",
                    email=f"customer{customer_index}@{tenant_key}.example",
                    membership_level=("gold" if customer_index == 0 else "regular"),
                )
            )
            counts["customers"] += 1

        variant_records: list[tuple[uuid.UUID, Decimal]] = []
        for product_index, (name, category, description) in enumerate(product_specs):
            product_id = stable_id(f"product:{tenant_key}:{product_index}")
            objects.append(
                Product(
                    id=product_id,
                    tenant_id=tenant_id,
                    store_id=store_id,
                    name=name,
                    description=f"{description}。由{store_name}提供。",
                    category=category,
                    status="active",
                )
            )
            counts["products"] += 1
            for variant_index, color in enumerate(("深灰", "米白")):
                variant_id = stable_id(f"variant:{tenant_key}:{product_index}:{variant_index}")
                price = Decimal("49.00") + Decimal(product_index * 17 + variant_index * 8)
                stock = 0 if product_index == 0 else 8 + product_index + variant_index
                objects.append(
                    ProductVariant(
                        id=variant_id,
                        product_id=product_id,
                        sku=f"{prefix}-{product_index + 1:03d}-{variant_index + 1}",
                        attributes_json={"颜色": color, "规格": "标准款"},
                        price=price,
                        stock_quantity=stock,
                    )
                )
                variant_records.append((variant_id, price))
                counts["variants"] += 1

        order_statuses = ("paid", "shipped", "delivered", "cancelled", "pending")
        for order_index in range(30):
            order_id = stable_id(f"order:{tenant_key}:{order_index}")
            customer_id = customer_ids[order_index % len(customer_ids)]
            variant_id, unit_price = variant_records[(order_index * 3) % len(variant_records)]
            quantity = 1 + order_index % 2
            total_amount = unit_price * quantity
            status = order_statuses[order_index % len(order_statuses)]
            if order_index in (4, 5):
                status = "shipped"
            order_number = f"{prefix}-202607-{order_index + 1:04d}"
            created_at = BASE_TIME - timedelta(days=order_index + 1)
            objects.extend(
                [
                    Order(
                        id=order_id,
                        tenant_id=tenant_id,
                        store_id=store_id,
                        customer_id=customer_id,
                        order_number=order_number,
                        status=status,
                        payment_status=("unpaid" if status == "pending" else "paid"),
                        total_amount=total_amount,
                        created_at=created_at,
                    ),
                    OrderItem(
                        id=stable_id(f"order-item:{tenant_key}:{order_index}"),
                        order_id=order_id,
                        variant_id=variant_id,
                        quantity=quantity,
                        unit_price=unit_price,
                    ),
                ]
            )
            counts["orders"] += 1

            if status in {"shipped", "delivered"}:
                shipment_id = stable_id(f"shipment:{tenant_key}:{order_index}")
                shipment_status = status
                last_updated_at = BASE_TIME - timedelta(hours=12)
                if order_index == 4:
                    last_updated_at = BASE_TIME - timedelta(days=6)
                if order_index == 5:
                    shipment_status = "delivery_failed"
                objects.append(
                    Shipment(
                        id=shipment_id,
                        order_id=order_id,
                        carrier="顺路快递",
                        tracking_number=f"YT{prefix}{order_index + 1:010d}",
                        status=shipment_status,
                        last_updated_at=last_updated_at,
                    )
                )
                counts["shipments"] += 1
                for event_index, event_status in enumerate(("picked_up", shipment_status)):
                    objects.append(
                        ShipmentEvent(
                            id=stable_id(
                                f"shipment-event:{tenant_key}:{order_index}:{event_index}"
                            ),
                            shipment_id=shipment_id,
                            status=event_status,
                            location=("杭州转运中心" if event_index == 0 else "上海配送站"),
                            description=(
                                "快件已揽收"
                                if event_index == 0
                                else {
                                    "delivered": "快件已签收",
                                    "delivery_failed": "收件地址暂时无法联系",
                                }.get(shipment_status, "运输途中")
                            ),
                            occurred_at=last_updated_at
                            - timedelta(hours=12 if event_index == 0 else 0),
                        )
                    )
                    counts["shipment_events"] += 1

            if order_index % 10 == 2:
                objects.append(
                    AfterSale(
                        id=stable_id(f"after-sale:{tenant_key}:{order_index}"),
                        tenant_id=tenant_id,
                        order_id=order_id,
                        customer_id=customer_id,
                        type="refund",
                        reason="商品与描述不符",
                        status="reviewing",
                        requested_amount=total_amount,
                        created_at=created_at + timedelta(days=2),
                    )
                )
                counts["after_sales"] += 1

    return objects, counts


async def seed(*, if_empty: bool = False) -> dict[str, int]:
    async with SessionFactory() as session, session.begin():
        if if_empty and await session.scalar(select(Tenant.id).limit(1)) is not None:
            return {"skipped": 1}
        await clear_commerce_data(session)
        objects, counts = build_seed_objects()
        session.add_all(objects)
    return counts


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic commerce demo data")
    parser.add_argument("--if-empty", action="store_true")
    args = parser.parse_args()
    result = await seed(if_empty=args.if_empty)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(async_main())
