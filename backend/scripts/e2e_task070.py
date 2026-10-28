"""TASK-070 cross-module reconciliation on local MySQL, always rolled back."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import redis as redis_module
from app.db.session import engine
from app.models.approval import ApprovalFlow
from app.models.finance import Voucher, VoucherItem
from app.models.order import Order, WaveOrder
from app.models.org import Staff
from app.models.party import Customer, Supplier
from app.models.platform import Logistics, Shop
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseOrderItem
from app.models.rbac import Role, StaffRole
from app.models.stock import StockFlow, StockInItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import (
    approval_service,
    order_service,
    pay_receipt_service,
    purchase_order_service,
    ship_service,
    stock_in_service,
    wave_service,
)
from scripts.e2e_task069_server import TransactionalTestRedis


def quantity(db: Session, location_id: int, sku_id: int) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(StockLocation.qty), 0)).where(
            StockLocation.location_id == location_id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == "NORMAL",
        )
    )
    return Decimal(value)


def ensure_flow(db: Session, doc_type: str, admin: Staff, marker: str) -> None:
    if db.scalar(select(ApprovalFlow.id).where(ApprovalFlow.doc_type == doc_type)):
        return
    approval_service.create_flow(
        db,
        name=f"{marker}-{doc_type}",
        doc_type=doc_type,
        approvers=[admin.id],
        actor=admin,
    )


def assert_voucher(db: Session, reference: str, expected: Decimal) -> int:
    voucher = db.scalar(select(Voucher).where(Voucher.reference == reference))
    assert voucher is not None, f"missing voucher: {reference}"
    assert voucher.total_debit == voucher.total_credit == expected, reference
    items = list(db.scalars(select(VoucherItem).where(VoucherItem.voucher_id == voucher.id)))
    assert len(items) >= 2, reference
    assert sum((row.debit for row in items), Decimal("0")) == expected
    assert sum((row.credit for row in items), Decimal("0")) == expected
    return voucher.id


def run(db: Session, marker: str) -> dict:
    admin = db.scalar(
        select(Staff)
        .join(StaffRole, StaffRole.staff_id == Staff.id)
        .join(Role, Role.id == StaffRole.role_id)
        .where(Role.code == "ADMIN", Staff.is_active.is_(True))
        .order_by(Staff.id.desc())
    )
    assert admin is not None, "local MySQL has no active admin"
    supplier = db.scalar(select(Supplier).where(Supplier.is_active.is_(True)))
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.is_enabled.is_(True), Warehouse.is_virtual.is_(False))
    )
    location = db.scalar(select(Location).where(Location.warehouse_id == warehouse.id, Location.is_enabled.is_(True))) if warehouse else None
    sku = db.scalar(select(ProductSku).where(ProductSku.is_active.is_(True)).order_by(ProductSku.id))
    product = db.get(Product, sku.product_id) if sku else None
    shop = db.scalar(select(Shop).order_by(Shop.id))
    logistics = db.scalar(select(Logistics).where(Logistics.is_enabled.is_(True)).order_by(Logistics.id))
    assert all((supplier, warehouse, location, sku, product, shop, logistics)), "local MySQL lacks purchase/order/warehouse fixtures"

    for doc_type in ("purchase_order", "payment", "receipt"):
        ensure_flow(db, doc_type, admin, marker)
    customer = Customer(code=marker, name=f"E2E customer {marker}")
    db.add(customer)
    db.commit()

    before = quantity(db, location.id, sku.id)
    purchase = purchase_order_service.create_order(
        db,
        data={
            "supplier_id": supplier.id,
            "warehouse_id": warehouse.id,
            "description": marker,
            "items": [{"product_id": product.id, "sku_id": sku.id, "qty": "2", "purchase_price": "10"}],
        },
        staff=admin,
    )
    purchase_order_service.submit_order(db, order_id=purchase.id, staff=admin)
    purchase_order_service.approve_order(db, order_id=purchase.id, staff=admin)
    inbound = stock_in_service.create_stock_in(
        db,
        data={
            "warehouse_id": warehouse.id,
            "purchase_order_id": purchase.id,
            "stock_in_type": "PURCHASE",
            "remark": marker,
            "items": [{"product_id": product.id, "sku_id": sku.id, "qty": "2"}],
        },
        staff=admin,
    )
    stock_in_service.submit_stock_in(db, stock_in_id=inbound.id, staff=admin)
    stock_in_service.approve_stock_in(db, stock_in_id=inbound.id, staff=admin)
    item = db.scalar(select(StockInItem).where(StockInItem.stock_in_id == inbound.id))
    assert item is not None
    stock_in_service.putaway_stock(
        db,
        stock_in_id=inbound.id,
        data={"items": [{"stock_in_item_id": item.id, "location_id": location.id, "qty": "2"}]},
        staff=admin,
    )
    assert quantity(db, location.id, sku.id) == before + Decimal("2")
    purchase_item = db.scalar(select(PurchaseOrderItem).where(PurchaseOrderItem.order_id == purchase.id))
    assert purchase_item and purchase_item.arrived_qty == Decimal("2")
    inbound_flows = list(db.scalars(select(StockFlow).where(StockFlow.source_no == inbound.stock_in_no, StockFlow.direction == "IN")))
    assert sum((row.qty for row in inbound_flows), Decimal("0")) == Decimal("2")
    assert sum((row.total_cost or Decimal("0") for row in inbound_flows), Decimal("0")) == Decimal("20")
    inbound_voucher_id = assert_voucher(db, f"采购入库单:{inbound.stock_in_no}", Decimal("20"))

    order = order_service.create_order(
        db,
        data={
            "order_no": marker,
            "shop_id": shop.id,
            "warehouse_id": warehouse.id,
            "logistics_id": logistics.id,
            "buyer_nick": marker,
            "receiver_name": "E2E Receiver",
            "receiver_phone": "13800000000",
            "receiver_address": marker,
            "order_time": datetime.now(timezone.utc).replace(tzinfo=None),
            "pay_time": datetime.now(timezone.utc).replace(tzinfo=None),
            "pay_status": "PAID",
            "paid_amount": "20.00",
            "items": [{"product_id": product.id, "sku_id": sku.id, "qty": "1", "price": "20", "is_gift": False}],
        },
        staff=admin,
    )
    order_service.approve_order(db, order_id=order.id, staff=admin)
    strategy = wave_service.create_wave_strategy(
        db,
        data={
            "name": marker,
            "logistics_id": logistics.id,
            "warehouse_id": warehouse.id,
            "shop_ids": [shop.id],
            "wave_order_count": 10,
            "load_no_type": "BY_EXPRESS",
            "sort_type": "LOCATION",
            "is_enabled": True,
        },
        staff=admin,
    )
    wave = wave_service.run_wave_strategies(db, strategy_ids=[strategy.id], order_ids=[order.id], staff=admin)
    assert wave["wave_count"] == wave["order_count"] == 1
    assert db.scalar(select(WaveOrder.id).where(WaveOrder.order_id == order.id)) is not None
    shipment = ship_service.scan_shipment(db, scan_code=order.order_no, qty="1", staff=admin)
    assert shipment["completed"] and db.get(Order, order.id).status == "SHIPPED"
    assert quantity(db, location.id, sku.id) == before + Decimal("1")
    out_qty = db.scalar(select(func.coalesce(func.sum(StockFlow.qty), 0)).where(StockFlow.order_no == order.order_no, StockFlow.direction == "OUT"))
    assert Decimal(out_qty) == Decimal("1")
    assert quantity(db, location.id, sku.id) == before + sum((row.qty for row in inbound_flows), Decimal("0")) - Decimal(out_qty)

    payment = pay_receipt_service.create_payment(
        db, data={"supplier_id": supplier.id, "stock_in_id": inbound.id, "amount": "20"}, staff=admin,
    )
    pay_receipt_service.submit_payment(db, payment_id=payment.id, staff=admin)
    pay_receipt_service.approve_payment(db, payment_id=payment.id, staff=admin)
    payment_voucher_id = assert_voucher(db, f"付款单:{payment.payment_no}", Decimal("20"))
    receipt = pay_receipt_service.create_receipt(
        db, data={"customer_id": customer.id, "order_id": order.id, "amount": "20"}, staff=admin,
    )
    pay_receipt_service.submit_receipt(db, receipt_id=receipt.id, staff=admin)
    pay_receipt_service.approve_receipt(db, receipt_id=receipt.id, staff=admin)
    receipt_voucher_id = assert_voucher(db, f"收款单:{receipt.receipt_no}", Decimal("20"))

    return {
        "marker": marker,
        "purchase_order_id": purchase.id,
        "stock_in_id": inbound.id,
        "order_id": order.id,
        "stock_before": str(before),
        "stock_after": str(quantity(db, location.id, sku.id)),
        "inbound_voucher_id": inbound_voucher_id,
        "payment_voucher_id": payment_voucher_id,
        "receipt_voucher_id": receipt_voucher_id,
    }


def main() -> None:
    assert engine.dialect.name == "mysql", "TASK-070 requires local MySQL"
    marker = f"TASK070-{uuid4().hex[:12]}"
    original_cache = redis_module._cache
    redis_module._cache = TransactionalTestRedis()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(
                    bind=connection,
                    autoflush=False,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    summary = run(db, marker)
                    print(json.dumps(summary, ensure_ascii=False))
            finally:
                transaction.rollback()
        with Session(engine) as check:
            assert check.scalar(select(func.count()).select_from(Order).where(Order.order_no == marker)) == 0
            assert check.scalar(select(func.count()).select_from(Customer).where(Customer.code == marker)) == 0
        print("TASK-070 MySQL rollback verified")
    finally:
        redis_module._cache = original_cache


if __name__ == "__main__":
    main()
