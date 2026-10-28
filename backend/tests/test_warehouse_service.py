from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.warehouse import router as warehouse_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.models.warehouse import StockLocation
from app.services import warehouse_service
from app.services import third_warehouse_service


def test_third_warehouse_mock_sync_and_failure(monkeypatch):
    from app.integrations import third_warehouse

    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        warehouse = _create_warehouse(db, staff, sync_stock=True)
        result = third_warehouse_service.sync_warehouse(db, warehouse_id=warehouse.id, staff=staff)
        assert result["success"] is True
        assert result["warehouse_code"] == warehouse.code
        assert result["sku_count"] == 0

        class BrokenAdapter:
            def sync_warehouse(self, payload):
                raise ConnectionError("third warehouse unavailable")

        monkeypatch.setattr(third_warehouse_service, "get_third_warehouse_adapter", lambda: BrokenAdapter())
        with pytest.raises(BizException, match="第三方仓储同步调用失败"):
            third_warehouse_service.sync_warehouse(db, warehouse_id=warehouse.id, staff=staff)
        warehouse.sync_stock = False
        db.commit()
        with pytest.raises(BizException, match="仅启用且参与库存同步"):
            third_warehouse_service.sync_warehouse(db, warehouse_id=warehouse.id, staff=staff)


def _session_factory(pool: bool = False):
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if pool
        else {}
    )
    engine = create_engine("sqlite:///:memory:", future=True, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db) -> Staff:
    db.add(Company(id=1, full_name="示例公司"))
    db.add(Staff(id=1, company_id=1, name="仓管员", phone="13900000001"))
    db.commit()
    return db.get(Staff, 1)


def _create_warehouse(db, staff: Staff, **overrides):
    data = {
        "code": "HB",
        "name": "华东仓",
        "warehouse_type": "FINISHED",
        "enable_location": True,
    }
    data.update(overrides)
    return warehouse_service.create_warehouse(db, data=data, staff=staff)


def _create_zone(db, staff: Staff, warehouse_id: int, code: str = "MD02"):
    return warehouse_service.create_zone(
        db,
        data={"warehouse_id": warehouse_id, "code": code, "name": f"{code}区"},
        staff=staff,
    )


def test_warehouse_rules_default_lock_and_delete_guard():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        first = _create_warehouse(db, staff, is_default=True)
        second = _create_warehouse(db, staff, code="SH", name="上海仓")
        assert first.is_default is True

        warehouse_service.set_default_warehouse(
            db, warehouse_id=second.id, staff=staff
        )
        assert db.get(type(first), first.id).is_default is False
        assert second.is_default is True

        defective = _create_warehouse(
            db,
            staff,
            code="BAD",
            name="次品仓",
            warehouse_type="DEFECTIVE",
            sync_stock=True,
        )
        assert defective.sync_stock is False

        virtual = _create_warehouse(
            db,
            staff,
            code="VIRTUAL",
            name="虚拟仓",
            is_virtual=True,
            sync_stock=True,
        )
        assert virtual.sync_stock is False

        locked = warehouse_service.lock_warehouse(
            db, warehouse_id=second.id, staff=staff
        )
        assert locked.lock_type == "MANUAL"
        assert locked.is_default is False
        with pytest.raises(BizException) as locked_exc:
            warehouse_service.ensure_warehouse_selectable(db, second.id)
        assert "锁定" in locked_exc.value.message

        warehouse_service.unlock_warehouse(
            db, warehouse_id=second.id, staff=staff
        )
        assert warehouse_service.ensure_warehouse_selectable(db, second.id).id == second.id

        warehouse_service.set_warehouse_enabled(
            db,
            warehouse_id=second.id,
            enabled=False,
            staff=staff,
        )
        with pytest.raises(BizException) as disabled_exc:
            warehouse_service.ensure_warehouse_selectable(db, second.id)
        assert "停用" in disabled_exc.value.message

        zone = _create_zone(db, staff, first.id)
        warehouse_service.create_location(
            db,
            data={
                "warehouse_id": first.id,
                "zone_id": zone.id,
                "row_no": 1,
                "col_no": 1,
                "layer_no": 1,
            },
            staff=staff,
        )
        with pytest.raises(BizException) as delete_exc:
            warehouse_service.delete_warehouse(
                db, warehouse_id=first.id, staff=staff
            )
        assert "库区或库位" in delete_exc.value.message


def test_zone_and_location_code_generation_and_sorting():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        warehouse = _create_warehouse(db, staff)
        zone = _create_zone(db, staff, warehouse.id)
        assert warehouse.enable_location is True

        location = warehouse_service.create_location(
            db,
            data={
                "warehouse_id": warehouse.id,
                "zone_id": zone.id,
                "row_no": 1,
                "col_no": 1,
                "layer_no": 6,
                "priority": 5,
            },
            staff=staff,
        )
        assert location.code == "HB-MD02区-01行-01列-06层"

        generated = warehouse_service.generate_locations(
            db,
            data={
                "warehouse_id": warehouse.id,
                "zone_id": zone.id,
                "row_count": 2,
                "col_count": 1,
                "layer_count": 2,
                "priority": 1,
                "sort_order": 10,
            },
            staff=staff,
        )
        assert len(generated) == 4
        assert {row.code for row in generated} == {
            "HB-MD02区-01行-01列-01层",
            "HB-MD02区-01行-01列-02层",
            "HB-MD02区-02行-01列-01层",
            "HB-MD02区-02行-01列-02层",
        }
        ordered = warehouse_service.list_locations(db, warehouse_id=warehouse.id)
        assert ordered[0].id == location.id
        assert ordered[1].sort_order == 10

        updated = warehouse_service.update_location(
            db,
            location_id=location.id,
            data={"col_no": 2, "priority": 9},
            staff=staff,
        )
        assert updated.code == "HB-MD02区-01行-02列-06层"


