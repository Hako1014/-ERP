from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.ship import router as ship_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.prefixes import SerialPrefix
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.order import (
    Order,
    OrderItem,
    OrderShipment,
    OrderShipmentItem,
    Waybill,
)
from app.models.org import Company, Staff
from app.models.platform import Logistics, Platform, Shop
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.stock import StockFlow, StockOut, StockOutItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import ship_service
from app.services import platform_sync_service


def test_platform_mock_push_records_status_and_failure(monkeypatch):
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        db.add(Order(id=901, order_no="PLATFORM-SHIP-1", shop_id=1))
        db.add(OrderShipment(id=901, shipment_no="PKG-901", order_id=901, warehouse_id=1, status="SHIPPED", tracking_no="TRACK-901"))
        db.commit()
        staff = db.get(Staff, 1)
        result = platform_sync_service.push_shipment(db, shipment_id=901, staff=staff)
        assert result["success"] is True
        assert db.get(OrderShipment, 901).platform_sync_status == "SYNCED"

        class BrokenAdapter:
            def push_shipment(self, shop, payload):
                raise ConnectionError("平台不可用")

        monkeypatch.setattr(platform_sync_service, "get_platform_adapter", lambda platform: BrokenAdapter())
        with pytest.raises(BizException):
            platform_sync_service.push_shipment(db, shipment_id=901, staff=staff)
        assert db.get(OrderShipment, 901).platform_sync_status == "FAILED"


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
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add_all(
        [
            Product(
                id=1,
                name="商品一",
                code="P0001",
                main_unit_id=1,
                online_price=Decimal("10.00"),
                cost_price=Decimal("6.00"),
                weight=Decimal("0.500"),
            ),
            Product(
                id=2,
                name="商品二",
                code="P0002",
                main_unit_id=1,
                online_price=Decimal("8.00"),
                cost_price=Decimal("5.00"),
                weight=Decimal("0.200"),
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
                barcode="690000000001",
                cost_price=Decimal("6.00"),
                is_active=True,
            ),
            ProductSku(
                id=2,
                product_id=2,
                spec_name="默认",
                spec_code="P0002-01",
                barcode="690000000002",
                cost_price=Decimal("5.00"),
                is_active=True,
            ),
        ]
    )
    db.add(
        Warehouse(
            id=1,
            code="WH01",
            name="主仓",
            warehouse_type="FINISHED",
            is_enabled=True,
            is_default=True,
            is_virtual=False,
            lock_type="NONE",
        )
    )
    db.add_all(
        [
            Location(
                id=1,
                warehouse_id=1,
                code="A-01",
                priority=10,
                is_enabled=True,
            ),
            Location(
                id=2,
                warehouse_id=1,
                code="A-02",
                priority=0,
                is_enabled=True,
            ),
        ]
    )
    db.add_all(
        [
            StockLocation(
                id=1,
                location_id=1,
                product_sku_id=1,
                qty=Decimal("1.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=2,
                location_id=2,
                product_sku_id=1,
                qty=Decimal("1.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=3,
                location_id=1,
                product_sku_id=2,
                qty=Decimal("2.000"),
                status="NORMAL",
            ),
        ]
    )
    db.add(Logistics(id=1, name="顺丰快递", company="顺丰", is_enabled=True))
    db.add(Platform(id=1, name="淘宝", code="TAOBAO"))
    db.add(Shop(id=1, platform_id=1, name="旗舰店"))
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


def _make_order(
    db,
    *,
    order_no: str,
    items: list[tuple[int, int, str]],
    tracking_no: str | None = None,
) -> Order:
    order = Order(
        order_no=order_no,
        shop_id=1,
        warehouse_id=1,
        logistics_id=1,
        receiver_name="张三",
        receiver_phone="13800000000",
        receiver_province="浙江省",
        receiver_city="杭州市",
        receiver_address="文一西路 1 号",
        status="PENDING",
        audit_status="APPROVED",
        pay_status="PAID",
        order_amount=Decimal("10.00"),
        paid_amount=Decimal("10.00"),
        tracking_no=tracking_no,
        order_time=datetime(2026, 9, 23, 10, 0, 0),
    )
    db.add(order)
    db.flush()
    for product_id, sku_id, qty in items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product_id,
                sku_id=sku_id,
                sku_name="默认",
                qty=Decimal(qty),
                price=Decimal("10.00"),
                amount=Decimal("10.00"),
                is_gift=False,
            )
        )
    if tracking_no:
        db.add(
            Waybill(
                order_id=order.id,
                waybill_no=tracking_no,
                print_type="THERMAL",
                is_printed=True,
            )
        )
    db.commit()
    return order


