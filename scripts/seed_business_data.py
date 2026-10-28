"""为本地 ERP 数据库写入覆盖六大业务模块的测试数据（每个主要实体 ≥10 条）。

覆盖范围（对应前端左侧菜单）：
    采购管理 / 商品管理 / 订单管理 / 库存管理 / 协同办公 / 财务管理
并补齐这些模块依赖的基础资料（供应商、客户、品牌、单位、分类、仓库、库位等）。

可重复执行：所有编码/单号以 ``TB-`` 前缀（或 ``测试`` 名称前缀）标识，
重复运行只做更新，不会重复插入。``--purge`` 可清除本脚本写入的全部数据。

运行方式（项目根目录）：
    backend\\.venv\\Scripts\\python.exe scripts\\seed_business_data.py
    backend\\.venv\\Scripts\\python.exe scripts\\seed_business_data.py --purge
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: F401
from app.db.session import SessionLocal
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.basic import Brand, Category, SpecType, SpecValue, Unit, UnitGroup, UnitGroupItem
from app.models.finance import (
    AccountSubject,
    Expense,
    Income,
    Invoice,
    Payment,
    ProjectCategory,
    Receipt,
    Settlement,
    Voucher,
    VoucherItem,
)
from app.models.order import (
    DeliveryList,
    ExchangeOrder,
    ExchangeOrderItem,
    LoadNo,
    Order,
    OrderItem,
    OrderShipment,
    OrderShipmentItem,
    PickList,
    PrintTemplate,
    ReturnOrder,
    ReturnOrderItem,
    ReturnRegister,
    Wave,
    WaveOrder,
    WaveStrategy,
    Waybill,
)
from app.models.org import Company, Department, Position, Staff
from app.models.party import Account, Customer, Supplier, SupplierPrice
from app.models.platform import AreaRule, FreightTemplate, Logistics, Platform, Shop, ShopApi
from app.models.product import ComboProduct, ComboProductItem, Product, ProductSku
from app.models.purchase import (
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanItem,
    PurchasePrepay,
    PurchaseReturn,
    PurchaseReturnItem,
)
from app.models.stock import (
    LocationAdjust,
    LocationAdjustItem,
    Putaway,
    ShopStock,
    StockCheck,
    StockCheckItem,
    StockCost,
    StockFlow,
    StockIn,
    StockInItem,
    StockOut,
    StockOutItem,
    StockSettlement,
    StockSetting,
    StockWarning,
    Transfer,
    TransferIn,
    TransferInItem,
    TransferItem,
    TransferOut,
    TransferOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse, Zone

P = "TB-"          # 编码前缀
N = "测试"          # 名称前缀
COUNT = 12         # 每个实体的目标条数（要求 ≥10）
D = Decimal

BRANDS = ["华光", "恒远", "嘉盛", "瑞达", "新程", "天弘", "博联", "正泰", "汇通", "万邦", "德昌", "立信"]
UNITS = ["件", "箱", "套", "台", "个", "双", "包", "桶", "对", "张"]
CATEGORIES = ["办公用品", "数码配件", "家用电器", "服饰鞋帽", "食品饮料", "仓储耗材"]
CITIES = [
    ("广东省", "深圳市"), ("浙江省", "杭州市"), ("江苏省", "南京市"),
    ("四川省", "成都市"), ("北京市", "北京市"), ("湖北省", "武汉市"),
]
PRODUCT_WORDS = [
    "无线鼠标", "机械键盘", "显示器支架", "USB 集线器", "移动硬盘",
    "蓝牙耳机", "桌面台灯", "收纳箱", "保温杯", "双肩背包",
    "笔记本支架", "网线",
]
SPEC_NAMES = ["颜色", "尺寸", "容量", "规格", "材质", "功率", "型号", "口味", "重量", "版本"]
SPEC_VALUES = {
    "颜色": ["黑色", "白色", "蓝色"],
    "尺寸": ["小号", "中号", "大号"],
    "容量": ["500ml", "1L", "2L"],
    "规格": ["单只装", "两只装", "套装"],
    "材质": ["ABS", "铝合金", "不锈钢"],
    "功率": ["20W", "45W", "65W"],
    "型号": ["基础款", "标准款", "旗舰款"],
    "口味": ["原味", "香辣", "五香"],
    "重量": ["1kg", "2kg", "5kg"],
    "版本": ["标准版", "专业版", "企业版"],
}
LOGISTICS = ["顺丰速运", "中通快递", "圆通速递", "韵达快递", "京东物流",
             "申通快递", "百世快递", "德邦物流", "邮政 EMS", "极兔速递"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(days: int, hour: int = 10) -> datetime:
    base = _now() - timedelta(days=days)
    return base.replace(hour=hour, minute=(days * 7) % 60, second=0, microsecond=0)


def _upsert(db: Session, model: type[Any], lookup: dict, values: dict) -> tuple[Any, bool]:
    stmt = select(model)
    for key, value in lookup.items():
        stmt = stmt.where(getattr(model, key) == value)
    obj = db.scalar(stmt)
    if obj is None:
        obj = model(**lookup, **values)
        db.add(obj)
        db.flush()
        return obj, True
    for key, value in values.items():
        setattr(obj, key, value)
    db.flush()
    return obj, False


class Counter:
    def __init__(self) -> None:
        self.created = 0
        self.updated = 0

    def hit(self, is_new: bool) -> None:
        if is_new:
            self.created += 1
        else:
            self.updated += 1


def _u(db: Session, model, lookup: dict, values: dict, counter: Counter):
    obj, is_new = _upsert(db, model, lookup, values)
    counter.hit(is_new)
    return obj


# --------------------------------------------------------------- 基础资料
def _seed_base(db: Session, ctx: dict, c: Counter) -> None:
    company_id = ctx["company_id"]
    staff_ids = ctx["staff_ids"]

    departments = []
    for i in range(1, COUNT - 1):
        departments.append(
            _u(db, Department,
               {"company_id": company_id, "name": f"{N}部门{i:02d}"},
               {"sort_order": i}, c)
        )
    for i in range(1, COUNT - 1):
        _u(db, Position, {"company_id": company_id, "name": f"{N}岗位{i:02d}"}, {}, c)

    brands = [
        _u(db, Brand, {"name": f"{N}品牌{i:02d}"}, {"image": None}, c)
        for i in range(1, COUNT + 1)
    ]
    units = [_u(db, Unit, {"name": name}, {"code": f"{P}U{i:02d}"}, c)
             for i, name in enumerate(UNITS, start=1)]

    roots = []
    for i, name in enumerate(CATEGORIES, start=1):
        roots.append(_u(db, Category, {"name": f"{name}"}, {"parent_id": None, "sort_order": i}, c))
    leaves = []
    for i, root in enumerate(roots, start=1):
        for j in (1, 2):
            leaves.append(
                _u(db, Category, {"name": f"{root.name}-子类{j}"},
                   {"parent_id": root.id, "sort_order": j}, c)
            )

    spec_types = []
    for i, name in enumerate(SPEC_NAMES, start=1):
        spec_types.append(
            _u(db, SpecType, {"name": name}, {"category_id": roots[i % len(roots)].id}, c)
        )
    for st in spec_types:
        for k, value in enumerate(SPEC_VALUES[st.name], start=1):
            _u(db, SpecValue, {"spec_type_id": st.id, "code": f"{P}{st.name}-{k:02d}"},
               {"name": value, "sort_order": k}, c)

    suppliers = []
    for i in range(1, COUNT + 1):
        province, city = CITIES[i % len(CITIES)]
        suppliers.append(
            _u(db, Supplier, {"code": f"{P}SUP-{i:03d}"},
               {"name": f"{N}供应商{i:02d}", "contact": f"联系人{i:02d}",
                "phone": f"1380{i:07d}", "address": f"{province}{city}工业园区{i}号",
                "settlement_type": "MONTHLY" if i % 2 else "CASH",
                "email": f"sup{i:02d}@test.local", "remark": f"{N}数据", "is_active": True}, c)
        )
    customers = []
    for i in range(1, COUNT + 1):
        province, city = CITIES[(i + 2) % len(CITIES)]
        customers.append(
            _u(db, Customer, {"code": f"{P}CUS-{i:03d}"},
               {"name": f"{N}客户{i:02d}", "contact": f"客户联系人{i:02d}",
                "phone": f"1390{i:07d}", "address": f"{province}{city}商务区{i}号",
                "settlement_type": "MONTHLY" if i % 3 else "CASH",
                "email": f"cus{i:02d}@test.local", "remark": f"{N}数据", "is_active": True}, c)
        )

    logistics = [
        _u(db, Logistics, {"name": f"{N}物流-{name}"},
           {"company": name, "contact": f"物流联系人{i:02d}", "phone": f"1370{i:07d}",
            "address": "物流园 1 号", "is_default": i == 1, "is_enabled": True}, c)
        for i, name in enumerate(LOGISTICS, start=1)
    ]
    platforms = [
        _u(db, Platform, {"name": f"{N}平台{i:02d}"}, {"code": f"{P}PLAT-{i:02d}"}, c)
        for i in range(1, COUNT + 1)
    ]
    shops = []
    for i in range(1, COUNT - 1):
        shops.append(
            _u(db, Shop, {"name": f"{N}店铺{i:02d}"},
               {"platform_id": platforms[i % len(platforms)].id,
                "login_account": f"tb_shop_{i:02d}", "url": f"https://shop{i:02d}.test.local",
                "tag": "测试", "supplier_id": suppliers[i % len(suppliers)].id,
                "default_address": "总仓发货", "is_locked": False,
                "auto_sync_order": i % 2 == 0, "auto_sync_stock": i % 3 == 0,
                "download_mode": "API", "enable_logistics_params": False}, c)
        )

    warehouses = []
    for i in range(1, COUNT + 1):
        warehouses.append(
            _u(db, Warehouse, {"code": f"{P}WH-{i:02d}"},
               {"name": f"{N}仓库{i:02d}", "region": CITIES[i % len(CITIES)][1],
                "address": f"{CITIES[i % len(CITIES)][0]}{CITIES[i % len(CITIES)][1]}仓储路{i}号",
                "warehouse_type": "FINISHED" if i <= COUNT - 2 else "DEFECTIVE",
                "is_default": False, "sync_stock": True, "enable_location": True,
                "zero_stock_out": False, "is_virtual": False, "is_enabled": True,
                "lock_type": "NONE", "remark": f"{N}数据"}, c)
        )

    locations: list[Location] = []
    for wh in warehouses[:6]:
        for z in range(1, 3):
            zone = _u(db, Zone, {"warehouse_id": wh.id, "code": f"{P}Z-{wh.id}-{z:02d}"},
                      {"name": f"{wh.name} 库区{z}", "sort_order": z}, c)
            for r in range(1, 3):
                locations.append(
                    _u(db, Location, {"code": f"{wh.code}-{zone.code}-{r:02d}"},
                       {"warehouse_id": wh.id, "zone_id": zone.id, "row_no": r, "col_no": 1,
                        "layer_no": 1, "capacity": 1000, "priority": r, "sort_order": r,
                        "is_virtual": False, "is_enabled": True}, c)
                )

    ctx.update(
        brands=brands, units=units, leaves=leaves, suppliers=suppliers,
        customers=customers, logistics=logistics, platforms=platforms,
        shops=shops, warehouses=warehouses, locations=locations,
        departments=departments, staff_ids=staff_ids,
    )


# --------------------------------------------------------------- 商品管理
def _seed_products(db: Session, ctx: dict, c: Counter) -> None:
    products: list[Product] = []
    skus: list[ProductSku] = []
    for i in range(1, COUNT + 1):
        cost = D(6 + i)
        product = _u(db, Product, {"code": f"{P}P-{i:03d}"},
                     {"name": f"{N}商品-{PRODUCT_WORDS[(i - 1) % len(PRODUCT_WORDS)]}{i:02d}",
                      "art_no": f"ART{i:05d}",
                      "category_id": ctx["leaves"][i % len(ctx["leaves"])].id,
                      "brand_id": ctx["brands"][i % len(ctx["brands"])].id,
                      "default_supplier_id": ctx["suppliers"][i % len(ctx["suppliers"])].id,
                      "measure_type": "SINGLE",
                      "main_unit_id": ctx["units"][i % len(ctx["units"])].id,
                      "online_price": cost * D("2.6"), "tag_price": cost * D("3"),
                      "min_price": cost * D("1.8"), "cost_price": cost,
                      "weight": D("0.5") + D(i) / D(10),
                      "min_stock": 10, "max_stock": 5000, "warn_stock": 20 + i,
                      "tax_rate": D("0.13"), "barcode": f"69{i:011d}",
                      "is_gift": False, "use_float_cost": False, "is_disabled": False,
                      "listed_at": _days_ago(120 - i), "description": f"{N}商品说明 {i}"}, c)
        products.append(product)
        for k in (1, 2):
            skus.append(
                _u(db, ProductSku, {"spec_code": f"{P}P-{i:03d}-{k:02d}"},
                   {"product_id": product.id, "spec_name": f"{'标准' if k == 1 else '升级'}款",
                    "barcode": f"69{i:06d}{k:04d}", "tag_price": cost * D("3"),
                    "sale_price": cost * D("2.6") + D(k - 1), "cost_price": cost,
                    "min_price": cost * D("1.8"), "is_active": True}, c)
            )
            _u(db, SupplierPrice,
               {"supplier_id": product.default_supplier_id, "product_id": product.id,
                "sku_id": skus[-1].id},
               {"price": cost, "status": "ACTIVE"}, c)

    for i in range(1, COUNT - 1):
        combo = _u(db, ComboProduct, {"combo_no": f"{P}CB-{i:03d}"},
                   {"name": f"{N}组合商品{i:02d}", "main_product_id": products[i].id,
                    "main_sku_id": skus[i * 2].id, "is_enabled": True,
                    "remark": f"{N}数据", "created_by": ctx["staff_ids"][0]}, c)
        for order, offset in enumerate((0, 2, 4), start=1):
            _u(db, ComboProductItem,
               {"combo_id": combo.id, "product_id": products[(i + offset) % len(products)].id,
                "sku_id": skus[((i + offset) % len(products)) * 2].id},
               {"qty": D(order), "sort_order": order}, c)

    ctx.update(products=products, skus=skus)


# --------------------------------------------------------------- 采购管理
def _seed_purchase(db: Session, ctx: dict, c: Counter) -> None:
    products, skus = ctx["products"], ctx["skus"]
    suppliers, warehouses = ctx["suppliers"], ctx["warehouses"]
    staff_ids = ctx["staff_ids"]

    def _lines(count: int, seed: int) -> list[dict]:
        lines = []
        for offset in range(count):
            idx = (seed + offset) % len(products)
            qty = D(5 + (seed + offset) % 20)
            price = products[idx].cost_price or D("10")
            lines.append({"product": products[idx], "sku": skus[idx * 2],
                          "qty": qty, "price": price, "amount": qty * price})
        return lines

    plans = []
    for i in range(1, COUNT + 1):
        lines = _lines(1 + i % 3, i)
        plan = _u(db, PurchasePlan, {"plan_no": f"{P}PP-{i:04d}"},
                  {"supplier_id": suppliers[i % len(suppliers)].id,
                   "plan_date": _days_ago(90 - i * 2),
                   "status": ("FINISHED" if i % 3 == 0 else "IN_PROGRESS"),
                   "audit_status": ("APPROVED" if i % 4 else "PENDING"),
                   "total_qty": sum(l["qty"] for l in lines),
                   "total_amount": sum(l["amount"] for l in lines),
                   "remark": f"{N}采购计划 {i}", "created_by": staff_ids[i % len(staff_ids)],
                   "auditor_id": staff_ids[0] if i % 4 == 0 else None,
                   "audit_time": _days_ago(88 - i * 2) if i % 4 == 0 else None}, c)
        plans.append(plan)
        for line in lines:
            _u(db, PurchasePlanItem,
               {"plan_id": plan.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"planned_qty": line["qty"], "purchased_qty": line["qty"] if i % 3 == 0 else D("0"),
                "purchase_price": line["price"], "amount": line["amount"]}, c)

    orders = []
    for i in range(1, COUNT + 1):
        lines = _lines(1 + i % 3, i)
        total_qty = sum(l["qty"] for l in lines)
        total_amount = sum(l["amount"] for l in lines)
        received = i % 3 == 0
        order = _u(db, PurchaseOrder, {"order_no": f"{P}PO-{i:04d}"},
                   {"plan_id": plans[i - 1].id, "supplier_id": suppliers[i % len(suppliers)].id,
                    "warehouse_id": warehouses[i % len(warehouses)].id,
                    "purchaser_id": staff_ids[i % len(staff_ids)],
                    "status": "RECEIVED" if received else ("APPROVED" if i % 2 else "PENDING"),
                    "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
                    "total_qty": total_qty, "total_amount": total_amount,
                    "arrived_qty": total_qty if received else D("0"),
                    "prepay_amount": (total_amount * D("0.3")).quantize(D("0.01")) if i % 4 == 0 else D("0"),
                    "description": f"{N}采购订单 {i}",
                    "created_by": staff_ids[i % len(staff_ids)],
                    "auditor_id": staff_ids[0] if i % 2 == 0 else None,
                    "audit_time": _days_ago(80 - i * 2) if i % 2 == 0 else None}, c)
        orders.append(order)
        for line in lines:
            _u(db, PurchaseOrderItem,
               {"order_id": order.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"brand_id": line["product"].brand_id, "unit_id": line["product"].main_unit_id,
                "purchase_price": line["price"], "qty": line["qty"],
                "in_transit_qty": D("0") if received else line["qty"],
                "arrived_qty": line["qty"] if received else D("0"),
                "amount": line["amount"]}, c)

    for i in range(1, COUNT + 1):
        order = orders[i - 1]
        _u(db, PurchasePrepay, {"prepay_no": f"{P}PRE-{i:04d}"},
           {"supplier_id": order.supplier_id, "order_id": order.id,
            "order_amount": order.total_amount,
            "prepay_amount": (order.total_amount * D("0.3")).quantize(D("0.01")),
            "write_off_amount": D("0") if i % 3 else (order.total_amount * D("0.1")).quantize(D("0.01")),
            "bank": "工商银行", "account_no": f"6222{i:012d}",
            "account_balance": D("100000"), "pay_note": f"{N}预付款 {i}",
            "status": "APPROVED" if i % 2 == 0 else "PENDING",
            "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    for i in range(1, COUNT + 1):
        order = orders[i - 1]
        lines = _lines(1 + i % 2, i + 1)
        qty = sum(l["qty"] for l in lines) / D("2")
        refund = (sum(l["amount"] for l in lines) / D("2")).quantize(D("0.01"))
        ret = _u(db, PurchaseReturn, {"return_no": f"{P}PR-{i:04d}"},
                 {"supplier_id": order.supplier_id, "order_id": order.id,
                  "warehouse_id": order.warehouse_id, "auto_out": i % 2 == 0,
                  "return_reason": "质量问题" if i % 2 else "多订退回",
                  "description": f"{N}采购退货 {i}", "total_qty": qty,
                  "refund_amount": refund,
                  "returned_qty": qty if i % 3 == 0 else D("0"),
                  "status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "created_by": staff_ids[i % len(staff_ids)]}, c)
        for line in lines:
            _u(db, PurchaseReturnItem,
               {"return_id": ret.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"unit_id": line["product"].main_unit_id, "unit_price": line["price"],
                "return_qty": line["qty"] / D("2"),
                "amount": (line["amount"] / D("2")).quantize(D("0.01"))}, c)

    invoices = []
    for i in range(1, COUNT + 1):
        order = orders[i - 1]
        amount = order.total_amount
        written = amount if i % 3 == 0 else (amount / D("2") if i % 3 == 1 else D("0"))
        inv = _u(db, PurchaseInvoice, {"invoice_no": f"{P}PINV-{i:04d}"},
                 {"supplier_id": order.supplier_id,
                  "invoice_type": "SPECIAL" if i % 2 else "NORMAL",
                  "tax_rate": D("0.13") if i % 2 else D("0"),
                  "amount": amount, "tax_amount": (amount * D("0.13") / D("1.13")).quantize(D("0.01")),
                  "write_off_amount": written,
                  "write_off_status": "FULL" if written == amount else ("PARTIAL" if written else "NONE"),
                  "business_type": "采购", "description": f"{N}采购发票 {i}",
                  "status": "APPROVED" if i % 2 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
                  "created_by": staff_ids[i % len(staff_ids)]}, c)
        invoices.append(inv)

    for i in range(1, COUNT + 1):
        order = orders[i - 1]
        _u(db, Payment, {"payment_no": f"{P}PAY-{i:04d}"},
           {"supplier_id": order.supplier_id, "invoice_id": invoices[i - 1].id,
            "payment_type": "采购付款", "pay_method": "BANK" if i % 2 else "CASH",
            "amount": order.total_amount,
            "status": "PAID" if i % 2 == 0 else "PENDING",
            "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    for i in range(1, COUNT + 1):
        _u(db, Income, {"income_no": f"{P}INC-{i:04d}"},
           {"customer_id": ctx["customers"][i % len(ctx["customers"])].id,
            "supplier_id": suppliers[i % len(suppliers)].id,
            "income_type": "其他收入" if i % 2 else "补贴收入",
            "amount": D(100 * i) + D("50"), "description": f"{N}收入单 {i}",
            "status": "CONFIRMED" if i % 2 == 0 else "PENDING",
            "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    ctx.update(purchase_orders=orders, purchase_invoices=invoices)


# --------------------------------------------------------------- 库存管理
def _seed_stock(db: Session, ctx: dict, c: Counter) -> None:
    products, skus = ctx["products"], ctx["skus"]
    warehouses, locations = ctx["warehouses"], ctx["locations"]
    staff_ids = ctx["staff_ids"]

    def _lines(count: int, seed: int) -> list[dict]:
        lines = []
        for offset in range(count):
            idx = (seed + offset) % len(products)
            qty = D(3 + (seed + offset) % 15)
            cost = products[idx].cost_price or D("10")
            lines.append({"product": products[idx], "sku": skus[idx * 2],
                          "qty": qty, "cost": cost, "amount": qty * cost})
        return lines

    stock_ins = []
    for i in range(1, COUNT + 1):
        lines = _lines(1 + i % 3, i)
        wh = warehouses[i % len(warehouses)]
        doc = _u(db, StockIn, {"stock_in_no": f"{P}IN-{i:04d}"},
                 {"warehouse_id": wh.id, "stock_in_type": "PURCHASE" if i % 2 else "OTHER",
                  "status": "FINISHED" if i % 2 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
                  "total_qty": sum(l["qty"] for l in lines), "remark": f"{N}入库单 {i}",
                  "created_by": staff_ids[i % len(staff_ids)]}, c)
        stock_ins.append(doc)
        for line in lines:
            _u(db, StockInItem,
               {"stock_in_id": doc.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"qty": line["qty"], "cost_price": line["cost"], "amount": line["amount"],
                "barcode": line["sku"].barcode}, c)

    for i in range(1, COUNT + 1):
        lines = _lines(1 + i % 2, i + 3)
        wh = warehouses[i % len(warehouses)]
        doc = _u(db, StockOut, {"stock_out_no": f"{P}OUT-{i:04d}"},
                 {"warehouse_id": wh.id, "stock_out_type": "SALE" if i % 2 else "PURCHASE_RETURN",
                  "status": "FINISHED" if i % 3 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "off_shelf_status": "DONE" if i % 3 == 0 else "NONE",
                  "total_qty": sum(l["qty"] for l in lines), "remark": f"{N}出库单 {i}",
                  "created_by": staff_ids[i % len(staff_ids)]}, c)
        for line in lines:
            loc = locations[i % len(locations)]
            _u(db, StockOutItem,
               {"stock_out_id": doc.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"location_id": loc.id, "qty": line["qty"], "cost_price": line["cost"],
                "barcode": line["sku"].barcode}, c)

    transfers = []
    for i in range(1, COUNT + 1):
        lines = _lines(1 + i % 2, i + 5)
        src = warehouses[i % len(warehouses)]
        dst = warehouses[(i + 3) % len(warehouses)]
        doc = _u(db, Transfer, {"transfer_no": f"{P}TR-{i:04d}"},
                 {"from_warehouse_id": src.id, "to_warehouse_id": dst.id,
                  "sync_stock": i % 2 == 0,
                  "status": "FINISHED" if i % 3 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "total_qty": sum(l["qty"] for l in lines), "remark": f"{N}调拨单 {i}",
                  "created_by": staff_ids[i % len(staff_ids)]}, c)
        transfers.append(doc)
        for line in lines:
            _u(db, TransferItem,
               {"transfer_id": doc.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"qty": line["qty"], "barcode": line["sku"].barcode}, c)

        out = _u(db, TransferOut, {"transfer_out_no": f"{P}TRO-{i:04d}"},
                 {"transfer_id": doc.id, "warehouse_id": src.id,
                  "status": "FINISHED" if i % 3 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "remark": f"{N}调拨出库 {i}", "created_by": staff_ids[i % len(staff_ids)]}, c)
        inn = _u(db, TransferIn, {"transfer_in_no": f"{P}TRI-{i:04d}"},
                 {"transfer_id": doc.id, "warehouse_id": dst.id,
                  "status": "FINISHED" if i % 3 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
                  "remark": f"{N}调拨入库 {i}", "created_by": staff_ids[i % len(staff_ids)]}, c)
        for line in lines:
            loc = locations[i % len(locations)]
            _u(db, TransferOutItem,
               {"transfer_out_id": out.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"location_id": loc.id, "qty": line["qty"]}, c)
            _u(db, TransferInItem,
               {"transfer_in_id": inn.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"location_id": loc.id, "qty": line["qty"]}, c)

    for i in range(1, COUNT + 1):
        wh = warehouses[i % len(warehouses)]
        lines = _lines(1 + i % 2, i + 7)
        doc = _u(db, LocationAdjust, {"adjust_no": f"{P}ADJ-{i:04d}"},
                 {"warehouse_id": wh.id,
                  "status": "FINISHED" if i % 2 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
                  "remark": f"{N}库存调整 {i}", "created_by": staff_ids[i % len(staff_ids)]}, c)
        for line in lines:
            src = locations[i % len(locations)]
            dst = locations[(i + 5) % len(locations)]
            _u(db, LocationAdjustItem,
               {"adjust_id": doc.id, "product_id": line["product"].id, "sku_id": line["sku"].id,
                "from_location_id": src.id, "to_location_id": dst.id},
               {"qty": line["qty"]}, c)

    for i in range(1, COUNT + 1):
        wh = warehouses[i % len(warehouses)]
        lines = _lines(1 + i % 2, i + 9)
        doc = _u(db, StockCheck, {"check_no": f"{P}CHK-{i:04d}"},
                 {"warehouse_id": wh.id, "lock_warehouse": i % 3 == 0,
                  "status": "FINISHED" if i % 2 == 0 else "PENDING",
                  "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
                  "remark": f"{N}库存盘点 {i}", "created_by": staff_ids[i % len(staff_ids)]}, c)
        for line in lines:
            _u(db, StockCheckItem,
               {"check_id": doc.id, "product_id": line["product"].id, "sku_id": line["sku"].id},
               {"location_id": locations[i % len(locations)].id,
                "current_qty": line["qty"],
                "actual_qty": line["qty"] if i % 2 == 0 else line["qty"] - D("1"),
                "barcode": line["sku"].barcode}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        wh = warehouses[i % len(warehouses)]
        loc = locations[i % len(locations)]
        qty = D(2 + i)
        cost = product.cost_price or D("10")
        _u(db, StockFlow, {"source_no": f"{P}FLOW-{i:04d}", "product_id": product.id,
                           "sku_id": sku.id, "direction": "IN" if i % 2 else "OUT"},
           {"warehouse_id": wh.id, "location_id": loc.id, "unit_id": product.main_unit_id,
            "biz_type": "PURCHASE_IN" if i % 2 else "SALE_OUT", "qty": qty,
            "cost_price": cost, "total_cost": qty * cost,
            "order_no": None, "flow_time": _days_ago(60 - i * 3), "remark": f"{N}库存流水 {i}"}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        _u(db, StockWarning, {"product_id": product.id, "sku_id": sku.id},
           {"current_qty": D(1 + i % 5), "warn_qty": D(20)}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        qty = D(50 + i * 3)
        cost = product.cost_price or D("10")
        _u(db, StockCost, {"product_id": product.id, "sku_id": sku.id},
           {"cost_price": cost, "qty": qty, "total_cost": qty * cost}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        _u(db, StockSettlement, {"settlement_date": _days_ago(i * 3).replace(hour=0, minute=0),
                                 "sku_id": sku.id},
           {"product_id": product.id, "qty": D(100 + i * 5),
            "cost_price": product.cost_price or D("10")}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        _u(db, ShopStock,
           {"shop_id": ctx["shops"][i % len(ctx["shops"])].id,
            "warehouse_id": warehouses[i % len(warehouses)].id, "sku_id": sku.id},
           {"product_id": product.id, "exclusive_qty": D(5 + i % 10),
            "is_sync": i % 2 == 0, "is_enabled": True,
            "effective_start": _days_ago(30), "effective_end": _days_ago(-30),
            "last_sync_qty": D(5 + i % 10), "last_sync_at": _days_ago(1),
            "remark": f"{N}店铺库存 {i}", "created_by": staff_ids[0]}, c)

    for i in range(1, COUNT + 1):
        product, sku = products[i % len(products)], skus[(i % len(products)) * 2]
        loc = locations[i % len(locations)]
        _u(db, StockLocation, {"location_id": loc.id, "product_sku_id": sku.id, "status": "NORMAL"},
           {"qty": D(20 + i * 2)}, c)

    for i, doc in enumerate(stock_ins, start=1):
        line = _lines(1, i)[0]
        _u(db, Putaway,
           {"source_type": "STOCK_IN", "source_id": doc.id,
            "location_id": locations[i % len(locations)].id,
            "product_id": line["product"].id, "sku_id": line["sku"].id},
           {"warehouse_id": doc.warehouse_id, "qty": line["qty"],
            "operator_id": staff_ids[i % len(staff_ids)]}, c)


# --------------------------------------------------- 订单补充：波次与退货
def _seed_order_extras(db: Session, ctx: dict, c: Counter) -> None:
    orders = list(db.scalars(select(Order).order_by(Order.id)))
    if not orders:
        return
    strategies = [
        _u(db, WaveStrategy, {"name": f"{N}波次策略{i:02d}"},
           {"logistics_id": ctx["logistics"][i % len(ctx["logistics"])].id,
            "warehouse_id": ctx["warehouses"][i % len(ctx["warehouses"])].id,
            "shop_ids": None, "wave_order_count": 5 + i, "load_no_type": "SINGLE",
            "sort_type": "BY_LOCATION", "is_enabled": True, "description": f"{N}数据"}, c)
        for i in range(1, COUNT - 1)
    ]
    for i in range(1, COUNT + 1):
        wave = _u(db, Wave, {"wave_no": f"{P}WAVE-{i:04d}"},
                  {"strategy_id": strategies[i % len(strategies)].id,
                   "name": f"{N}波次{i:02d}", "express_type": "NORMAL",
                   "total_order_count": 2, "created_by": ctx["staff_ids"][0]}, c)
        for k in range(2):
            order = orders[(i * 2 + k) % len(orders)]
            _u(db, WaveOrder, {"order_id": order.id},
               {"wave_id": wave.id, "sequence": k + 1, "load_no": None,
                "sort_key": f"{order.order_no}"}, c)

    for i in range(1, COUNT + 1):
        order = orders[i % len(orders)]
        ret = _u(db, ReturnOrder, {"return_no": f"{P}RO-{i:04d}"},
                 {"order_id": order.id, "shop_id": order.shop_id,
                  "warehouse_id": ctx["warehouses"][i % len(ctx["warehouses"])].id,
                  "status": "FINISHED" if i % 2 == 0 else "PENDING",
                  "refund_amount": D(20 * i), "return_qty": D(1 + i % 3),
                  "logistics_company": LOGISTICS[i % len(LOGISTICS)],
                  "tracking_no": f"{P}RT{i:010d}", "reason": "七天无理由" if i % 2 else "商品破损",
                  "buyer_phone": f"1390{i:07d}", "is_arrived": i % 2 == 0,
                  "freight": D("0.00"), "is_rejected_sign": False,
                  "return_person": f"{N}退货人{i:02d}", "goods_status": "GOOD" if i % 2 else "DAMAGED",
                  "need_return_goods": True, "audit_time": _days_ago(20 - i)}, c)
        for k in range(2):
            product = ctx["products"][(i + k) % len(ctx["products"])]
            _u(db, ReturnOrderItem,
               {"return_order_id": ret.id, "product_id": product.id,
                "sku_id": ctx["skus"][((i + k) % len(ctx["products"])) * 2].id},
               {"qty": D(1 + k), "refund_amount": D(10 * (i + k + 1))}, c)


# ---------------------------------------------- 订单履约流程与设置支持表
def _seed_extras(db: Session, ctx: dict, c: Counter) -> None:
    staff_ids = ctx["staff_ids"]
    warehouses = ctx["warehouses"]
    shops = ctx["shops"]

    for i in range(1, COUNT - 1):
        group = _u(db, UnitGroup, {"name": f"{N}单位组{i:02d}"},
                   {"main_unit_id": ctx["units"][0].id}, c)
        for k, unit in enumerate(ctx["units"][:3], start=1):
            _u(db, UnitGroupItem, {"unit_group_id": group.id, "unit_id": unit.id},
               {"conversion_rate": D(k)}, c)

    templates = []
    for i in range(1, COUNT + 1):
        templates.append(
            _u(db, FreightTemplate, {"name": f"{N}运费模板{i:02d}"},
               {"logistics_id": ctx["logistics"][i % len(ctx["logistics"])].id,
                "charge_type": "BY_COUNT" if i % 2 else "BY_WEIGHT",
                "first_count": 1, "continue_count": 1,
                "first_weight": D("1.000"), "continue_weight": D("0.500"),
                "base_fee": D("8.00"), "continue_fee": D("2.00"),
                "deduct_weight": D("0.000"), "operation_fee": D("1.00"),
                "consumable_fee": D("0.50"), "ewaybill_fee": D("0.30"),
                "tax_fee": D("0.00"), "tax_point": D("0.06")}, c)
        )
        province, city = CITIES[i % len(CITIES)]
        _u(db, AreaRule, {"logistics_id": templates[-1].logistics_id, "province": province,
                          "city": city, "freight_template_id": templates[-1].id},
           {"match_weight_min": D("0.000"), "match_weight_max": D("30.000")}, c)

    for i in range(1, COUNT + 1):
        supplier = ctx["suppliers"][i % len(ctx["suppliers"])]
        _u(db, Account, {"supplier_id": supplier.id, "account_no": f"6222{i:012d}"},
           {"customer_id": None, "bank": "中国银行", "branch": f"{CITIES[i % len(CITIES)][1]}分行",
            "bank_no": f"1041{i:08d}", "address": "开户行地址",
            "is_default": i % 3 == 0, "balance": D(10000 * i),
            "kingdee_sync_status": "NOT_SYNCED"}, c)

    for i, shop in enumerate(shops[:COUNT], start=1):
        _u(db, ShopApi, {"shop_id": shop.id},
           {"api_code": f"{P}API-{i:04d}", "verify_code": f"verify{i:04d}",
            "app_key": f"key{i:012d}", "app_secret": f"secret{i:012d}"}, c)
        _u(db, PrintTemplate, {"name": f"{N}打印模板{i:02d}"},
           {"template_type": ["WAYBILL", "PICK_LIST", "DELIVERY", "INVOICE"][i % 4],
            "logistics_id": ctx["logistics"][i % len(ctx["logistics"])].id,
            "content": "<html><body>{{order_no}}</body></html>",
            "paper_width": D("100.00"), "paper_height": D("150.00"),
            "is_default": i == 1}, c)

    waves = list(db.scalars(select(Wave).order_by(Wave.id)))
    for i, wave in enumerate(waves[:COUNT], start=1):
        load_no = f"{P}LOAD-{i:04d}"
        _u(db, LoadNo, {"wave_id": wave.id, "load_no": load_no},
           {"express_type": "NORMAL"}, c)
        _u(db, PickList, {"batch_no": f"{P}PICK-{i:04d}"},
           {"wave_id": wave.id, "load_no": load_no, "content": f"{N}拣货清单 {i}"}, c)
        _u(db, DeliveryList, {"batch_no": f"{P}DLV-{i:04d}"},
           {"wave_id": wave.id, "load_no": load_no, "content": f"{N}发货清单 {i}"}, c)

    orders = list(db.scalars(select(Order).order_by(Order.id)))
    items_by_order: dict[int, list[OrderItem]] = {}
    for item in db.scalars(select(OrderItem).order_by(OrderItem.id)):
        items_by_order.setdefault(item.order_id, []).append(item)

    shipped = [o for o in orders if o.status in ("SHIPPED", "COMPLETED")]
    for i, order in enumerate(shipped[:COUNT], start=1):
        shipment = _u(db, OrderShipment, {"shipment_no": f"{P}SHIP-{i:04d}"},
                      {"order_id": order.id,
                       "warehouse_id": order.warehouse_id or warehouses[0].id,
                       "package_no": 1, "tracking_no": f"{P}TRK{i:012d}",
                       "shipping_type": "EXPRESS", "source": "SCAN", "status": "SHIPPED",
                       "expected_weight_g": D("1500.000"), "actual_weight_g": D("1520.000"),
                       "weight_tolerance_g": D("200.000"), "weight_out_of_range": False,
                       "weight_overridden": False, "platform_sync_status": "SYNCED",
                       "shipped_by": staff_ids[i % len(staff_ids)],
                       "shipped_at": _days_ago(30 - i), "remark": f"{N}发货包裹 {i}"}, c)
        for item in items_by_order.get(order.id, [])[:2]:
            _u(db, OrderShipmentItem,
               {"shipment_id": shipment.id, "order_item_id": item.id},
               {"product_id": item.product_id, "sku_id": item.sku_id, "qty": item.qty,
                "cost_price": None, "barcode": None}, c)
        _u(db, Waybill, {"order_id": order.id, "batch_no": f"{P}WB-{i:04d}"},
           {"waybill_no": f"{P}WB{i:012d}", "print_type": "THERMAL", "is_printed": i % 3 != 0}, c)

    for i in range(1, COUNT + 1):
        order = orders[i % len(orders)]
        exchange = _u(db, ExchangeOrder, {"exchange_no": f"{P}EX-{i:04d}"},
                      {"order_id": order.id, "shop_id": order.shop_id or shops[0].id,
                       "status": "FINISHED" if i % 2 == 0 else "PENDING",
                       "exchange_qty": D(1 + i % 3),
                       "reason": "尺码不符" if i % 2 else "质量问题",
                       "is_cod": False, "is_return": i % 3 == 0,
                       "compensate_order_no": f"{P}CP-{i:04d}" if i % 4 == 0 else None,
                       "compensate_amount": D(10 * i) if i % 4 == 0 else None,
                       "compensate_direction": "TO_CUSTOMER" if i % 4 == 0 else None}, c)
        for k in range(2):
            product = ctx["products"][(i + k) % len(ctx["products"])]
            _u(db, ExchangeOrderItem,
               {"exchange_order_id": exchange.id, "product_id": product.id,
                "sku_id": ctx["skus"][((i + k) % len(ctx["products"])) * 2].id},
               {"qty": D(1 + k), "exchange_qty": D(1 + k)}, c)

    returns = list(db.scalars(select(ReturnOrder).order_by(ReturnOrder.id)))
    for i, ret in enumerate(returns[:COUNT], start=1):
        _u(db, ReturnRegister, {"return_order_id": ret.id},
           {"exchange_order_id": None,
            "logistics_company": LOGISTICS[i % len(LOGISTICS)],
            "tracking_no": f"{P}RR{i:010d}", "sender": f"{N}寄件人{i:02d}",
            "sender_phone": f"1390{i:07d}", "product_info": f"{N}退货商品信息 {i}",
            "image": None}, c)

    _u(db, StockSetting, {"company_id": ctx["company_id"]},
       {"sync_stock_default": True, "shop_stock_enabled": True,
        "floating_cost_enabled": True, "off_shelf_below_warning": False}, c)


# --------------------------------------------------------------- 协同办公
def _seed_approval(db: Session, ctx: dict, c: Counter) -> None:
    doc_types = ["PURCHASE_PLAN", "PURCHASE_ORDER", "PURCHASE_PREPAY", "PURCHASE_RETURN",
                 "PURCHASE_INVOICE", "PAYMENT", "RECEIPT", "EXPENSE", "STOCK_CHECK", "TRANSFER"]
    for i, doc_type in enumerate(doc_types, start=1):
        _u(db, ApprovalFlow, {"name": f"{N}审批流-{doc_type}"},
           {"doc_type": doc_type,
            "approvers": f"[{ctx['staff_ids'][0]}, {ctx['staff_ids'][1]}]",
            "created_by": ctx["staff_ids"][0]}, c)


# --------------------------------------------------------------- 财务管理
def _seed_finance(db: Session, ctx: dict, c: Counter) -> None:
    staff_ids = ctx["staff_ids"]

    categories = [
        _u(db, ProjectCategory, {"code": f"{P}PC-{i:02d}"},
           {"name": f"{N}项目类别{i:02d}", "remark": f"{N}数据"}, c)
        for i in range(1, COUNT + 1)
    ]

    subject_defs = [
        ("1001", "库存现金", "ASSET", "DEBIT", True, False),
        ("1002", "银行存款", "ASSET", "DEBIT", False, True),
        ("1122", "应收账款", "ASSET", "DEBIT", False, False),
        ("1123", "预付账款", "ASSET", "DEBIT", False, False),
        ("1403", "原材料", "ASSET", "DEBIT", False, False),
        ("1405", "库存商品", "ASSET", "DEBIT", False, False),
        ("2202", "应付账款", "LIABILITY", "CREDIT", False, False),
        ("2203", "预收账款", "LIABILITY", "CREDIT", False, False),
        ("6001", "主营业务收入", "PROFIT", "CREDIT", False, False),
        ("6401", "主营业务成本", "PROFIT", "DEBIT", False, False),
        ("6601", "销售费用", "PROFIT", "DEBIT", False, False),
        ("6602", "管理费用", "PROFIT", "DEBIT", False, False),
    ]
    subjects = []
    for i, (code, name, category, direction, is_cash, is_bank) in enumerate(subject_defs):
        subjects.append(
            _u(db, AccountSubject, {"code": f"{P}{code}"},
               {"name": name, "category": category, "balance_direction": direction,
                "is_auxiliary": False, "is_qty_amount": False, "measure_unit": None,
                "is_cash": is_cash, "is_bank": is_bank, "is_journal": False,
                "is_cash_equivalent": is_cash, "is_enabled": True,
                "project_category_id": categories[i % len(categories)].id if i % 4 == 0 else None}, c)
        )

    for i in range(1, COUNT + 1):
        amount = D(200 * i) + D("80")
        voucher = _u(db, Voucher, {"voucher_no": f"{P}V-{i:04d}"},
                     {"voucher_word": "记", "business_date": _days_ago(45 - i * 2),
                      "record_date": _days_ago(45 - i * 2),
                      "reference": f"{N}凭证 {i}",
                      "total_debit": amount, "total_credit": amount,
                      "status": "POSTED" if i % 3 == 0 else "DRAFT",
                      "created_by": staff_ids[i % len(staff_ids)],
                      "auditor_id": staff_ids[0] if i % 3 == 0 else None,
                      "posted_by": staff_ids[0] if i % 3 == 0 else None}, c)
        debit_subject = subjects[i % len(subjects)]
        credit_subject = subjects[(i + 6) % len(subjects)]
        for k, (subject, debit, credit) in enumerate(
            ((debit_subject, amount, D("0")), (credit_subject, D("0"), amount))
        ):
            _u(db, VoucherItem, {"voucher_id": voucher.id, "subject_id": subject.id},
               {"summary": f"{N}凭证分录 {i}-{k + 1}", "debit": debit, "credit": credit}, c)

    for i in range(1, COUNT + 1):
        _u(db, Receipt, {"receipt_no": f"{P}RCP-{i:04d}"},
           {"customer_id": ctx["customers"][i % len(ctx["customers"])].id,
            "sale_type": "零售" if i % 2 else "批发",
            "settle_type": "BANK" if i % 2 else "CASH",
            "amount": D(150 * i) + D("20"),
            "status": "CONFIRMED" if i % 2 == 0 else "PENDING",
            "audit_status": "APPROVED" if i % 2 == 0 else "PENDING",
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    for i in range(1, COUNT + 1):
        _u(db, Expense, {"expense_no": f"{P}EXP-{i:04d}"},
           {"customer_id": ctx["customers"][i % len(ctx["customers"])].id,
            "expense_type": ["办公费", "差旅费", "运输费", "广告费"][i % 4],
            "amount": D(80 * i) + D("30"), "description": f"{N}费用单 {i}",
            "status": "CONFIRMED" if i % 3 == 0 else "PENDING",
            "audit_status": "APPROVED" if i % 3 == 0 else "PENDING",
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    for i in range(1, COUNT + 1):
        _u(db, Settlement, {"settlement_no": f"{P}SET-{i:04d}"},
           {"shop_id": ctx["shops"][i % len(ctx["shops"])].id,
            "platform_no": f"{P}PLAT-{i:04d}",
            "settlement_date": (_now() - timedelta(days=i * 5)).date(),
            "amount": D(500 * i) + D("60"),
            "status": "CONFIRMED" if i % 2 == 0 else "PENDING",
            "source_file": f"settlement_{i:04d}.xlsx", "remark": f"{N}结算单 {i}",
            "checked_by": staff_ids[0] if i % 2 == 0 else None,
            "checked_at": _days_ago(20 - i) if i % 2 == 0 else None,
            "confirmed_by": staff_ids[0] if i % 2 == 0 else None,
            "confirmed_at": _days_ago(19 - i) if i % 2 == 0 else None,
            "created_by": staff_ids[i % len(staff_ids)]}, c)

    # 发票管理页只列 document_type=INVOICE，另留少量 RECEIVABLE 供结算单关联。
    for i in range(1, COUNT + 3):
        amount = D(300 * i) + D("40")
        received = amount if i % 3 == 0 else (amount / D("2") if i % 3 == 1 else D("0"))
        _u(db, Invoice, {"invoice_no": f"{P}INV-{i:04d}"},
           {"document_type": "RECEIVABLE" if i % 5 == 0 else "INVOICE",
            "invoice_title": f"{N}客户{i:02d}",
            "tax_no": f"91{i:015d}", "invoice_type": "SPECIAL" if i % 2 else "NORMAL",
            "amount": amount, "received_amount": received,
            "is_received": received == amount,
            "company": "ERP演示公司", "status": "ISSUED" if i % 2 == 0 else "PENDING",
            "applicant": f"{N}申请人{i:02d}"}, c)


# ------------------------------------------------------------------ 清理
PURGE_ORDER = [
    OrderShipmentItem, OrderShipment, Waybill, PickList, DeliveryList, LoadNo,
    ReturnRegister, ExchangeOrderItem, ExchangeOrder,
    VoucherItem, Voucher, Receipt, Expense, Settlement, Invoice, Payment, Income,
    AccountSubject, ProjectCategory,
    ReturnOrderItem, ReturnOrder, WaveOrder, Wave, WaveStrategy,
    LocationAdjustItem, LocationAdjust, StockCheckItem, StockCheck,
    TransferInItem, TransferIn, TransferOutItem, TransferOut, TransferItem, Transfer,
    StockOutItem, StockOut, StockInItem, StockIn, Putaway,
    StockFlow, StockWarning, StockCost, StockSettlement, ShopStock,
    PurchaseReturnItem, PurchaseReturn, PurchasePrepay, PurchaseInvoice,
    PurchaseOrderItem, PurchaseOrder, PurchasePlanItem, PurchasePlan,
    ComboProductItem, ComboProduct, SupplierPrice, ProductSku, Product,
    Location, Zone, Warehouse, Shop, ShopApi, Platform, Logistics, FreightTemplate,
    PrintTemplate, UnitGroupItem, UnitGroup, AreaRule,
    Customer, Supplier, Account, SpecValue, SpecType, Category, Unit, Brand,
    Position, Department, ApprovalFlow,
]


def _purge(db: Session) -> int:
    removed = 0
    for model in PURGE_ORDER:
        table = model.__table__
        columns = table.columns.keys()
        code_col = next(
            (n for n in ("code", "no", "order_no", "voucher_no", "combo_no", "name")
             if n in columns and n != "name"),
            None,
        )
        name_col = "name" if "name" in columns else None
        stmt = delete(model)
        if code_col:
            stmt = stmt.where(getattr(model, code_col).like(f"{P}%"))
        elif name_col:
            stmt = stmt.where(getattr(model, name_col).like(f"{N}%"))
        else:
            continue
        result = db.execute(stmt)
        removed += result.rowcount or 0
    db.commit()
    return removed


def seed() -> dict[str, int]:
    counter = Counter()
    with SessionLocal() as db:
        company = db.scalar(select(Company).order_by(Company.id).limit(1))
        if company is None:
            raise RuntimeError("本地库没有公司，请先执行 scripts/seed_demo_data.py")
        # 只取本公司员工：单据 created_by 落在其他公司时，会被数据范围过滤后不可见。
        staff = list(
            db.scalars(
                select(Staff).where(Staff.company_id == company.id).order_by(Staff.id)
            )
        )
        if not staff:
            raise RuntimeError("本公司没有员工，请先执行 scripts/seed_demo_data.py")

        ctx: dict[str, Any] = {
            "company_id": company.id,
            "staff_ids": [s.id for s in staff],
        }
        _seed_base(db, ctx, counter)
        _seed_products(db, ctx, counter)
        _seed_purchase(db, ctx, counter)
        _seed_stock(db, ctx, counter)
        _seed_order_extras(db, ctx, counter)
        _seed_extras(db, ctx, counter)
        _seed_approval(db, ctx, counter)
        _seed_finance(db, ctx, counter)
        db.commit()
    return {"created": counter.created, "updated": counter.updated}


def main() -> int:
    parser = argparse.ArgumentParser(description="写入六大模块测试数据")
    parser.add_argument("--purge", action="store_true", help="清除本脚本写入的数据后退出")
    args = parser.parse_args()

    if args.purge:
        with SessionLocal() as db:
            removed = _purge(db)
        print(f"已清除约 {removed} 行 {P} / {N} 前缀数据")
        return 0

    result = seed()
    print(f"写入完成：新增 {result['created']} 行，更新 {result['updated']} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
