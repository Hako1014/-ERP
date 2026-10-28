from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.wave import router as wave_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.order import WaveOrder
from app.models.org import Company, Staff
from app.models.party import Supplier
from app.models.platform import Logistics, Platform, Shop
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import order_service, wave_service


@pytest.fixture(autouse=True)
def isolated_serials(monkeypatch):
    """Keep SQLite wave tests independent of the optional Redis service."""
    order_sequence = count(1)
    wave_sequence = count(1)
    monkeypatch.setattr(
        order_service,
        "generate_serial",
        lambda prefix, dt=None: f"DD20260923-{next(order_sequence):04d}",
    )
    monkeypatch.setattr(
        wave_service,
        "generate_serial",
        lambda prefix, dt=None: f"BC20260923-{next(wave_sequence):04d}",
    )


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
            Staff(id=3, company_id=1, name="售后", phone="13900000003"),
        ]
    )
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add_all(
        [
            Supplier(id=1, code="S001", name="供应商一"),
            Supplier(id=2, code="S002", name="供应商二"),
        ]
    )
    db.add_all(
        [
            Product(
                id=1,
                name="商品一",
                code="P0001",
                main_unit_id=1,
                default_supplier_id=1,
                online_price=Decimal("10.00"),
            ),
            Product(
                id=2,
                name="商品二",
                code="P0002",
                main_unit_id=1,
                default_supplier_id=2,
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
    db.add_all(
        [
            Logistics(
                id=1,
                name="顺丰快递",
                company="顺丰",
                is_enabled=True,
            ),
            Logistics(
                id=2,
                name="圆通快递",
                company="圆通",
                is_enabled=True,
            ),
        ]
    )
    db.add_all(
        [
            Platform(id=1, name="淘宝", code="TAOBAO"),
            Shop(id=1, platform_id=1, name="旗舰店"),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=2, company_id=1, code="FINANCE", name="财务"),
            Role(id=3, company_id=1, code="AFTER_SALE", name="售后"),
            Role(id=4, company_id=1, code="ADMIN", name="管理员"),
        ]
    )
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=1),
            StaffRole(staff_id=2, role_id=2),
            StaffRole(staff_id=3, role_id=3),
        ]
    )
    db.commit()


def _make_order(
    db,
    *,
    order_no: str,
    product_id: int,
    sku_id: int,
    logistics_id: int,
    minutes: int,
) -> object:
    order = order_service.create_order(
        db,
        data={
            "order_no": order_no,
            "shop_id": 1,
            "warehouse_id": 1,
            "logistics_id": logistics_id,
            "receiver_name": "张三",
            "receiver_phone": "13800000000",
            "receiver_province": "浙江省",
            "receiver_city": "杭州市",
            "order_time": datetime(2026, 9, 23, 10, 0, 0)
            + timedelta(minutes=minutes),
            "pay_status": "PAID",
            "items": [
                {
                    "product_id": product_id,
                    "sku_id": sku_id,
                    "qty": "1.000",
                    "price": "10.00",
                }
            ],
        },
        staff=db.get(Staff, 1),
    )
    order.status = "PENDING"
    order.audit_status = "APPROVED"
    db.commit()
    return order


def _strategy(db, **overrides):
    payload = {
        "name": "默认波次",
        "logistics_id": 1,
        "warehouse_id": 1,
        "shop_ids": [1],
        "wave_order_count": 2,
        "load_no_type": "BY_EXPRESS",
        "sort_type": "DEFAULT",
        "is_enabled": True,
    }
    payload.update(overrides)
    return wave_service.create_wave_strategy(
        db,
        data=payload,
        staff=db.get(Staff, 1),
    )


def test_wave_strategy_crud_and_validation():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        strategy = _strategy(db)
        assert strategy.name == "默认波次"
        assert strategy.shop_ids == "1"
        assert strategy.wave_order_count == 2

        listed = wave_service.list_wave_strategies(db)
        assert listed["total"] == 1
        assert listed["items"][0]["shop_ids"] == [1]

        strategy = wave_service.update_wave_strategy(
            db,
            strategy_id=strategy.id,
            data={
                "name": "库位排序波次",
                "sort_type": "LOCATION",
                "wave_order_count": 3,
            },
            staff=staff,
        )
        assert strategy.name == "库位排序波次"
        assert strategy.sort_type == "LOCATION"
        assert strategy.wave_order_count == 3

        with pytest.raises(BizException) as duplicate_exc:
            wave_service.create_wave_strategy(
                db,
                data={"name": "库位排序波次"},
                staff=staff,
            )
        assert "名称已存在" in duplicate_exc.value.message

        with pytest.raises(BizException) as sort_exc:
            wave_service.update_wave_strategy(
                db,
                strategy_id=strategy.id,
                data={"sort_type": "PRICE"},
                staff=staff,
            )
        assert "排序条件" in sort_exc.value.message

        wave_service.delete_wave_strategy(
            db, strategy_id=strategy.id, staff=staff
        )
        assert wave_service.list_wave_strategies(db)["total"] == 0