def _serial_patch(monkeypatch, *, start: int = 1):
    counters = {
        SerialPrefix.SHIPMENT.value: start,
        SerialPrefix.STOCK_OUT.value: start,
    }

    def fake_generate(prefix, dt=None):
        value = prefix.value if hasattr(prefix, "value") else str(prefix)
        seq = counters.get(value, start)
        counters[value] = seq + 1
        return f"{value}20260923-{seq:04d}"

    monkeypatch.setattr(ship_service, "generate_serial", fake_generate)


def _stock_qty(db, stock_location_id: int) -> Decimal:
    return Decimal(db.get(StockLocation, stock_location_id).qty)


def test_scan_order_barcode_deducts_stock_and_ships(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(
            db,
            order_no="TB-SHIP-001",
            tracking_no="SF1001",
            items=[(1, 1, "2.000")],
        )

        result = ship_service.scan_shipment(
            db,
            scan_code="TB-SHIP-001",
            barcode="690000000001",
            qty=Decimal("2.000"),
            actual_weight_g=Decimal("1000.000"),
            weight_tolerance_g=Decimal("50.000"),
            staff=db.get(Staff, 1),
        )

        assert result["completed"] is True
        assert result["scan_source"] == "ORDER"
        assert result["shipment"]["status"] == "SHIPPED"
        assert result["shipment"]["expected_weight_g"] == Decimal("1000.000")
        assert result["shipment"]["actual_weight_g"] == Decimal("1000.000")
        assert result["shipment"]["platform_sync_status"] == "PENDING"
        assert _stock_qty(db, 1) == Decimal("0.000")
        assert _stock_qty(db, 2) == Decimal("0.000")
        assert db.scalar(select(func.count(StockOutItem.id))) == 2
        assert db.scalar(select(func.count(StockFlow.id))) == 2
        db.refresh(order)
        assert order.status == "SHIPPED"
        assert order.ship_time is not None


def test_weight_out_of_range_blocks_scan_before_stock_deduction(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_order(
            db,
            order_no="TB-SHIP-002",
            items=[(1, 1, "1.000")],
        )

        with pytest.raises(BizException) as weight_exc:
            ship_service.scan_shipment(
                db,
                scan_code="TB-SHIP-002",
                barcode="690000000001",
                qty=Decimal("1.000"),
                actual_weight_g=Decimal("600.000"),
                weight_tolerance_g=Decimal("50.000"),
                staff=db.get(Staff, 1),
            )
        assert "重量超出误差区间" in weight_exc.value.message
        db.rollback()
        assert _stock_qty(db, 1) == Decimal("1.000")
        assert _stock_qty(db, 2) == Decimal("1.000")
        assert db.scalar(select(func.count(OrderShipment.id))) == 0

        overridden = ship_service.scan_shipment(
            db,
            scan_code="TB-SHIP-002",
            barcode="690000000001",
            qty=Decimal("1.000"),
            actual_weight_g=Decimal("600.000"),
            weight_tolerance_g=Decimal("50.000"),
            allow_weight_override=True,
            staff=db.get(Staff, 1),
        )
        assert overridden["shipment"]["weight_out_of_range"] is True
        assert overridden["shipment"]["weight_overridden"] is True


def test_multi_package_scan_completes_all_packages(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(
            db,
            order_no="TB-SHIP-003",
            items=[(1, 1, "1.000"), (2, 2, "1.000")],
        )
        staff = db.get(Staff, 1)

        first = ship_service.scan_shipment(
            db,
            scan_code="TB-SHIP-003",
            barcode="690000000001",
            tracking_no="SF2001",
            package_no=1,
            staff=staff,
        )
        assert first["completed"] is False
        assert first["shipment"]["status"] == "SCANNING"
        assert _stock_qty(db, 1) == Decimal("0.000")

        second = ship_service.scan_shipment(
            db,
            scan_code="TB-SHIP-003",
            barcode="690000000002",
            tracking_no="SF2002",
            package_no=2,
            staff=staff,
        )
        assert second["completed"] is True
        assert second["package_count"] == 2

        shipments = list(
            db.scalars(
                select(OrderShipment)
                .where(OrderShipment.order_id == order.id)
                .order_by(OrderShipment.package_no)
            )
        )
        assert [row.status for row in shipments] == ["SHIPPED", "SHIPPED"]
        assert [row.tracking_no for row in shipments] == ["SF2001", "SF2002"]
        assert db.scalar(select(func.count(OrderShipmentItem.id))) == 2
        db.refresh(order)
        assert order.status == "SHIPPED"


def test_one_click_and_self_pickup_shipping(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        one_click = _make_order(
            db,
            order_no="TB-SHIP-004",
            tracking_no="SF3001",
            items=[(1, 1, "1.000")],
        )
        pickup = _make_order(
            db,
            order_no="TB-SHIP-005",
            items=[(2, 2, "2.000")],
        )
        staff = db.get(Staff, 1)

        shipped = ship_service.ship_order(
            db,
            order_id=one_click.id,
            tracking_no="SF3001",
            actual_weight_g=Decimal("500.000"),
            weight_tolerance_g=Decimal("10.000"),
            staff=staff,
        )
        assert shipped["completed"] is True
        assert shipped["shipment"]["source"] == "ONE_CLICK"

        picked = ship_service.self_pickup_order(
            db,
            order_id=pickup.id,
            actual_weight_g=Decimal("400.000"),
            weight_tolerance_g=Decimal("10.000"),
            staff=staff,
        )
        assert picked["completed"] is True
        assert picked["shipment"]["shipping_type"] == "SELF_PICKUP"
        assert picked["shipment"]["tracking_no"] is None
        assert _stock_qty(db, 3) == Decimal("0.000")


def _make_api():
    Session = _session_factory(pool=True)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ship_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(staff_id, name, [])}"
    }


def test_api_scan_permissions_and_platform_sync(monkeypatch):
    _serial_patch(monkeypatch)
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        _make_order(
            db,
            order_no="TB-SHIP-API",
            items=[(1, 1, "1.000")],
        )
    client = TestClient(app)

    denied = client.post(
        "/shipments/scan",
        json={
            "scan_code": "TB-SHIP-API",
            "barcode": "690000000001",
        },
        headers=_auth(2, "财务"),
    )
    assert denied.status_code == 403

    scanned = client.post(
        "/shipments/scan",
        json={
            "scan_code": "TB-SHIP-API",
            "barcode": "690000000001",
            "actual_weight_g": "500.000",
            "weight_tolerance_g": "10.000",
        },
        headers=_auth(1, "仓库"),
    )
    assert scanned.status_code == 200
    shipment = scanned.json()["data"]["shipment"]
    assert shipment["status"] == "SHIPPED"

    listed = client.get("/shipments", headers=_auth(2, "财务"))
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    synced = client.post(
        f"/shipments/{shipment['id']}/platform-sync",
        json={"status": "SYNCED", "message": "本地模拟回传成功"},
        headers=_auth(1, "仓库"),
    )
    assert synced.status_code == 200
    assert synced.json()["data"]["platform_sync_status"] == "SYNCED"
