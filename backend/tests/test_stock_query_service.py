from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.stock_query import router as stock_query_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.org import Company, Staff
from app.models.product import Product, ProductSku
from app.models.rbac import Role, StaffRole
from app.models.stock import (
    LocationAdjust,
    LocationAdjustItem,
    StockCost,
    StockFlow,
    StockOut,
    StockOutItem,
    StockWarning,
    Transfer,
    TransferOut,
    TransferOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, stock_query_service


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
            Staff(id=2, company_id=1, name="审核员", phone="13900000002"),
            Staff(id=3, company_id=1, name="质检", phone="13900000003"),
        ]
    )
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add_all(
        [
            Product(
                id=1,
                name="主商品",
                code="P0001",
                main_unit_id=1,
                cost_price=Decimal("10.00"),
                warn_stock=10,
            ),
            Product(
                id=2,
                name="配件商品",
                code="P0002",
                main_unit_id=1,
                cost_price=Decimal("5.00"),
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
                cost_price=Decimal("10.00"),
                is_active=True,
            ),
            ProductSku(
                id=2,
                product_id=2,
                spec_name="默认",
                spec_code="P0002-01",
                cost_price=Decimal("5.00"),
                is_active=True,
            ),
        ]
    )
    db.add_all(
        [
            Warehouse(
                id=1,
                code="WH01",
                name="主仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                enable_location=True,
                lock_type="NONE",
            ),
            Warehouse(
                id=2,
                code="WH02",
                name="次品仓",
                warehouse_type="DEFECTIVE",
                is_enabled=True,
                enable_location=True,
                lock_type="NONE",
            ),
        ]
    )
    db.add_all(
        [
            Location(id=1, warehouse_id=1, code="WH01-A01", is_enabled=True),
            Location(id=2, warehouse_id=1, code="WH01-A02", is_enabled=True),
            Location(id=3, warehouse_id=2, code="WH02-A01", is_enabled=True),
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
                product_sku_id=1,
                qty=Decimal("4.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=3,
                location_id=3,
                product_sku_id=2,
                qty=Decimal("1.000"),
                status="DEFECTIVE",
            ),
            StockLocation(
                id=4,
                location_id=1,
                product_sku_id=2,
                qty=Decimal("2.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=5,
                location_id=2,
                product_sku_id=2,
                qty=Decimal("1.000"),
                status="NORMAL",
            ),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=2, company_id=1, code="ADMIN", name="管理员"),
            Role(id=3, company_id=1, code="QUALITY", name="质检"),
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

    db.add(
        StockOut(
            id=1,
            stock_out_no="CKD20260923-0001",
            warehouse_id=1,
            stock_out_type="PURCHASE_RETURN",
            status="PENDING",
            audit_status="PENDING",
            off_shelf_status="NONE",
            total_qty=Decimal("2.000"),
        )
    )
    db.add(
        StockOutItem(
            id=1,
            stock_out_id=1,
            product_id=1,
            sku_id=1,
            qty=Decimal("2.000"),
        )
    )
    db.add(
        Transfer(
            id=1,
            transfer_no="DBD20260923-0001",
            from_warehouse_id=1,
            to_warehouse_id=2,
            status="APPROVED",
            audit_status="APPROVED",
            total_qty=Decimal("3.000"),
        )
    )
    db.add(
        TransferOut(
            id=1,
            transfer_out_no="DBC20260923-0001",
            transfer_id=1,
            warehouse_id=1,
            status="PENDING",
            audit_status="PENDING",
        )
    )
    db.add(
        TransferOutItem(
            id=1,
            transfer_out_id=1,
            product_id=1,
            sku_id=1,
            qty=Decimal("3.000"),
        )
    )
    db.commit()
    approval_service.create_flow(
        db,
        name="库位调整审批",
        doc_type=stock_query_service.ADJUST_DOC_TYPE,
        approvers=[2],
        actor=db.get(Staff, 1),
    )


def _patch_serial(monkeypatch) -> None:
    counters: dict[str, int] = {}

    def fake_serial(prefix, dt=None):
        code = prefix.value if hasattr(prefix, "value") else str(prefix)
        counters[code] = counters.get(code, 0) + 1
        return f"{code}20260923-{counters[code]:04d}"

    monkeypatch.setattr(
        stock_query_service,
        "generate_serial",
        fake_serial,
    )


def _create_adjust(
    db,
    *,
    qty: str = "3.000",
    from_location_id: int = 1,
    to_location_id: int = 2,
):
    return stock_query_service.create_location_adjust(
        db,
        data={
            "warehouse_id": 1,
            "remark": "测试库位调整",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "from_location_id": from_location_id,
                    "to_location_id": to_location_id,
                    "qty": qty,
                }
            ],
        },
        staff=db.get(Staff, 1),
    )