def test_run_wave_chunks_remainder_and_is_idempotent(monkeypatch):
    serials = iter(["BC20260923-0001", "BC20260923-0002"])
    monkeypatch.setattr(
        wave_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        strategy = _strategy(db, wave_order_count=2)
        orders = [
            _make_order(
                db,
                order_no=f"TB-{index}",
                product_id=1,
                sku_id=1,
                logistics_id=1,
                minutes=index,
            )
            for index in range(1, 4)
        ]

        result = wave_service.run_wave_strategy(
            db,
            strategy_id=strategy.id,
            staff=db.get(Staff, 1),
        )
        assert result["wave_count"] == 2
        assert result["order_count"] == 3
        assert [item["total_order_count"] for item in result["waves"]] == [2, 1]
        assert [item["wave_no"] for item in result["waves"]] == [
            "BC20260923-0001",
            "BC20260923-0002",
        ]
        assert result["waves"][0]["load_numbers"][0]["load_no"].startswith(
            "BC20260923-0001-SF-"
        )

        assigned = set(
            db.scalars(select(WaveOrder.order_id))
        )
        assert assigned == {order.id for order in orders}

        second = wave_service.run_wave_strategy(
            db,
            strategy_id=strategy.id,
            staff=db.get(Staff, 1),
        )
        assert second["wave_count"] == 0
        assert second["order_count"] == 0


def test_load_number_grouped_by_express_type():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        strategy = _strategy(
            db,
            logistics_id=None,
            wave_order_count=10,
        )
        _make_order(
            db,
            order_no="TB-SF",
            product_id=1,
            sku_id=1,
            logistics_id=1,
            minutes=1,
        )
        _make_order(
            db,
            order_no="TB-YTO",
            product_id=2,
            sku_id=2,
            logistics_id=2,
            minutes=2,
        )

        result = wave_service.run_wave_strategy(
            db,
            strategy_id=strategy.id,
            staff=db.get(Staff, 1),
        )
        wave = result["waves"][0]
        assert wave["express_type"] == "混合快递"
        assert {
            row["express_type"] for row in wave["load_numbers"]
        } == {"顺丰快递", "圆通快递"}
        assert {
            row["load_no"].split("-")[-2]
            for row in wave["orders"]
        } == {"SF", "YTO"}


@pytest.mark.parametrize(
    ("sort_type", "expected_product_ids"),
    [
        ("LOCATION", [2, 1]),
        ("SUPPLIER", [1, 2]),
    ],
)
def test_wave_sorting_by_location_and_supplier(
    sort_type, expected_product_ids
):
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        strategy = _strategy(
            db,
            sort_type=sort_type,
            wave_order_count=10,
        )
        order_one = _make_order(
            db,
            order_no="TB-ONE",
            product_id=1,
            sku_id=1,
            logistics_id=1,
            minutes=2,
        )
        order_two = _make_order(
            db,
            order_no="TB-TWO",
            product_id=2,
            sku_id=2,
            logistics_id=1,
            minutes=1,
        )

        result = wave_service.run_wave_strategy(
            db,
            strategy_id=strategy.id,
            staff=db.get(Staff, 1),
        )
        wave = result["waves"][0]
        assert [row["id"] for row in wave["orders"]] == [
            order_two.id if expected_product_ids[0] == 2 else order_one.id,
            order_one.id if expected_product_ids[1] == 1 else order_two.id,
        ]


def test_wave_location_route_respects_picking_priority():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        db.get(Location, 1).priority = 10  # A-02 优先于编码靠前的 A-01
        strategy = _strategy(db, sort_type="LOCATION", wave_order_count=10)
        first = _make_order(
            db, order_no="ROUTE-1", product_id=1, sku_id=1,
            logistics_id=1, minutes=2,
        )
        second = _make_order(
            db, order_no="ROUTE-2", product_id=2, sku_id=2,
            logistics_id=1, minutes=1,
        )
        result = wave_service.run_wave_strategy(
            db, strategy_id=strategy.id, staff=db.get(Staff, 1),
        )
        assert [row["id"] for row in result["waves"][0]["orders"]] == [first.id, second.id]


def test_run_multiple_strategies_and_delete_constraints():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        sf_strategy = _strategy(
            db,
            name="顺丰波次",
            logistics_id=1,
            wave_order_count=10,
        )
        yto_strategy = _strategy(
            db,
            name="圆通波次",
            logistics_id=2,
            wave_order_count=10,
        )
        sf_order = _make_order(
            db,
            order_no="TB-SF-2",
            product_id=1,
            sku_id=1,
            logistics_id=1,
            minutes=1,
        )
        yto_order = _make_order(
            db,
            order_no="TB-YTO-2",
            product_id=2,
            sku_id=2,
            logistics_id=2,
            minutes=2,
        )

        result = wave_service.run_wave_strategies(
            db,
            strategy_ids=[sf_strategy.id, yto_strategy.id],
            staff=staff,
        )
        assert result["strategy_count"] == 2
        assert result["wave_count"] == 2
        assert result["order_count"] == 2
        assigned = set(db.scalars(select(WaveOrder.order_id)))
        assert assigned == {sf_order.id, yto_order.id}

        with pytest.raises(BizException) as delete_exc:
            wave_service.delete_wave_strategy(
                db, strategy_id=sf_strategy.id, staff=staff
            )
        assert "不能删除" in delete_exc.value.message

        wave_id = result["results"][0]["waves"][0]["id"]
        wave_service.delete_wave(db, wave_id=wave_id, staff=staff)
        assert (
            db.scalar(
                select(func.count(WaveOrder.id)).where(
                    WaveOrder.wave_id == wave_id
                )
            )
            == 0
        )
        wave_service.delete_wave_strategy(
            db, strategy_id=sf_strategy.id, staff=staff
        )
        remaining = wave_service.list_wave_strategies(db)
        assert remaining["total"] == 1
        assert remaining["items"][0]["id"] == yto_strategy.id


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
    app.include_router(wave_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(staff_id, name, [])}"
    }


