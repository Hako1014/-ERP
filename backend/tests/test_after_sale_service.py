from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.after_sale import router as after_sale_router
from app.core.data_scope import clear_scope_policy, initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.prefixes import SerialPrefix
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.finance import Voucher
from app.models.order import (
    Order,
    OrderItem,
    OrderShipment,
    OrderShipmentItem,
    ReturnOrder,
    ReturnRegister,
)
from app.models.org import Company, Staff
from app.models.platform import Platform, Shop
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.stock import StockFlow, StockIn
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import after_sale_service, stock_in_service, voucher_service


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
            Staff(id=1, company_id=1, name="售后", phone="13900000001"),
            Staff(id=2, company_id=1, name="财务", phone="13900000002"),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="AFTER_SALE", name="售后"),
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
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add(
        Product(
            id=1,
            name="测试商品",
            code="P0001",
            main_unit_id=1,
            online_price=Decimal("10.00"),
            cost_price=Decimal("6.00"),
        )
    )
    db.add(
        ProductSku(
            id=1,
            product_id=1,
            spec_name="默认",
            spec_code="P0001-01",
            barcode="690000000001",
            cost_price=Decimal("6.00"),
            is_active=True,
        )
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
    db.add(
        Location(
            id=1,
            warehouse_id=1,
            code="A-01",
            priority=10,
            is_enabled=True,
        )
    )
    db.add(Platform(id=1, name="淘宝", code="TAOBAO"))
    db.add(Shop(id=1, platform_id=1, name="旗舰店"))
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _make_order(db) -> Order:
    order = Order(
        order_no="TB-AFTER-001",
        shop_id=1,
        warehouse_id=1,
        receiver_name="张三",
        receiver_phone="13800000000",
        status="SHIPPED",
        audit_status="APPROVED",
        pay_status="PAID",
        order_amount=Decimal("50.00"),
        paid_amount=Decimal("50.00"),
        order_time=datetime(2026, 9, 23, 10, 0, 0),
        ship_time=datetime(2026, 9, 23, 12, 0, 0),
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=1,
        sku_id=1,
        sku_name="默认",
        qty=Decimal("5.000"),
        price=Decimal("10.00"),
        amount=Decimal("50.00"),
        is_gift=False,
    )
    db.add(item)
    db.flush()
    shipment = OrderShipment(
        shipment_no="FHD20260923-0001",
        order_id=order.id,
        warehouse_id=1,
        package_no=1,
        tracking_no="SF1001",
        shipping_type="EXPRESS",
        source="SCAN",
        status="SHIPPED",
        platform_sync_status="SYNCED",
        shipped_at=datetime(2026, 9, 23, 12, 0, 0),
    )
    db.add(shipment)
    db.flush()
    db.add(
        OrderShipmentItem(
            shipment_id=shipment.id,
            order_item_id=item.id,
            product_id=1,
            sku_id=1,
            qty=Decimal("5.000"),
            cost_price=Decimal("6.00"),
            barcode="690000000001",
        )
    )
    db.commit()
    return order


def _serial_patch(monkeypatch):
    counters: dict[str, int] = {}

    def fake_generate(prefix, dt=None):
        value = prefix.value if hasattr(prefix, "value") else str(prefix)
        seq = counters.get(value, 0) + 1
        counters[value] = seq
        return f"{value}20260923-{seq:04d}"

    monkeypatch.setattr(after_sale_service, "generate_serial", fake_generate)
    monkeypatch.setattr(stock_in_service, "generate_serial", fake_generate)


def _create_return(
    db,
    order: Order,
    *,
    freight: Decimal | None = None,
    qty: str = "2.000",
    refund_amount: str = "20.00",
):
    return after_sale_service.create_return(
        db,
        data={
            "order_id": order.id,
            "warehouse_id": 1,
            "reason": "顾客七天无理由",
            "logistics_company": "顺丰",
            "tracking_no": "SF-RETURN-1",
            "buyer_phone": "13800000000",
            "is_arrived": False,
            "freight": freight,
            "is_rejected_sign": False,
            "return_person": "张三",
            "goods_status": "完好",
            "need_return_goods": True,
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": Decimal(qty),
                    "refund_amount": Decimal(refund_amount),
                }
            ],
        },
        staff=db.get(Staff, 1),
    )


