from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.print import router as print_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Brand, Category, Unit
from app.models.order import (
    DeliveryList,
    Order,
    OrderItem,
    PickList,
    PrintTemplate,
    Wave,
    WaveOrder,
    Waybill,
)
from app.models.org import Company, Staff
from app.models.platform import Logistics, Platform, Shop
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import print_service


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
            Brand(id=1, name="品牌甲"),
            Brand(id=2, name="品牌乙"),
            Category(id=1, name="分类甲"),
            Category(id=2, name="分类乙"),
        ]
    )
    db.add_all(
        [
            Product(
                id=1,
                name="商品一",
                code="P0001",
                brand_id=1,
                category_id=1,
                main_unit_id=1,
                online_price=Decimal("10.00"),
            ),
            Product(
                id=2,
                name="商品二",
                code="P0002",
                brand_id=2,
                category_id=2,
                main_unit_id=1,
                online_price=Decimal("8.00"),
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
                is_active=True,
            ),
            ProductSku(
                id=2,
                product_id=2,
                spec_name="默认",
                spec_code="P0002-01",
                barcode="690000000002",
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
            Location(id=1, warehouse_id=1, code="A-02", is_enabled=True),
            Location(id=2, warehouse_id=1, code="A-01", is_enabled=True),
        ]
    )
    db.add_all(
        [
            StockLocation(
                id=1,
                location_id=1,
                product_sku_id=1,
                qty=Decimal("10.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=2,
                location_id=2,
                product_sku_id=2,
                qty=Decimal("10.000"),
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
    product_id: int,
    sku_id: int,
    receiver_name: str,
    amount: str,
) -> Order:
    order = Order(
        order_no=order_no,
        shop_id=1,
        warehouse_id=1,
        logistics_id=1,
        receiver_name=receiver_name,
        receiver_phone="13800000000",
        receiver_province="浙江省",
        receiver_city="杭州市",
        receiver_address="文一西路 1 号",
        status="PENDING",
        audit_status="APPROVED",
        pay_status="PAID",
        order_amount=Decimal(amount),
        paid_amount=Decimal(amount),
        order_time=datetime(2026, 9, 23, 10, 0, 0),
        seller_remark="轻拿轻放",
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product_id,
            sku_id=sku_id,
            sku_name="默认",
            qty=Decimal("1.000"),
            price=Decimal(amount),
            amount=Decimal(amount),
            is_gift=False,
        )
    )
    db.commit()
    return order


def _add_wave(db, orders: list[Order]) -> Wave:
    wave = Wave(
        wave_no="BC20260923-0001",
        name="测试波次",
        total_order_count=len(orders),
        created_by=1,
    )
    db.add(wave)
    db.flush()
    for index, order in enumerate(orders, start=1):
        db.add(
            WaveOrder(
                wave_id=wave.id,
                order_id=order.id,
                load_no=f"BC20260923-0001-SF-{index:02d}",
                sequence=index,
            )
        )
    db.commit()
    return wave


def test_acquire_and_recycle_waybills(monkeypatch):
    serials = iter(["YDH20260923-0001", "YDH20260923-0002"])
    monkeypatch.setattr(
        print_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(
            db,
            order_no="TB-001",
            product_id=1,
            sku_id=1,
            receiver_name="张三",
            amount="10.00",
        )

        acquired = print_service.acquire_waybills(
            db,
            order_ids=[order.id],
            print_type="THERMAL",
            staff=db.get(Staff, 1),
        )
        assert acquired["source"] == "LOCAL"
        assert acquired["items"][0]["waybill_no"] == "YDH20260923-0001"
        db.refresh(order)
        assert order.tracking_no == "YDH20260923-0001"

        again = print_service.acquire_waybills(
            db,
            order_ids=[order.id],
            print_type="NORMAL",
            staff=db.get(Staff, 1),
        )
        assert again["items"][0]["waybill_no"] == "YDH20260923-0001"
        assert (
            db.scalar(select(func.count(Waybill.id))) == 1
        )

        recycled = print_service.recycle_waybills(
            db,
            order_ids=[order.id],
            staff=db.get(Staff, 1),
        )
        assert recycled["count"] == 1
        assert recycled["items"][0]["waybill_no"] == "YDH20260923-0001"
        db.refresh(order)
        assert order.tracking_no is None
        assert db.scalar(select(Waybill.waybill_no)) is None


def test_logistics_mock_acquire_label_and_recycle(monkeypatch):
    from app.integrations import logistics_provider

    Session = _session_factory()
    with Session() as db:
        _seed(db)
        db.get(Logistics, 1).name = "EMS"
        order = _make_order(db, order_no="EMS-001", product_id=1, sku_id=1, receiver_name="张三", amount="10.00")
        staff = db.get(Staff, 1)
        acquired = print_service.acquire_waybills(db, order_ids=[order.id], staff=staff, provider=True)
        assert acquired["source"] == "PROVIDER_MOCK"
        number = acquired["items"][0]["waybill_no"]
        assert number == f"MOCK-CAINIAO-{order.id}"

        monkeypatch.setattr(print_service, "generate_serial", lambda prefix, dt=None: "DYPC-MOCK-001")
        batch = print_service.create_print_batch(db, order_ids=[order.id], staff=staff)
        assert batch["waybills"][0]["waybill_no"] == number
        assert print_service.build_print_batch_pdf(db, batch_no=batch["batch_no"], document_type="WAYBILL").startswith(b"%PDF")
        with pytest.raises(BizException):
            print_service.recycle_waybills(db, order_ids=[order.id], staff=staff)

        second = _make_order(db, order_no="EMS-002", product_id=1, sku_id=1, receiver_name="李四", amount="10.00")
        print_service.acquire_waybills(db, order_ids=[second.id], staff=staff, provider=True)
        recycled = print_service.recycle_waybills(db, order_ids=[second.id], staff=staff)
        assert recycled["count"] == 1
        assert db.get(Order, second.id).tracking_no is None

        class BrokenProvider(logistics_provider.MockLogisticsProvider):
            def acquire(self, order, logistics):
                raise ConnectionError("mock provider down")

        third = _make_order(db, order_no="EMS-003", product_id=1, sku_id=1, receiver_name="王五", amount="10.00")
        monkeypatch.setattr(logistics_provider, "get_logistics_provider", lambda logistics: BrokenProvider("CAINIAO"))
        with pytest.raises(BizException, match="物流取号失败"):
            print_service.acquire_waybills(db, order_ids=[third.id], staff=staff, provider=True)


def test_create_print_batch_builds_one_pick_list_and_pdf(monkeypatch):
    monkeypatch.setattr(
        print_service,
        "generate_serial",
        lambda prefix, dt=None: "DYPC20260923-0001",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        first = _make_order(
            db,
            order_no="TB-001",
            product_id=1,
            sku_id=1,
            receiver_name="张三",
            amount="10.00",
        )
        second = _make_order(
            db,
            order_no="TB-002",
            product_id=2,
            sku_id=2,
            receiver_name="李四",
            amount="8.00",
        )
        _add_wave(db, [first, second])
        template = PrintTemplate(
            name="热敏快递单",
            template_type="WAYBILL",
            paper_width=Decimal("100.00"),
            paper_height=Decimal("150.00"),
            is_default=True,
        )
        db.add(template)
        db.commit()

        batch = print_service.create_print_batch(
            db,
            order_ids=[first.id, second.id],
            print_type="THERMAL",
            printer_name="仓库热敏打印机",
            template_id=template.id,
            include_shipping_slip=True,
            sort_type="LOCATION",
            waybill_nos={str(first.id): "SF0001", str(second.id): "SF0002"},
            staff=db.get(Staff, 1),
        )

        assert batch["batch_no"] == "DYPC20260923-0001"
        assert batch["include_shipping_slip"] is True
        assert batch["printer_name"] == "仓库热敏打印机"
        assert batch["order_count"] == 2
        assert len(batch["waybills"]) == 2
        assert {row["waybill_no"] for row in batch["waybills"]} == {
            "SF0001",
            "SF0002",
        }
        assert {row["is_printed"] for row in batch["waybills"]} == {True}
        assert db.scalar(select(func.count(PickList.id))) == 1
        assert db.scalar(select(func.count(DeliveryList.id))) == 2
        assert len(batch["shipping_slips"]) == 2

        pick = batch["pick_list"]
        assert pick["batch_no"] == "DYPC20260923-0001"
        assert pick["wave_id"] == db.scalar(select(Wave.id))
        assert [row["order_no"] for row in pick["rows"]] == [
            "TB-002",
            "TB-001",
        ]
        assert all(row["wave_no"] == "BC20260923-0001" for row in pick["rows"])
        assert all(row["load_no"] for row in pick["rows"])

        pdf = print_service.build_print_batch_pdf(
            db,
            batch_no=batch["batch_no"],
            document_type="ALL",
        )
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000


def test_batch_item_prefetch_has_bounded_query_count():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        orders = [
            _make_order(
                db,
                order_no=f"PERF-{index:03d}",
                product_id=1,
                sku_id=1,
                receiver_name="测试",
                amount="10.00",
            )
            for index in range(20)
        ]
        db.commit()
        queries = 0

        def count_query(*_args):
            nonlocal queries
            queries += 1

        event.listen(db.bind, "before_cursor_execute", count_query)
        try:
            context = print_service._batch_item_context(db, orders)
            records = [
                print_service._item_records(db, order, context=context)
                for order in orders
            ]
            batched_queries = queries
            queries = 0
            original_records = [print_service._item_records(db, order) for order in orders]
            per_order_queries = queries
        finally:
            event.remove(db.bind, "before_cursor_execute", count_query)
        assert batched_queries <= 7
        assert per_order_queries >= 20
        assert records == original_records
        assert len(records) == 20
        assert all(len(row) == 1 and row[0]["product_code"] == "P0001" for row in records)


def test_print_batch_validation_and_printed_recycle_guard():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(
            db,
            order_no="TB-003",
            product_id=1,
            sku_id=1,
            receiver_name="王五",
            amount="10.00",
        )
        with pytest.raises(BizException) as sort_exc:
            print_service.create_print_batch(
                db,
                order_ids=[order.id],
                sort_type="PRICE",
                waybill_nos={str(order.id): "SF0003"},
                staff=db.get(Staff, 1),
            )
        assert "排序方式" in sort_exc.value.message

        print_service.create_print_batch(
            db,
            order_ids=[order.id],
            waybill_nos={str(order.id): "SF0003"},
            staff=db.get(Staff, 1),
        )
        with pytest.raises(BizException) as recycle_exc:
            print_service.recycle_waybills(
                db,
                order_ids=[order.id],
                staff=db.get(Staff, 1),
            )
        assert "已打印" in recycle_exc.value.message


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
    app.include_router(print_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(staff_id, name, [])}"
    }


def test_api_print_permissions_and_batch_download(monkeypatch):
    monkeypatch.setattr(
        print_service,
        "generate_serial",
        lambda prefix, dt=None: "DYPC20260923-1001",
    )
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        order = _make_order(
            db,
            order_no="TB-API",
            product_id=1,
            sku_id=1,
            receiver_name="接口用户",
            amount="10.00",
        )
        order_id = order.id
    client = TestClient(app)

    denied = client.post(
        "/print/batches",
        json={
            "order_ids": [order_id],
            "waybill_nos": {str(order_id): "SF1001"},
        },
        headers=_auth(2, "财务"),
    )
    assert denied.status_code == 403

    created = client.post(
        "/print/batches",
        json={
            "order_ids": [order_id],
            "printer_name": "接口打印机",
            "include_shipping_slip": True,
            "waybill_nos": {str(order_id): "SF1001"},
        },
        headers=_auth(1, "仓库"),
    )
    assert created.status_code == 200
    batch_no = created.json()["data"]["batch_no"]
    assert batch_no == "DYPC20260923-1001"

    detail = client.get(
        f"/print/batches/{batch_no}",
        headers=_auth(2, "财务"),
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["order_count"] == 1

    denied_pdf = client.get(
        f"/print/batches/{batch_no}/pdf?document_type=WAYBILL",
        headers=_auth(2, "财务"),
    )
    assert denied_pdf.status_code == 403

    pdf = client.get(
        f"/print/batches/{batch_no}/pdf?document_type=WAYBILL",
        headers=_auth(1, "仓库"),
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