def test_stock_flow_and_summary_use_consistent_stock_scope():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                StockFlow(
                    id=1,
                    warehouse_id=1,
                    product_id=1,
                    sku_id=1,
                    location_id=1,
                    unit_id=1,
                    direction="IN",
                    biz_type="MANUAL_IN",
                    qty=Decimal("2.000"),
                    cost_price=Decimal("10.00"),
                    total_cost=Decimal("20.00"),
                    flow_time=datetime(2026, 9, 23, 1, 0, 0),
                ),
                StockFlow(
                    id=2,
                    warehouse_id=1,
                    product_id=1,
                    sku_id=1,
                    location_id=1,
                    unit_id=1,
                    direction="OUT",
                    biz_type="MANUAL_OUT",
                    qty=Decimal("1.000"),
                    cost_price=Decimal("10.00"),
                    total_cost=Decimal("10.00"),
                    flow_time=datetime(2026, 9, 23, 2, 0, 0),
                ),
            ]
        )
        db.commit()

        flows = stock_query_service.list_stock_flows(
            db,
            warehouse_id=1,
            direction="OUT",
            biz_type="manual_out",
            keyword="P0001",
        )
        assert flows["total"] == 1
        assert flows["items"][0]["product_name"] == "主商品"
        assert flows["items"][0]["total_cost"] == Decimal("10.00")

        summary = stock_query_service.list_stock_summary(
            db,
            warehouse_id=1,
            product_id=1,
        )
        assert summary["total"] == 1
        item = summary["items"][0]
        assert item["total_qty"] == Decimal("14.000")
        assert item["normal_qty"] == Decimal("14.000")
        assert item["defective_qty"] == Decimal("0.000")
        assert item["occupied_qty"] == Decimal("5.000")
        assert item["available_qty"] == Decimal("9.000")
        assert item["shortage_qty"] == Decimal("0.000")
        assert item["stock_cost"] == Decimal("140.00")

        below = stock_query_service.list_stock_summary(
            db,
            warehouse_id=1,
            below_warning=True,
            page=1,
            page_size=1,
        )
        assert below["total"] == 1
        assert len(below["items"]) == 1
        assert below["items"][0]["product_id"] == 2

        defective = stock_query_service.list_stock_summary(
            db,
            warehouse_id=2,
            product_id=2,
        )
        assert defective["items"][0]["normal_qty"] == Decimal("0.000")
        assert defective["items"][0]["defective_qty"] == Decimal("1.000")


def test_manual_stock_operations_and_capacity_rollback():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)

        inbound = stock_query_service.manual_in(
            db,
            data={
                "warehouse_id": 1,
                "location_id": 1,
                "product_id": 1,
                "sku_id": 1,
                "qty": "2.000",
                "cost_price": "8.00",
                "remark": "补录",
            },
            staff=staff,
        )
        assert inbound.biz_type == "MANUAL_IN"
        assert inbound.total_cost == Decimal("16.00")
        assert db.get(StockLocation, 1).qty == Decimal("12.000")

        outbound = stock_query_service.manual_out(
            db,
            data={
                "warehouse_id": 1,
                "location_id": 1,
                "product_id": 1,
                "sku_id": 1,
                "qty": "1.000",
            },
            staff=staff,
        )
        assert outbound.direction == "OUT"
        assert db.get(StockLocation, 1).qty == Decimal("11.000")

        adjusted = stock_query_service.set_location_stock(
            db,
            data={
                "warehouse_id": 1,
                "location_id": 1,
                "product_id": 1,
                "sku_id": 1,
                "target_qty": "15.000",
            },
            staff=staff,
        )
        assert adjusted is not None
        assert adjusted.biz_type == "ADJUST_IN"
        assert db.get(StockLocation, 1).qty == Decimal("15.000")

        with pytest.raises(BizException) as insufficient:
            stock_query_service.manual_out(
                db,
                data={
                    "warehouse_id": 1,
                    "location_id": 1,
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": "99.000",
                },
                staff=staff,
            )
        assert "库存不足" in insufficient.value.message
        assert db.get(StockLocation, 1).qty == Decimal("15.000")

        db.get(Location, 2).capacity = 5
        db.commit()
        with pytest.raises(BizException) as capacity:
            stock_query_service.manual_in(
                db,
                data={
                    "warehouse_id": 1,
                    "location_id": 2,
                    "product_id": 1,
                    "sku_id": 1,
                    "qty": "1.000",
                },
                staff=staff,
            )
        assert "容量不足" in capacity.value.message
        assert db.get(StockLocation, 2).qty == Decimal("4.000")