def test_return_requires_register_and_confirm_updates_stock(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(db)
        record = _create_return(db, order, freight=None)
        assert record.refund_amount == Decimal("20.00")
        assert record.freight is None

        with pytest.raises(BizException) as exc:
            after_sale_service.submit_return(
                db,
                return_id=record.id,
                staff=db.get(Staff, 1),
            )
        assert "必须绑定退货登记" in exc.value.message
        db.rollback()

        register = after_sale_service.create_register(
            db,
            data={
                "return_order_id": record.id,
                "logistics_company": "顺丰",
                "tracking_no": "SF-RETURN-1",
                "sender": "张三",
                "sender_phone": "13800000000",
                "product_info": "测试商品 x 2",
            },
            staff=db.get(Staff, 1),
        )
        assert register.return_order_id == record.id

        after_sale_service.submit_return(
            db,
            return_id=record.id,
            staff=db.get(Staff, 1),
        )
        finance = db.get(Staff, 2)
        clear_scope_policy(db)
        initialize_scope_policy(db, finance)
        confirmed = after_sale_service.confirm_return(
            db,
            return_id=record.id,
            location_id=1,
            staff=finance,
            comment="财务确认",
        )

        assert confirmed.status == "CONFIRMED"
        assert confirmed.is_arrived is True
        assert confirmed.audit_time is not None
        assert confirmed.stock_in_id is not None
        stock_in = db.get(StockIn, confirmed.stock_in_id)
        assert stock_in is not None
        assert stock_in.stock_in_type == "AFTER_SALE"
        stock = db.scalar(
            select(StockLocation).where(
                StockLocation.location_id == 1,
                StockLocation.product_sku_id == 1,
            )
        )
        assert stock is not None
        assert stock.qty == Decimal("2.000")
        flow = db.scalar(
            select(StockFlow).where(StockFlow.source_no == stock_in.stock_in_no)
        )
        assert flow is not None
        assert flow.direction == "IN"
        assert flow.biz_type == "AFTER_SALE"

        voucher = db.scalar(
            select(Voucher).where(
                Voucher.reference == f"采购入库单:{stock_in.stock_in_no}"
            )
        )
        assert voucher is not None
        voucher_detail = voucher_service.voucher_dict(db, voucher)
        assert [item["subject_code"] for item in voucher_detail["items"]] == [
            "1405",
            "6401",
        ]


def test_return_freight_empty_and_explicit_zero_are_distinct(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(db)
        record = _create_return(db, order, freight=None)
        assert record.refund_amount == Decimal("20.00")
        assert record.freight is None

        after_sale_service.update_return(
            db,
            return_id=record.id,
            data={"freight": Decimal("5.00")},
            staff=db.get(Staff, 1),
        )
        assert record.refund_amount == Decimal("25.00")

        after_sale_service.update_return(
            db,
            return_id=record.id,
            data={"freight": Decimal("0.00")},
            staff=db.get(Staff, 1),
        )
        assert record.freight == Decimal("0.00")
        assert record.refund_amount == Decimal("20.00")

        after_sale_service.update_return(
            db,
            return_id=record.id,
            data={"freight": None},
            staff=db.get(Staff, 1),
        )
        assert record.freight is None
        assert record.refund_amount == Decimal("20.00")


def test_exchange_binding_and_compensation_validation(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(db)
        with pytest.raises(BizException) as exc:
            after_sale_service.create_exchange(
                db,
                data={
                    "order_id": order.id,
                    "compensate_amount": Decimal("3.00"),
                    "items": [
                        {
                            "product_id": 1,
                            "sku_id": 1,
                            "exchange_qty": Decimal("1.000"),
                        }
                    ],
                },
                staff=db.get(Staff, 1),
            )
        assert "必须选择收入或支出" in exc.value.message
        db.rollback()

        exchange = after_sale_service.create_exchange(
            db,
            data={
                "order_id": order.id,
                "reason": "尺码换货",
                "is_cod": True,
                "is_return": True,
                "compensate_order_no": "BC-001",
                "compensate_amount": Decimal("3.00"),
                "compensate_direction": "INCOME",
                "items": [
                    {
                        "product_id": 1,
                        "sku_id": 1,
                        "exchange_qty": Decimal("1.000"),
                    }
                ],
            },
            staff=db.get(Staff, 1),
        )
        assert exchange.compensate_direction == "INCOME"

        with pytest.raises(BizException) as submit_exc:
            after_sale_service.submit_exchange(
                db,
                exchange_id=exchange.id,
                staff=db.get(Staff, 1),
            )
        assert "必须绑定退货登记" in submit_exc.value.message
        db.rollback()

        after_sale_service.create_register(
            db,
            data={
                "exchange_order_id": exchange.id,
                "logistics_company": "中通",
                "tracking_no": "ZT-RETURN-1",
                "sender": "张三",
                "sender_phone": "13800000000",
            },
            staff=db.get(Staff, 1),
        )
        submitted = after_sale_service.submit_exchange(
            db,
            exchange_id=exchange.id,
            staff=db.get(Staff, 1),
        )
        assert submitted.status == "SUBMITTED"


def test_direct_return_list_logistics_cancel_and_export(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        order = _make_order(db)
        record = _create_return(db, order, freight=Decimal("5.00"))
        after_sale_service.update_return_logistics(
            db,
            return_id=record.id,
            logistics_company="圆通",
            tracking_no="YT-001",
            staff=db.get(Staff, 1),
        )
        after_sale_service.create_register(
            db,
            data={
                "return_order_id": record.id,
                "logistics_company": "圆通",
                "tracking_no": "YT-001",
            },
            staff=db.get(Staff, 1),
        )
        listed = after_sale_service.list_returns(
            db,
            keyword="YT-001",
        )
        assert listed["total"] == 1
        assert listed["items"][0]["order_no"] == order.order_no
        assert listed["items"][0]["refund_amount"] == Decimal("25.00")
        assert listed["items"][0]["register"]["tracking_no"] == "YT-001"

        exported = after_sale_service.export_returns(db, keyword="YT-001")
        assert exported[:2] == b"PK"

        cancelled = after_sale_service.cancel_return(
            db,
            return_id=record.id,
            reason="买家撤销",
            staff=db.get(Staff, 1),
        )
        assert cancelled.status == "CANCELLED"
        with pytest.raises(BizException):
            after_sale_service.confirm_return(
                db,
                return_id=record.id,
                location_id=1,
                staff=db.get(Staff, 2),
            )


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
    app.include_router(after_sale_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(staff_id, name, [])}"
    }


def test_api_after_sale_permissions(monkeypatch):
    _serial_patch(monkeypatch)
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        _make_order(db)
    client = TestClient(app)

    denied = client.post(
        "/after-sales/returns",
        json={
            "order_id": 1,
            "warehouse_id": 1,
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": "1.000",
                    "refund_amount": "10.00",
                }
            ],
        },
        headers=_auth(2, "财务"),
    )
    assert denied.status_code == 403

    created = client.post(
        "/after-sales/returns",
        json={
            "order_id": 1,
            "warehouse_id": 1,
            "freight": None,
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": "1.000",
                    "refund_amount": "10.00",
                }
            ],
        },
        headers=_auth(1, "售后"),
    )
    assert created.status_code == 200
    return_id = created.json()["data"]["id"]

    register = client.post(
        "/after-sales/registers",
        json={
            "return_order_id": return_id,
            "logistics_company": "顺丰",
            "tracking_no": "SF-API-001",
        },
        headers=_auth(1, "售后"),
    )
    assert register.status_code == 200

    submitted = client.post(
        f"/after-sales/returns/{return_id}/submit",
        headers=_auth(1, "售后"),
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["status"] == "SUBMITTED"

    listed = client.get(
        "/after-sales/returns",
        headers=_auth(2, "财务"),
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    confirmed = client.post(
        f"/after-sales/returns/{return_id}/confirm",
        json={"location_id": 1, "comment": "财务确认"},
        headers=_auth(2, "财务"),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "CONFIRMED"
    assert confirmed.json()["data"]["stock_in_no"] is not None

    assert db_count(Session, ReturnOrder) == 1
    assert db_count(Session, ReturnRegister) == 1


def db_count(Session, model) -> int:
    with Session() as db:
        return int(db.scalar(select(func.count(model.id))) or 0)
