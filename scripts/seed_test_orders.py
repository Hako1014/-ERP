"""向本地 ERP 数据库批量写入测试订单。

用途：给看板、指标查询、报表与助手只读查询提供可用的数据量
（原演示数据只有 6 条订单，不足以验证趋势与筛选）。

可重复执行：以固定单号前缀 ``TEST-ORD-`` 识别本脚本写入的数据，
重复运行只做更新，不会重复插入。``--purge`` 可清除本脚本写入的全部数据。

运行方式（项目根目录）：
    backend\\.venv\\Scripts\\python.exe scripts\\seed_test_orders.py
    backend\\.venv\\Scripts\\python.exe scripts\\seed_test_orders.py --count 30
    backend\\.venv\\Scripts\\python.exe scripts\\seed_test_orders.py --purge
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401
from app.db.session import SessionLocal
from app.models.order import Order, OrderItem
from app.models.product import Product, ProductSku
from app.models.warehouse import Warehouse

ORDER_NO_PREFIX = "TEST-ORD-"
DEFAULT_COUNT = 30
SPAN_DAYS = 90

# 轮转的状态组合，保证每种状态都有样本，便于验证筛选与统计。
STATUS_CYCLE: tuple[tuple[str, str, str], ...] = (
    ("COMPLETED", "PAID", "APPROVED"),
    ("COMPLETED", "PAID", "APPROVED"),
    ("SHIPPED", "PAID", "APPROVED"),
    ("AUDITED", "PAID", "APPROVED"),
    ("NEW", "UNPAID", "PENDING"),
    ("NEW", "UNPAID", "PENDING"),
    ("AUDITED", "PARTIAL", "APPROVED"),
    ("CANCELLED", "PAID", "REJECTED"),
)

BUYERS = ("测试买家甲", "测试买家乙", "测试买家丙", "测试买家丁", "测试买家戊")
CITIES = (
    ("广东省", "深圳市", "南山区科技园南路 1 号"),
    ("浙江省", "杭州市", "余杭区文一西路 969 号"),
    ("江苏省", "南京市", "玄武区中山路 100 号"),
    ("四川省", "成都市", "高新区天府大道 500 号"),
    ("北京市", "北京市", "朝阳区建国路 88 号"),
)
LOGISTICS = ("顺丰速运", "中通快递", "圆通速递", "京东物流")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_reference(db: Session) -> tuple[Warehouse, list[tuple[Product, ProductSku]]]:
    """取默认仓库与可用于下单的商品/SKU。"""
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.is_default.is_(True)).limit(1)
    ) or db.scalar(select(Warehouse).order_by(Warehouse.id).limit(1))
    if warehouse is None:
        raise RuntimeError("本地库没有仓库，请先执行 scripts/seed_demo_data.py")

    pairs: list[tuple[Product, ProductSku]] = []
    for product in db.scalars(select(Product).order_by(Product.id)):
        sku = db.scalar(
            select(ProductSku)
            .where(ProductSku.product_id == product.id, ProductSku.is_active.is_(True))
            .order_by(ProductSku.id)
            .limit(1)
        )
        if sku is not None:
            pairs.append((product, sku))
    if not pairs:
        raise RuntimeError("本地库没有可用商品/SKU，请先执行 scripts/seed_demo_data.py")
    return warehouse, pairs


def _plan_items(index: int, pairs: list[tuple[Product, ProductSku]]) -> list[dict]:
    """按索引确定性地生成 1~3 行明细，保证重复执行结果一致。"""
    line_count = 1 + index % 3
    items: list[dict] = []
    for offset in range(line_count):
        product, sku = pairs[(index + offset) % len(pairs)]
        qty = Decimal(2 + (index + offset) % 7)
        price = Decimal(sku.sale_price or product.online_price or Decimal("10.00"))
        items.append(
            {
                "product": product,
                "sku": sku,
                "qty": qty,
                "price": price,
                "amount": qty * price,
            }
        )
    return items


def _plan_order(index: int, warehouse: Warehouse, pairs: list[tuple[Product, ProductSku]]) -> dict:
    status, pay_status, audit_status = STATUS_CYCLE[index % len(STATUS_CYCLE)]
    order_time = _now() - timedelta(days=index * 3, hours=index % 12, minutes=index % 60)
    items = _plan_items(index, pairs)
    goods_amount = sum((item["amount"] for item in items), Decimal("0"))
    freight = Decimal("8.00") if index % 4 == 0 else Decimal("0.00")
    platform_discount = Decimal("5.00") if index % 5 == 0 else Decimal("0.00")
    order_amount = goods_amount + freight - platform_discount
    if pay_status == "PAID":
        paid_amount = order_amount
    elif pay_status == "PARTIAL":
        paid_amount = (order_amount / 2).quantize(Decimal("0.01"))
    else:
        paid_amount = Decimal("0.00")

    province, city, address = CITIES[index % len(CITIES)]
    shipped = status in ("SHIPPED", "COMPLETED")
    return {
        "order_no": f"{ORDER_NO_PREFIX}{index + 1:04d}",
        "shop_id": None,  # 由调用方填入
        "warehouse_id": warehouse.id if status != "NEW" else None,
        "status": status,
        "audit_status": audit_status,
        "pay_status": pay_status,
        "order_time": order_time,
        "pay_time": order_time if pay_status != "UNPAID" else None,
        "ship_time": order_time + timedelta(days=1) if shipped else None,
        "order_amount": order_amount,
        "paid_amount": paid_amount,
        "freight": freight,
        "platform_discount": platform_discount,
        "seller_discount": Decimal("0.00"),
        "buyer_nick": BUYERS[index % len(BUYERS)],
        "receiver_name": BUYERS[(index + 1) % len(BUYERS)],
        "receiver_phone": f"139{index:08d}"[:11],
        "receiver_province": province,
        "receiver_city": city,
        "receiver_address": address,
        "logistics_company": LOGISTICS[index % len(LOGISTICS)] if shipped else None,
        "tracking_no": f"TEST-SF{index + 1:010d}" if shipped else None,
        "remark": f"批量测试数据 #{index + 1}",
        "_items": items,
    }


def _purge(db: Session) -> int:
    order_ids = list(
        db.scalars(select(Order.id).where(Order.order_no.like(f"{ORDER_NO_PREFIX}%")))
    )
    if not order_ids:
        return 0
    db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
    db.execute(delete(Order).where(Order.id.in_(order_ids)))
    db.commit()
    return len(order_ids)


def seed(count: int = DEFAULT_COUNT) -> dict[str, int]:
    with SessionLocal() as db:
        shop_id = db.scalar(select(Order.shop_id).order_by(Order.id).limit(1))
        if shop_id is None:
            raise RuntimeError("本地库没有店铺，请先执行 scripts/seed_demo_data.py")
        warehouse, pairs = _load_reference(db)

        created = updated = 0
        for index in range(count):
            plan = _plan_order(index, warehouse, pairs)
            items = plan.pop("_items")
            plan["shop_id"] = shop_id

            order = db.scalar(
                select(Order).where(Order.order_no == plan["order_no"])
            )
            if order is None:
                order = Order(**plan)
                db.add(order)
                db.flush()
                created += 1
            else:
                for key, value in plan.items():
                    setattr(order, key, value)
                updated += 1

            # 明细按订单整体重建，保证重复执行后与计划一致。
            db.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
            for item in items:
                db.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=item["product"].id,
                        sku_id=item["sku"].id,
                        sku_name=item["sku"].spec_name,
                        qty=item["qty"],
                        price=item["price"],
                        amount=item["amount"],
                        is_gift=False,
                    )
                )
            db.flush()

        db.commit()
        total = db.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.order_no.like(f"{ORDER_NO_PREFIX}%"))
        )
        return {"created": created, "updated": updated, "total": int(total or 0)}


def main() -> int:
    parser = argparse.ArgumentParser(description="写入批量测试订单")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="订单数量")
    parser.add_argument("--purge", action="store_true", help="清除本脚本写入的订单后退出")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.purge:
            removed = _purge(db)
            print(f"已清除 {removed} 条 {ORDER_NO_PREFIX} 订单")
            return 0

    if args.count < 1:
        print("--count 必须大于 0")
        return 1

    result = seed(args.count)
    print(
        f"测试订单写入完成：新增 {result['created']} 条，更新 {result['updated']} 条，"
        f"当前 {ORDER_NO_PREFIX} 订单共 {result['total']} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