def test_warning_and_cost_refresh_persist_decimal_snapshots():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)

        warnings = stock_query_service.list_stock_warnings(
            db,
            only_below=True,
        )
        assert len(warnings) == 1
        assert warnings[0]["product_id"] == 2
        assert warnings[0]["current_qty"] == Decimal("4.000")
        assert warnings[0]["warn_qty"] == Decimal("5.000")

        refreshed = stock_query_service.refresh_stock_warnings(
            db,
            staff=staff,
        )
        assert len(refreshed) == 2
        assert db.scalar(select(func.count(StockWarning.id))) == 2

        costs = stock_query_service.list_stock_costs(db)
        assert costs[0]["total_cost"] == Decimal("140.00")
        assert costs[1]["total_cost"] == Decimal("20.00")
        stock_query_service.refresh_stock_costs(db, staff=staff)
        assert db.scalar(select(func.count(StockCost.id))) == 2
        saved = db.scalar(
            select(StockCost).where(
                StockCost.product_id == 1,
                StockCost.sku_id == 1,
            )
        )
        assert saved is not None
        assert saved.qty == Decimal("14.000")
        assert saved.total_cost == Decimal("140.00")


def test_location_adjust_approval_moves_stock_without_changing_total(
    monkeypatch,
):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        adjust = _create_adjust(db, qty="3.000")
        assert adjust.adjust_no.startswith("KWTZ20260923-")
        assert adjust.status == "PENDING"

        listed = stock_query_service.list_location_adjusts(
            db,
            warehouse_id=1,
            keyword=adjust.adjust_no,
        )
        assert listed["total"] == 1
        assert listed["items"][0]["items"][0]["qty"] == Decimal("3.000")

        submitted = stock_query_service.submit_location_adjust(
            db,
            adjust_id=adjust.id,
            staff=db.get(Staff, 1),
        )
        assert submitted.audit_status == "PENDING"
        approved = stock_query_service.approve_location_adjust(
            db,
            adjust_id=adjust.id,
            staff=db.get(Staff, 2),
            comment="同意",
        )
        assert approved.status == "APPROVED"
        assert approved.audit_status == "APPROVED"
        assert db.get(StockLocation, 1).qty == Decimal("7.000")
        assert db.get(StockLocation, 2).qty == Decimal("7.000")
        assert (
            db.scalar(
                select(func.coalesce(func.sum(StockLocation.qty), 0))
            )
            == Decimal("18.000")
        )

        flows = list(
            db.scalars(
                select(StockFlow)
                .where(StockFlow.source_no == adjust.adjust_no)
                .order_by(StockFlow.id)
            )
        )
        assert [(row.direction, row.biz_type) for row in flows] == [
            ("OUT", "ADJUST_OUT"),
            ("IN", "ADJUST_IN"),
        ]


def test_location_adjust_reject_edit_delete_and_validation():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)

        with pytest.raises(BizException) as foreign:
            _create_adjust(db, to_location_id=3)
        assert "不属于当前仓库" in foreign.value.message

        with pytest.raises(BizException) as aggregate_source:
            stock_query_service.create_location_adjust(
                db,
                data={
                    "warehouse_id": 1,
                    "items": [
                        {
                            "product_id": 1,
                            "sku_id": 1,
                            "from_location_id": 1,
                            "to_location_id": 2,
                            "qty": "6.000",
                        },
                        {
                            "product_id": 1,
                            "sku_id": 1,
                            "from_location_id": 1,
                            "to_location_id": 2,
                            "qty": "6.000",
                        },
                    ],
                },
                staff=staff,
            )
        assert "库存不足" in aggregate_source.value.message

        db.get(Location, 2).capacity = 6
        db.commit()
        with pytest.raises(BizException) as aggregate_capacity:
            stock_query_service.create_location_adjust(
                db,
                data={
                    "warehouse_id": 1,
                    "items": [
                        {
                            "product_id": 1,
                            "sku_id": 1,
                            "from_location_id": 1,
                            "to_location_id": 2,
                            "qty": "1.000",
                        },
                        {
                            "product_id": 2,
                            "sku_id": 2,
                            "from_location_id": 1,
                            "to_location_id": 2,
                            "qty": "1.000",
                        },
                    ],
                },
                staff=staff,
            )
        assert "容量不足" in aggregate_capacity.value.message

        db.get(Location, 2).capacity = 0
        db.commit()
        adjust = _create_adjust(db, qty="1.000")
        updated = stock_query_service.update_location_adjust(
            db,
            adjust_id=adjust.id,
            data={"remark": "已修改", "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "from_location_id": 1,
                    "to_location_id": 2,
                    "qty": "2.000",
                }
            ]},
            staff=staff,
        )
        assert updated.remark == "已修改"
        assert list(
            db.scalars(
                select(LocationAdjustItem).where(
                    LocationAdjustItem.adjust_id == adjust.id
                )
            )
        )[0].qty == Decimal("2.000")

        stock_query_service.submit_location_adjust(
            db,
            adjust_id=adjust.id,
            staff=staff,
        )
        rejected = stock_query_service.reject_location_adjust(
            db,
            adjust_id=adjust.id,
            staff=db.get(Staff, 2),
            comment="资料有误",
        )
        assert rejected.status == "REJECTED"
        assert rejected.audit_status == "REJECTED"

        stock_query_service.update_location_adjust(
            db,
            adjust_id=rejected.id,
            data={"remark": "驳回后修改"},
            staff=staff,
        )
        stock_query_service.delete_location_adjust(
            db,
            adjust_id=rejected.id,
            staff=staff,
        )
        assert db.get(LocationAdjust, rejected.id) is None


