from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import dashboard as dashboard_api
from app.api.dashboard import router as dashboard_router
from app.api.report import router as report_router
from app.core.handlers import register_exception_handlers
from app.core.data_scope import initialize_scope_policy
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Brand, Category
from app.models.org import Company, Staff
from app.models.order import Order, OrderItem
from app.models.party import Customer, Supplier
from app.models.platform import Platform, Shop
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseInvoice, PurchaseOrder, PurchaseOrderItem
from app.models.rbac import Role, StaffRole
from app.models.stock import StockIn, StockInItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import metrics_service


def test_dashboard_short_cache_and_db_fallback(monkeypatch):
    class FakeCache:
        value = None
        ttl = None

        def get(self, _key):
            return self.value

        def set(self, _key, value, ex=None):
            self.value = value
            self.ttl = ex

    cache = FakeCache()
    calls = 0

    def overview(_db, *, trend_period):
        nonlocal calls
        calls += 1
        return {"period": trend_period, "as_of": date(2026, 9, 24)}

    monkeypatch.setattr(dashboard_api, "get_redis", lambda: cache)
    monkeypatch.setattr(metrics_service, "dashboard_overview", overview)
    Session = _session_factory()
    db = Session()
    _seed(db)
    db.add(Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"))
    db.add(StaffRole(staff_id=1, role_id=1))
    db.commit()
    staff = db.get(Staff, 1)
    initialize_scope_policy(db, staff)
    first = dashboard_api.get_dashboard_overview(trend_period="day", staff=staff, db=db)
    second = dashboard_api.get_dashboard_overview(trend_period="day", staff=staff, db=db)
    assert first.data["period"] == second.data["period"] == "day"
    assert calls == 1
    assert cache.ttl == 15

    class BrokenCache:
        def get(self, _key):
            from redis.exceptions import ConnectionError
            raise ConnectionError("offline")

        def set(self, *_args, **_kwargs):
            from redis.exceptions import ConnectionError
            raise ConnectionError("offline")

    monkeypatch.setattr(dashboard_api, "get_redis", lambda: BrokenCache())
    fallback = dashboard_api.get_dashboard_overview(trend_period="day", staff=staff, db=db)
    assert fallback.data["period"] == "day"
    assert calls == 2
    db.close()


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db) -> None:
    db.add(Company(id=1, full_name="示例公司"))
    db.add_all(
        [
            Staff(id=1, company_id=1, name="仓库", phone="13900000001"),
            Staff(id=2, company_id=1, name="财务", phone="13900000002"),
        ]
    )
    db.add_all(
        [
            Category(id=1, name="主商品分类"),
            Category(id=2, name="配件分类"),
            Brand(id=1, name="主品牌"),
            Brand(id=2, name="配件品牌"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add_all(
        [
            Product(
                id=1,
                name="主商品",
                code="P0001",
                category_id=1,
                brand_id=1,
                online_price=Decimal("50.00"),
                warn_stock=10,
            ),
            Product(
                id=2,
                name="配件商品",
                code="P0002",
                category_id=2,
                brand_id=2,
                online_price=Decimal("12.00"),
                warn_stock=5,
            ),
        ]
    )
    db.add_all(
        [
            ProductSku(
                id=1,
                product_id=1,
                spec_name="默认",
                spec_code="P0001-01",
                is_active=True,
            ),
            ProductSku(
                id=2,
                product_id=2,
                spec_name="默认",
                spec_code="P0002-01",
                is_active=True,
            ),
        ]
    )
    db.add(Platform(id=1, code="TB", name="淘宝"))
    db.add(Shop(id=1, platform_id=1, name="示例店铺"))
    db.add(
        Warehouse(
            id=1,
            code="WH01",
            name="主仓",
            warehouse_type="FINISHED",
            is_enabled=True,
            is_virtual=False,
            lock_type="NONE",
        )
    )
    db.add(Location(id=1, warehouse_id=1, code="WH01-A01", is_enabled=True))
    db.add_all(
        [
            Customer(
                id=1,
                code="C0001",
                name="今日客户一",
                created_at=datetime(2026, 9, 23, 16, 10, 0),
            ),
            Customer(
                id=2,
                code="C0002",
                name="今日客户二",
                created_at=datetime(2026, 9, 24, 2, 10, 0),
            ),
            Customer(
                id=3,
                code="C0003",
                name="昨日客户",
                created_at=datetime(2026, 9, 23, 15, 30, 0),
            ),
        ]
    )
    db.add_all(
        [
            StockLocation(
                id=1,
                location_id=1,
                product_sku_id=1,
                qty=Decimal("12.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=2,
                location_id=1,
                product_sku_id=2,
                qty=Decimal("4.000"),
                status="NORMAL",
            ),
        ]
    )

    # 业务日 2026-09-24（Asia/Shanghai）从 UTC 2026-09-23 16:00 开始。
    db.add_all(
        [
            Order(
                id=1,
                order_no="O0001",
                shop_id=1,
                buyer_nick="客户甲",
                status="DONE",
                audit_status="APPROVED",
                pay_status="PAID",
                order_amount=Decimal("100.00"),
                paid_amount=Decimal("100.00"),
                order_time=datetime(2026, 9, 23, 16, 20, 0),
                pay_time=datetime(2026, 9, 23, 16, 30, 0),
                created_at=datetime(2026, 9, 23, 16, 20, 0),
            ),
            Order(
                id=2,
                order_no="O0002",
                shop_id=1,
                buyer_nick="客户乙",
                status="PENDING",
                audit_status="APPROVED",
                pay_status="PAID",
                order_amount=Decimal("50.00"),
                paid_amount=Decimal("50.00"),
                order_time=datetime(2026, 9, 24, 1, 50, 0),
                pay_time=datetime(2026, 9, 24, 2, 0, 0),
                created_at=datetime(2026, 9, 24, 1, 50, 0),
            ),
            Order(
                id=3,
                order_no="O0003",
                shop_id=1,
                buyer_nick="取消客户",
                status="CANCELLED",
                audit_status="APPROVED",
                pay_status="PAID",
                order_amount=Decimal("999.00"),
                paid_amount=Decimal("999.00"),
                order_time=datetime(2026, 9, 24, 3, 0, 0),
                pay_time=datetime(2026, 9, 24, 3, 0, 0),
                created_at=datetime(2026, 9, 24, 3, 0, 0),
            ),
            Order(
                id=4,
                order_no="O0004",
                shop_id=1,
                buyer_nick="未付款客户",
                status="NEW",
                audit_status="PENDING",
                pay_status="UNPAID",
                order_amount=Decimal("888.00"),
                paid_amount=Decimal("0.00"),
                order_time=datetime(2026, 9, 24, 4, 0, 0),
                created_at=datetime(2026, 9, 24, 4, 0, 0),
            ),
            Order(
                id=5,
                order_no="O0005",
                shop_id=1,
                buyer_nick="昨日客户甲",
                status="DONE",
                audit_status="APPROVED",
                pay_status="PAID",
                order_amount=Decimal("40.00"),
                paid_amount=Decimal("40.00"),
                order_time=datetime(2026, 9, 23, 11, 50, 0),
                pay_time=datetime(2026, 9, 23, 12, 0, 0),
                created_at=datetime(2026, 9, 23, 11, 50, 0),
            ),
            Order(
                id=6,
                order_no="O0006",
                shop_id=1,
                buyer_nick="昨日客户乙",
                status="DONE",
                audit_status="APPROVED",
                pay_status="PAID",
                order_amount=Decimal("30.00"),
                paid_amount=Decimal("30.00"),
                order_time=datetime(2026, 9, 23, 15, 20, 0),
                pay_time=datetime(2026, 9, 23, 15, 30, 0),
                created_at=datetime(2026, 9, 23, 15, 20, 0),
            ),
        ]
    )
    db.add_all(
        [
            OrderItem(
                id=1,
                order_id=1,
                product_id=1,
                sku_id=1,
                qty=Decimal("2.000"),
                price=Decimal("50.00"),
                amount=Decimal("100.00"),
                is_gift=False,
            ),
            OrderItem(
                id=2,
                order_id=2,
                product_id=1,
                sku_id=1,
                qty=Decimal("1.000"),
                price=Decimal("50.00"),
                amount=Decimal("50.00"),
                is_gift=False,
            ),
            OrderItem(
                id=3,
                order_id=3,
                product_id=1,
                sku_id=1,
                qty=Decimal("5.000"),
                price=Decimal("199.80"),
                amount=Decimal("999.00"),
                is_gift=False,
            ),
            OrderItem(
                id=4,
                order_id=5,
                product_id=2,
                sku_id=2,
                qty=Decimal("4.000"),
                price=Decimal("10.00"),
                amount=Decimal("40.00"),
                is_gift=False,
            ),
            OrderItem(
                id=5,
                order_id=6,
                product_id=2,
                sku_id=2,
                qty=Decimal("3.000"),
                price=Decimal("10.00"),
                amount=Decimal("30.00"),
                is_gift=False,
            ),
            OrderItem(
                id=6,
                order_id=1,
                product_id=2,
                sku_id=2,
                qty=Decimal("1.000"),
                price=Decimal("0.00"),
                amount=Decimal("0.00"),
                is_gift=True,
            ),
        ]
    )

    db.add_all(
        [
            PurchaseOrder(
                id=1,
                order_no="PO0001",
                supplier_id=1,
                warehouse_id=1,
                purchaser_id=1,
                status="RECEIVING",
                audit_status="APPROVED",
                total_qty=Decimal("10.000"),
                total_amount=Decimal("100.00"),
                arrived_qty=Decimal("4.000"),
                created_at=datetime(2026, 9, 10, 1, 0, 0),
            ),
            PurchaseOrder(
                id=2,
                order_no="PO0002",
                supplier_id=1,
                warehouse_id=1,
                purchaser_id=1,
                status="RECEIVED",
                audit_status="APPROVED",
                total_qty=Decimal("2.000"),
                total_amount=Decimal("20.00"),
                arrived_qty=Decimal("2.000"),
                created_at=datetime(2026, 9, 11, 1, 0, 0),
            ),
        ]
    )
    db.add_all(
        [
            PurchaseOrderItem(
                id=1,
                order_id=1,
                product_id=1,
                sku_id=1,
                purchase_price=Decimal("10.00"),
                qty=Decimal("10.000"),
                in_transit_qty=Decimal("6.000"),
                arrived_qty=Decimal("4.000"),
                amount=Decimal("100.00"),
            ),
            PurchaseOrderItem(
                id=2,
                order_id=2,
                product_id=1,
                sku_id=1,
                purchase_price=Decimal("10.00"),
                qty=Decimal("2.000"),
                in_transit_qty=Decimal("0.000"),
                arrived_qty=Decimal("2.000"),
                amount=Decimal("20.00"),
            ),
        ]
    )
    db.add_all(
        [
            StockIn(
                id=1,
                stock_in_no="IN0001",
                warehouse_id=1,
                stock_in_type="PURCHASE",
                purchase_order_id=1,
                status="APPROVED",
                audit_status="APPROVED",
                total_qty=Decimal("4.000"),
                created_at=datetime(2026, 9, 12, 1, 0, 0),
            ),
            StockIn(
                id=2,
                stock_in_no="IN0002",
                warehouse_id=1,
                stock_in_type="PURCHASE",
                purchase_order_id=1,
                status="PENDING",
                audit_status="PENDING",
                total_qty=Decimal("2.000"),
                created_at=datetime(2026, 9, 13, 1, 0, 0),
            ),
        ]
    )
    db.add_all(
        [
            StockInItem(
                id=1,
                stock_in_id=1,
                product_id=1,
                sku_id=1,
                qty=Decimal("4.000"),
                cost_price=Decimal("10.00"),
                amount=Decimal("40.00"),
            ),
            StockInItem(
                id=2,
                stock_in_id=2,
                product_id=1,
                sku_id=1,
                qty=Decimal("2.000"),
                cost_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
        ]
    )
    db.add(
        PurchaseInvoice(
            id=1,
            invoice_no="FP0001",
            supplier_id=1,
            invoice_type="SPECIAL",
            tax_rate=Decimal("13.00"),
            amount=Decimal("113.00"),
            tax_amount=Decimal("13.00"),
            write_off_amount=Decimal("50.00"),
            write_off_status="PARTIAL",
            status="APPROVED",
            audit_status="APPROVED",
            created_at=datetime(2026, 9, 15, 1, 0, 0),
        )
    )
    db.flush()
    # 指标口径样本显式属于员工 2；无归属记录的拒绝由 TASK-098 反向测试覆盖。
    for model in (PurchaseOrder, PurchaseInvoice, StockIn):
        db.execute(update(model).values(created_by=2))
    db.commit()


def test_dashboard_overview_uses_business_day_and_sales_scope():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        overview = metrics_service.dashboard_overview(
            db,
            trend_period="day",
            as_of=date(2026, 9, 24),
        )

    metrics = {item["key"]: item for item in overview["metrics"]}
    assert metrics["today_order_amount"]["value"] == Decimal("150.00")
    assert metrics["today_order_amount"]["previous_value"] == Decimal("70.00")
    assert metrics["today_order_count"]["value"] == 2
    assert metrics["today_order_count"]["previous_value"] == 2
    assert metrics["stock_warning_count"]["value"] == 1
    assert metrics["stock_warning_count"]["previous_value"] is None
    assert metrics["customer_growth"]["value"] == 2
    assert metrics["customer_growth"]["previous_value"] == 1

    trend = overview["sales_trend"]["items"]
    assert trend[-1]["period"] == "2026-09-24"
    assert trend[-1]["amount"] == Decimal("150.00")
    assert trend[-1]["order_count"] == 2
    assert trend[-2]["amount"] == Decimal("70.00")
    assert trend[-2]["order_count"] == 2

    category = {
        item["name"]: item
        for item in overview["category_sales"]
    }
    assert category["主商品分类"]["amount"] == Decimal("150.00")
    assert category["配件分类"]["amount"] == Decimal("70.00")
    assert category["主商品分类"]["ratio"] == Decimal("68.18")
    assert category["配件分类"]["ratio"] == Decimal("31.82")

    brand = {
        item["name"]: item
        for item in overview["brand_sales"]
    }
    assert brand["主品牌"]["amount"] == Decimal("150.00")
    assert brand["配件品牌"]["amount"] == Decimal("70.00")
    assert overview["hot_products"][0]["product_id"] == 2
    assert overview["hot_products"][0]["sales_qty"] == Decimal("7.000")
    assert overview["recent_orders"][0]["order_no"] == "O0004"


def test_purchase_reports_aggregate_by_required_dimensions():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        db.add(Role(id=1, company_id=1, code="FINANCE", name="财务"))
        db.add(StaffRole(staff_id=2, role_id=1))
        db.commit()
        initialize_scope_policy(db, db.get(Staff, 2))
        order_report = metrics_service.purchase_order_report(db)
        inbound_report = metrics_service.purchase_inbound_report(db)
        invoice_report = metrics_service.purchase_invoice_report(db)
        stock_report = metrics_service.inventory_in_transit_report(db)

    assert order_report["summary"]["order_count"] == 2
    assert order_report["summary"]["total_qty"] == Decimal("12.000")
    assert order_report["summary"]["total_amount"] == Decimal("120.00")
    assert order_report["summary"]["in_transit_qty"] == Decimal("6.000")
    assert order_report["items"][0]["supplier_name"] == "示例供应商"

    assert inbound_report["summary"]["stock_in_count"] == 1
    assert inbound_report["summary"]["qty"] == Decimal("4.000")
    assert inbound_report["summary"]["amount"] == Decimal("40.00")
    assert inbound_report["items"][0]["purchaser_name"] == "仓库"
    assert inbound_report["items"][0]["product_code"] == "P0001"

    assert invoice_report["summary"]["invoice_count"] == 1
    assert invoice_report["summary"]["amount"] == Decimal("113.00")
    assert invoice_report["summary"]["tax_amount"] == Decimal("13.00")
    assert invoice_report["summary"]["net_amount"] == Decimal("100.00")
    assert invoice_report["items"][0]["write_off_status"] == "PARTIAL"

    assert stock_report["summary"]["current_qty"] == Decimal("16.000")
    assert stock_report["summary"]["in_transit_qty"] == Decimal("6.000")
    assert stock_report["summary"]["total_qty"] == Decimal("22.000")
    main_product = next(
        item
        for item in stock_report["items"]
        if item["product_id"] == 1
    )
    assert main_product["current_qty"] == Decimal("12.000")
    assert main_product["in_transit_qty"] == Decimal("6.000")


def test_dashboard_and_purchase_report_api_permissions():
    Session = _session_factory(pool=True)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(dashboard_router)
    app.include_router(report_router)
    app.dependency_overrides[get_db] = override_get_db

    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
                Role(id=2, company_id=1, code="FINANCE", name="财务"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
            ]
        )
        db.commit()

    client = TestClient(app)
    warehouse_headers = {
        "Authorization": f"Bearer {create_access_token(1, '仓库', [])}"
    }
    finance_headers = {
        "Authorization": f"Bearer {create_access_token(2, '财务', [])}"
    }

    dashboard = client.get(
        "/dashboard/overview",
        params={"trend_period": "day"},
        headers=warehouse_headers,
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["timezone"] == "Asia/Shanghai"
    assert len(dashboard.json()["data"]["metrics"]) == 4

    denied = client.get(
        "/reports/purchase-orders",
        headers=warehouse_headers,
    )
    assert denied.status_code == 403

    report = client.get(
        "/reports/purchase-orders",
        headers=finance_headers,
    )
    assert report.status_code == 200
    assert report.json()["data"]["summary"]["order_count"] == 2