def test_api_wave_permissions_and_run(monkeypatch):
    serials = iter(["BC20260923-1001"])
    monkeypatch.setattr(
        wave_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        _make_order(
            db,
            order_no="TB-API",
            product_id=1,
            sku_id=1,
            logistics_id=1,
            minutes=1,
        )
    client = TestClient(app)

    denied_create = client.post(
        "/wave-strategies",
        json={"name": "财务不可创建"},
        headers=_auth(2, "财务"),
    )
    assert denied_create.status_code == 403

    created = client.post(
        "/wave-strategies",
        json={
            "name": "接口波次",
            "logistics_id": 1,
            "warehouse_id": 1,
            "wave_order_count": 10,
        },
        headers=_auth(1, "仓库"),
    )
    assert created.status_code == 200
    strategy_id = created.json()["data"]["id"]

    listed = client.get("/wave-strategies", headers=_auth(2, "财务"))
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1

    pending = client.get(
        f"/wave-strategies/{strategy_id}/pending-count",
        headers=_auth(2, "财务"),
    )
    assert pending.status_code == 200
    assert pending.json()["data"]["pending_count"] == 1

    denied_run = client.post(
        "/waves/run",
        json={"strategy_ids": [strategy_id]},
        headers=_auth(2, "财务"),
    )
    assert denied_run.status_code == 403

    run = client.post(
        "/waves/run",
        json={"strategy_ids": [strategy_id]},
        headers=_auth(1, "仓库"),
    )
    assert run.status_code == 200
    data = run.json()["data"]
    assert data["wave_count"] == 1
    wave_id = data["results"][0]["waves"][0]["id"]

    detail = client.get(f"/waves/{wave_id}", headers=_auth(2, "财务"))
    assert detail.status_code == 200
    assert detail.json()["data"]["total_order_count"] == 1

    loads = client.get(
        f"/waves/{wave_id}/load-numbers",
        headers=_auth(2, "财务"),
    )
    assert loads.status_code == 200
    assert len(loads.json()["data"]) == 1