def test_location_adjust_rechecks_stock_before_execution(monkeypatch):
    _patch_serial(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        adjust = _create_adjust(db, qty="5.000")
        stock_query_service.submit_location_adjust(
            db,
            adjust_id=adjust.id,
            staff=db.get(Staff, 1),
        )
        stock_query_service.manual_out(
            db,
            data={
                "warehouse_id": 1,
                "location_id": 1,
                "product_id": 1,
                "sku_id": 1,
                "qty": "6.000",
            },
            staff=db.get(Staff, 1),
        )
        assert db.get(StockLocation, 1).qty == Decimal("4.000")

        with pytest.raises(BizException) as changed:
            stock_query_service.approve_location_adjust(
                db,
                adjust_id=adjust.id,
                staff=db.get(Staff, 2),
            )
        assert "库存不足" in changed.value.message
        assert db.get(StockLocation, 1).qty == Decimal("4.000")
        assert db.get(StockLocation, 2).qty == Decimal("4.000")
        assert db.get(LocationAdjust, adjust.id).status == "PENDING"
        assert (
            db.scalar(
                select(func.count(StockFlow.id)).where(
                    StockFlow.source_no == adjust.adjust_no
                )
            )
            == 0
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
    app.include_router(stock_query_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_stock_query_permissions_and_location_adjust_flow(
    monkeypatch,
):
    _patch_serial(monkeypatch)
    app, Session = _make_api()
    with Session() as db:
        _seed(db)

    client = TestClient(app)
    warehouse_headers = _auth(1, "仓库")
    admin_headers = _auth(2, "审核员")
    quality_headers = _auth(3, "质检")

    listed = client.get("/stock-summary", headers=quality_headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 3

    denied = client.post(
        "/stock/manual-in",
        json={
            "warehouse_id": 1,
            "location_id": 1,
            "product_id": 1,
            "sku_id": 1,
            "qty": "1.000",
        },
        headers=quality_headers,
    )
    assert denied.status_code == 403

    inbound = client.post(
        "/stock/manual-in",
        json={
            "warehouse_id": 1,
            "location_id": 1,
            "product_id": 1,
            "sku_id": 1,
            "qty": "1.000",
            "cost_price": "9.00",
        },
        headers=warehouse_headers,
    )
    assert inbound.status_code == 200
    assert inbound.json()["data"]["biz_type"] == "MANUAL_IN"

    created = client.post(
        "/location-adjusts",
        json={
            "warehouse_id": 1,
            "remark": "API 调整",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "from_location_id": 1,
                    "to_location_id": 2,
                    "qty": "2.000",
                }
            ],
        },
        headers=warehouse_headers,
    )
    assert created.status_code == 200
    adjust_id = created.json()["data"]["id"]

    submitted = client.post(
        f"/location-adjusts/{adjust_id}/submit",
        headers=warehouse_headers,
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["audit_status"] == "PENDING"

    denied_approve = client.post(
        f"/location-adjusts/{adjust_id}/approve",
        json={"comment": "越权"},
        headers=quality_headers,
    )
    assert denied_approve.status_code == 403

    approved = client.post(
        f"/location-adjusts/{adjust_id}/approve",
        json={"comment": "同意"},
        headers=admin_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"

    warnings = client.post(
        "/stock-warnings/refresh",
        headers=warehouse_headers,
    )
    assert warnings.status_code == 200
    assert len(warnings.json()["data"]) == 2

    costs = client.post(
        "/stock-costs/refresh",
        headers=warehouse_headers,
    )
    assert costs.status_code == 200
    assert len(costs.json()["data"]) == 2

    with Session() as db:
        assert db.get(StockLocation, 1).qty == Decimal("9.000")
        assert db.get(StockLocation, 2).qty == Decimal("6.000")
