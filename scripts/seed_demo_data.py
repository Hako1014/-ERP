"""向本地 ERP 数据库写入可重复执行的演示数据。

运行方式（项目根目录）：
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py

脚本以固定编码和单号识别演示数据，重复执行不会重复插入。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.basic import Brand, Unit
from app.models.order import Order, OrderItem
from app.models.org import Company, Staff
from app.models.party import Supplier, SupplierPrice
from app.models.platform import Platform, Shop
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanItem,
)
from app.models.rbac import Role, StaffRole
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import purchase_plan_service

DEMO_PASSWORD = "123456"
DEMO_COMPANY_NAME = "ERP演示公司"
DEMO_BRAND_NAME = "演示品牌"
DEMO_UNIT_NAME = "件"
DEMO_PLATFORM_CODE = "DEMO-PLATFORM"
DEMO_PLATFORM_NAME = "演示平台"
DEMO_SHOP_NAME = "演示店铺"

ROLE_DEFINITIONS = {
    "ADMIN": "系统管理员",
    "VP": "分管副总",
    "FINANCE": "财务",
    "PURCHASER": "采购员",
    "WAREHOUSE": "仓库人员",
    "QUALITY": "商控/质检",
    "AFTER_SALE": "售后/客服",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(days: int) -> datetime:
    return _now() - timedelta(days=days)


def _get_by(db: Session, model: type[Any], **filters: Any) -> Any | None:
    conditions = [getattr(model, key) == value for key, value in filters.items()]
    return db.scalar(select(model).where(*conditions))


def _get_or_create(
    db: Session,
    model: type[Any],
    *,
    lookup: dict[str, Any],
    values: dict[str, Any],
) -> Any:
    row = _get_by(db, model, **lookup)
    if row is None:
        row = model(**lookup, **values)
        db.add(row)
        db.flush()
        return row
    for key, value in values.items():
        setattr(row, key, value)
    db.flush()
    return row


def _upsert_order(
    db: Session,
    *,
    order_no: str,
    shop_id: int,
    status: str,
    pay_status: str,
    order_time: datetime,
    amount: Decimal,
    paid_amount: Decimal,
) -> Order:
    return _get_or_create(
        db,
        Order,
        lookup={"order_no": order_no},
        values={
            "shop_id": shop_id,
            "status": status,
            "pay_status": pay_status,
            "order_time": order_time,
            "pay_time": order_time if pay_status == "PAID" else None,
            "order_amount": amount,
            "paid_amount": paid_amount,
            "freight": Decimal("0"),
            "platform_discount": Decimal("0"),
            "seller_discount": Decimal("0"),
        },
    )


def _upsert_order_item(
    db: Session,
    *,
    order: Order,
    product: Product,
    sku: ProductSku,
    qty: Decimal,
    price: Decimal,
    is_gift: bool = False,
) -> OrderItem:
    item = _get_by(
        db,
        OrderItem,
        order_id=order.id,
        product_id=product.id,
        sku_id=sku.id,
        is_gift=is_gift,
    )
    values = {
        "sku_name": sku.spec_name,
        "qty": qty,
        "price": price,
        "amount": Decimal("0") if is_gift else qty * price,
    }
    if item is None:
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            sku_id=sku.id,
            is_gift=is_gift,
            **values,
        )
        db.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
    db.flush()
    return item


def _seed_company_roles_and_staff(
    db: Session, *, password: str
) -> tuple[Company, Staff, Staff]:
    company = _get_or_create(
        db,
        Company,
        lookup={"full_name": DEMO_COMPANY_NAME},
        values={
            "short_name": "演示公司",
            "legal_person": "演示管理员",
            "phone": "13800000001",
            "tax_no": "DEMO-TAX-001",
        },
    )

    roles: dict[str, Role] = {}
    for code, name in ROLE_DEFINITIONS.items():
        roles[code] = _get_or_create(
            db,
            Role,
            lookup={"company_id": company.id, "code": code},
            values={"name": name},
        )

    admin = _get_or_create(
        db,
        Staff,
        lookup={"phone": "13800000001"},
        values={
            "company_id": company.id,
            "name": "演示管理员",
            "staff_no": "DEMO-001",
            "login_account": "demo_admin",
            "password_hash": hash_password(password),
            "is_active": True,
        },
    )
    purchaser = _get_or_create(
        db,
        Staff,
        lookup={"phone": "13800000002"},
        values={
            "company_id": company.id,
            "name": "演示采购员",
            "staff_no": "DEMO-002",
            "login_account": "demo_purchaser",
            "password_hash": hash_password(password),
            "is_active": True,
        },
    )

    _get_or_create(
        db,
        StaffRole,
        lookup={"staff_id": admin.id, "role_id": roles["ADMIN"].id},
        values={},
    )
    _get_or_create(
        db,
        StaffRole,
        lookup={"staff_id": purchaser.id, "role_id": roles["PURCHASER"].id},
        values={},
    )
    return company, admin, purchaser


def _seed_basic_data(
    db: Session, *, company_id: int
) -> tuple[Supplier, Brand, Unit]:
    supplier = _get_or_create(
        db,
        Supplier,
        lookup={"code": "DEMO-SUP-001"},
        values={
            "name": "演示供应商",
            "contact": "演示联系人",
            "phone": "13800000003",
            "settlement_type": "MONTHLY",
            "is_active": True,
        },
    )
    brand = _get_or_create(
        db,
        Brand,
        lookup={"name": DEMO_BRAND_NAME},
        values={},
    )
    unit = _get_or_create(
        db,
        Unit,
        lookup={"name": DEMO_UNIT_NAME},
        values={"code": "DEMO-PCS"},
    )
    if company_id < 1:
        raise ValueError("公司 ID 无效")
    return supplier, brand, unit


def _seed_products(
    db: Session, *, supplier: Supplier, brand: Brand, unit: Unit
) -> tuple[Product, Product, ProductSku, ProductSku]:
    product_a = _get_or_create(
        db,
        Product,
        lookup={"code": "DEMO-P-001"},
        values={
            "name": "演示商品A",
            "art_no": "DEMO-ART-A",
            "brand_id": brand.id,
            "default_supplier_id": supplier.id,
            "main_unit_id": unit.id,
            "cost_price": Decimal("9.00"),
            "tax_rate": Decimal("13.00"),
            "is_disabled": False,
        },
    )
    product_b = _get_or_create(
        db,
        Product,
        lookup={"code": "DEMO-P-002"},
        values={
            "name": "演示商品B",
            "art_no": "DEMO-ART-B",
            "brand_id": brand.id,
            "default_supplier_id": supplier.id,
            "main_unit_id": unit.id,
            "cost_price": Decimal("7.00"),
            "tax_rate": Decimal("13.00"),
            "is_disabled": False,
        },
    )
    sku_a = _get_or_create(
        db,
        ProductSku,
        lookup={"spec_code": "DEMO-P-001-RED"},
        values={
            "product_id": product_a.id,
            "spec_name": "红色",
            "cost_price": Decimal("8.50"),
            "sale_price": Decimal("20.00"),
            "is_active": True,
        },
    )
    sku_b = _get_or_create(
        db,
        ProductSku,
        lookup={"spec_code": "DEMO-P-002-BLUE"},
        values={
            "product_id": product_b.id,
            "spec_name": "蓝色",
            "cost_price": Decimal("6.50"),
            "sale_price": Decimal("10.00"),
            "is_active": True,
        },
    )
    for product, sku, price in (
        (product_a, sku_a, Decimal("10.00")),
        (product_b, sku_b, Decimal("8.00")),
    ):
        _get_or_create(
            db,
            SupplierPrice,
            lookup={
                "supplier_id": supplier.id,
                "product_id": product.id,
                "sku_id": sku.id,
            },
            values={"price": price, "status": "ACTIVE"},
        )
    return product_a, product_b, sku_a, sku_b


def _seed_warehouse_stock(
    db: Session, *, sku_a: ProductSku, sku_b: ProductSku
) -> tuple[Warehouse, Location]:
    warehouse = _get_or_create(
        db,
        Warehouse,
        lookup={"code": "DEMO-WH-01"},
        values={
            "name": "演示主仓",
            "warehouse_type": "FINISHED",
            "is_default": True,
            "enable_location": True,
            "is_virtual": False,
            "is_enabled": True,
        },
    )
    location = _get_or_create(
        db,
        Location,
        lookup={"code": "DEMO-A-01"},
        values={
            "warehouse_id": warehouse.id,
            "is_virtual": False,
            "is_enabled": True,
        },
    )
    for sku, qty in ((sku_a, Decimal("3.000")), (sku_b, Decimal("5.000"))):
        _get_or_create(
            db,
            StockLocation,
            lookup={
                "location_id": location.id,
                "product_sku_id": sku.id,
                "status": "NORMAL",
            },
            values={"qty": qty},
        )
    return warehouse, location


def _seed_shop(
    db: Session, *, supplier: Supplier, warehouse: Warehouse
) -> Shop:
    platform = _get_or_create(
        db,
        Platform,
        lookup={"code": DEMO_PLATFORM_CODE},
        values={"name": DEMO_PLATFORM_NAME},
    )
    return _get_or_create(
        db,
        Shop,
        lookup={"platform_id": platform.id, "name": DEMO_SHOP_NAME},
        values={
            "supplier_id": supplier.id,
            "default_address": warehouse.address or "演示仓库地址",
            "is_locked": False,
        },
    )


def _seed_orders(
    db: Session,
    *,
    shop: Shop,
    product_a: Product,
    product_b: Product,
    sku_a: ProductSku,
    sku_b: ProductSku,
) -> dict[str, Order]:
    paid = _upsert_order(
        db,
        order_no="DEMO-ORDER-PAID",
        shop_id=shop.id,
        status="COMPLETED",
        pay_status="PAID",
        order_time=_days_ago(2),
        amount=Decimal("260.00"),
        paid_amount=Decimal("260.00"),
    )
    _upsert_order_item(
        db,
        order=paid,
        product=product_a,
        sku=sku_a,
        qty=Decimal("12.000"),
        price=Decimal("20.00"),
    )
    _upsert_order_item(
        db,
        order=paid,
        product=product_b,
        sku=sku_b,
        qty=Decimal("2.000"),
        price=Decimal("10.00"),
    )

    cancelled = _upsert_order(
        db,
        order_no="DEMO-ORDER-CANCELLED",
        shop_id=shop.id,
        status="CANCELLED",
        pay_status="PAID",
        order_time=_days_ago(1),
        amount=Decimal("140.00"),
        paid_amount=Decimal("140.00"),
    )
    _upsert_order_item(
        db,
        order=cancelled,
        product=product_a,
        sku=sku_a,
        qty=Decimal("7.000"),
        price=Decimal("20.00"),
    )

    old = _upsert_order(
        db,
        order_no="DEMO-ORDER-OLD",
        shop_id=shop.id,
        status="COMPLETED",
        pay_status="PAID",
        order_time=_days_ago(30),
        amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
    )
    _upsert_order_item(
        db,
        order=old,
        product=product_a,
        sku=sku_a,
        qty=Decimal("5.000"),
        price=Decimal("20.00"),
    )

    gift = _upsert_order(
        db,
        order_no="DEMO-ORDER-GIFT",
        shop_id=shop.id,
        status="COMPLETED",
        pay_status="PAID",
        order_time=_days_ago(1),
        amount=Decimal("0.00"),
        paid_amount=Decimal("0.00"),
    )
    _upsert_order_item(
        db,
        order=gift,
        product=product_a,
        sku=sku_a,
        qty=Decimal("99.000"),
        price=Decimal("20.00"),
        is_gift=True,
    )

    unpaid = _upsert_order(
        db,
        order_no="DEMO-ORDER-UNPAID",
        shop_id=shop.id,
        status="NEW",
        pay_status="UNPAID",
        order_time=_days_ago(1),
        amount=Decimal("1000.00"),
        paid_amount=Decimal("0.00"),
    )
    _upsert_order_item(
        db,
        order=unpaid,
        product=product_a,
        sku=sku_a,
        qty=Decimal("50.000"),
        price=Decimal("20.00"),
    )

    return {
        "paid": paid,
        "cancelled": cancelled,
        "old": old,
        "gift": gift,
        "unpaid": unpaid,
    }


def _seed_purchase_data(
    db: Session,
    *,
    supplier: Supplier,
    warehouse: Warehouse,
    purchaser: Staff,
    product_a: Product,
    sku_a: ProductSku,
) -> tuple[PurchaseOrder, PurchasePlan]:
    purchase_order = _get_or_create(
        db,
        PurchaseOrder,
        lookup={"order_no": "DEMO-PO-IN-TRANSIT"},
        values={
            "supplier_id": supplier.id,
            "warehouse_id": warehouse.id,
            "purchaser_id": purchaser.id,
            "status": "APPROVED",
            "audit_status": "APPROVED",
            "total_qty": Decimal("3.000"),
            "total_amount": Decimal("30.00"),
            "arrived_qty": Decimal("1.000"),
            "prepay_amount": Decimal("0.00"),
            "description": "演示在途采购订单",
        },
    )
    order_item = _get_by(
        db,
        PurchaseOrderItem,
        order_id=purchase_order.id,
        product_id=product_a.id,
        sku_id=sku_a.id,
    )
    order_item_values = {
        "purchase_price": Decimal("10.00"),
        "qty": Decimal("3.000"),
        "in_transit_qty": Decimal("2.000"),
        "arrived_qty": Decimal("1.000"),
        "amount": Decimal("30.00"),
    }
    if order_item is None:
        db.add(
            PurchaseOrderItem(
                order_id=purchase_order.id,
                product_id=product_a.id,
                sku_id=sku_a.id,
                **order_item_values,
            )
        )
    else:
        for key, value in order_item_values.items():
            setattr(order_item, key, value)

    purchase_plan = _get_or_create(
        db,
        PurchasePlan,
        lookup={"plan_no": "DEMO-PP-IN-PROGRESS"},
        values={
            "supplier_id": supplier.id,
            "plan_date": _days_ago(1),
            "status": "IN_PROGRESS",
            "audit_status": "PENDING",
            "total_qty": Decimal("2.000"),
            "total_amount": Decimal("20.00"),
            "remark": "演示进行中采购计划",
            "created_by": purchaser.id,
        },
    )
    plan_item = _get_by(
        db,
        PurchasePlanItem,
        plan_id=purchase_plan.id,
        product_id=product_a.id,
        sku_id=sku_a.id,
    )
    plan_item_values = {
        "planned_qty": Decimal("2.000"),
        "purchased_qty": Decimal("1.000"),
        "purchase_price": Decimal("10.00"),
        "amount": Decimal("20.00"),
    }
    if plan_item is None:
        db.add(
            PurchasePlanItem(
                plan_id=purchase_plan.id,
                product_id=product_a.id,
                sku_id=sku_a.id,
                **plan_item_values,
            )
        )
    else:
        for key, value in plan_item_values.items():
            setattr(plan_item, key, value)
    db.flush()
    return purchase_order, purchase_plan


def _print_preview(db: Session, *, supplier_id: int) -> None:
    preview = purchase_plan_service.preview_purchase_needs(
        db, supplier_id=supplier_id, page=1, page_size=200
    )
    print("采购需求测算：")
    for item in preview["items"]:
        print(
            "  {product_code}/{spec_code}: "
            "20日销量={sales_20d}, 库存={current_stock}, "
            "在途={in_transit_stock}, 进行中={in_progress_qty}, "
            "建议采购={suggested_qty}, 采购价={purchase_price}, "
            "预计金额={estimated_amount}".format(**item)
        )


def seed(*, password: str = DEMO_PASSWORD) -> None:
    with SessionLocal() as db:
        company, _admin, purchaser = _seed_company_roles_and_staff(
            db, password=password
        )
        supplier, brand, unit = _seed_basic_data(db, company_id=company.id)
        product_a, product_b, sku_a, sku_b = _seed_products(
            db, supplier=supplier, brand=brand, unit=unit
        )
        warehouse, _location = _seed_warehouse_stock(
            db, sku_a=sku_a, sku_b=sku_b
        )
        shop = _seed_shop(db, supplier=supplier, warehouse=warehouse)
        _seed_orders(
            db,
            shop=shop,
            product_a=product_a,
            product_b=product_b,
            sku_a=sku_a,
            sku_b=sku_b,
        )
        _seed_purchase_data(
            db,
            supplier=supplier,
            warehouse=warehouse,
            purchaser=purchaser,
            product_a=product_a,
            sku_a=sku_a,
        )
        db.commit()

        print("演示数据已写入数据库。")
        print(f"公司：{company.full_name}（ID={company.id}）")
        print("登录账号：")
        print(f"  ADMIN      13800000001 / {password}")
        print(f"  PURCHASER  13800000002 / {password}")
        print(f"演示供应商 ID：{supplier.id}")
        _print_preview(db, supplier_id=supplier.id)


def main() -> int:
    parser = argparse.ArgumentParser(description="写入 ERP 演示数据")
    parser.add_argument(
        "--password",
        default=DEMO_PASSWORD,
        help=f"演示账号密码，默认 {DEMO_PASSWORD}",
    )
    args = parser.parse_args()
    seed(password=args.password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