def test_location_capacity_and_delete_reference_guard():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        warehouse = _create_warehouse(db, staff)
        zone = _create_zone(db, staff, warehouse.id)
        location = warehouse_service.create_location(
            db,
            data={
                "warehouse_id": warehouse.id,
                "zone_id": zone.id,
                "row_no": 1,
                "col_no": 1,
                "layer_no": 1,
                "capacity": 10,
            },
            staff=staff,
        )
        db.add(
            StockLocation(
                location_id=location.id,
                product_sku_id=1,
                qty=Decimal("7.000"),
                status="NORMAL",
            )
        )
        db.commit()

        state = warehouse_service.ensure_location_capacity(
            db, location.id, added_qty="3"
        )
        assert state["new_qty"] == Decimal("10.000")
        assert state["remaining_after"] == Decimal("0.000")

        with pytest.raises(BizException) as capacity_exc:
            warehouse_service.ensure_location_capacity(
                db, location.id, added_qty="4"
            )
        assert "容量不足" in capacity_exc.value.message

        with pytest.raises(BizException) as update_exc:
            warehouse_service.update_location(
                db,
                location_id=location.id,
                data={"capacity": 6},
                staff=staff,
            )
        assert "已用容量" in update_exc.value.message

        with pytest.raises(BizException) as delete_exc:
            warehouse_service.delete_location(
                db, location_id=location.id, staff=staff
            )
        assert "stock_location" in delete_exc.value.message

        unlimited = warehouse_service.create_location(
            db,
            data={
                "warehouse_id": warehouse.id,
                "zone_id": zone.id,
                "row_no": 1,
                "col_no": 1,
                "layer_no": 2,
                "capacity": 0,
            },
            staff=staff,
        )
        result = warehouse_service.ensure_location_capacity(
            db, unlimited.id, added_qty="999"
        )
        assert result["unlimited"] is True


def test_zone_requires_enabled_location_warehouse_and_delete_guard():
    Session = _session_factory()
    with Session() as db:
        staff = _seed(db)
        warehouse = _create_warehouse(
            db,
            staff,
            code="NO-LOC",
            enable_location=False,
        )
        with pytest.raises(BizException) as create_exc:
            _create_zone(db, staff, warehouse.id)
        assert "未开启库位" in create_exc.value.message

        warehouse.enable_location = True
        db.flush()
        zone = _create_zone(db, staff, warehouse.id)
        warehouse_service.create_location(
            db,
            data={
                "warehouse_id": warehouse.id,
                "zone_id": zone.id,
                "row_no": 1,
                "col_no": 1,
                "layer_no": 1,
            },
            staff=staff,
        )
        with pytest.raises(BizException) as delete_exc:
            warehouse_service.delete_zone(db, zone_id=zone.id, staff=staff)
        assert "已有库位" in delete_exc.value.message


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
    app.include_router(warehouse_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str = "staff") -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def _seed_roles(Session):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add_all(
            [
                Staff(id=1, company_id=1, name="仓管员", phone="13900000001"),
                Staff(id=2, company_id=1, name="质检员", phone="13900000002"),
            ]
        )
        db.add_all(
            [
                Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
                Role(id=2, company_id=1, code="QUALITY", name="质检"),
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


def test_api_warehouse_permissions_and_location_generation():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)
    warehouse_headers = _auth(1, "仓管员")
    quality_headers = _auth(2, "质检员")

    created = client.post(
        "/warehouses",
        json={
            "code": "HB",
            "name": "华东仓",
            "warehouse_type": "FINISHED",
            "enable_location": True,
        },
        headers=warehouse_headers,
    )
    assert created.status_code == 200
    warehouse_id = created.json()["data"]["id"]

    readable = client.get("/warehouses", headers=quality_headers)
    assert readable.status_code == 200
    denied = client.post(
        "/warehouses",
        json={"code": "SH", "name": "上海仓"},
        headers=quality_headers,
    )
    assert denied.status_code == 403

    zone = client.post(
        "/zones",
        json={"warehouse_id": warehouse_id, "code": "MD02", "name": "MD02区"},
        headers=warehouse_headers,
    )
    assert zone.status_code == 200
    zone_id = zone.json()["data"]["id"]

    generated = client.post(
        "/locations/generate",
        json={
            "warehouse_id": warehouse_id,
            "zone_id": zone_id,
            "row_count": 1,
            "col_count": 1,
            "layer_count": 2,
            "capacity": 0,
        },
        headers=warehouse_headers,
    )
    assert generated.status_code == 200
    assert generated.json()["data"]["generated_count"] == 2
    assert generated.json()["data"]["items"][0]["code"] == "HB-MD02区-01行-01列-01层"

    listed = client.get(
        f"/locations?warehouse_id={warehouse_id}",
        headers=quality_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 2
