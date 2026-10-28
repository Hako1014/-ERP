from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.stock_out import router as stock_out_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.finance import Income
from app.models.org import Company, Staff
from app.models.party import Supplier
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseOrder, PurchaseOrderItem
from app.models.rbac import Role, StaffRole
from app.models.stock import StockFlow, StockOut, StockOutItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import (
    approval_service,
    purchase_return_service,
    stock_out_service,
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
            Staff(id=2, company_id=1, name="审核员", phone="13900000002"),
            Staff(id=3, company_id=1, name="质检", phone="13900000003"),
            Staff(id=4, company_id=1, name="财务", phone="13900000004"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add(
        Product(
            id=1,
            name="测试商品",
            code="P0001",
            main_unit_id=1,
            cost_price=Decimal("10.00"),
        )
    )
    db.add(
        ProductSku(
            id=1,
            product_id=1,
            spec_name="默认",
            spec_code="P0001-01",
            cost_price=Decimal("10.00"),
            is_active=True,
        )
    )
    db.add_all(
        [
            Warehouse(
                id=1,
                code="WH01",
                name="主仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                is_virtual=False,
                lock_type="NONE",
            ),
            Warehouse(
                id=2,
                code="WH02",
                name="虚拟仓",
                warehouse_type="FINISHED",
                is_enabled=True,
                is_virtual=True,
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
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD20260923-0001",
            supplier_id=1,
            warehouse_id=1,
            status="RECEIVED",
            audit_status="APPROVED",
            total_qty=Decimal("10.000"),
            total_amount=Decimal("100.00"),
            arrived_qty=Decimal("10.000"),
            created_by=1,
        )
    )
    db.add(
        PurchaseOrderItem(
            id=1,
            order_id=1,
            product_id=1,
            sku_id=1,
            unit_id=1,
            purchase_price=Decimal("10.00"),
            qty=Decimal("10.000"),
            in_transit_qty=Decimal("0.000"),
            arrived_qty=Decimal("10.000"),
            amount=Decimal("100.00"),
        )
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
                qty=Decimal("5.000"),
                status="NORMAL",
            ),
            StockLocation(
                id=3,
                location_id=3,
                product_sku_id=1,
                qty=Decimal("7.000"),
                status="NORMAL",
            ),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="WAREHOUSE", name="仓库"),
            Role(id=2, company_id=1, code="ADMIN", name="管理员"),
            Role(id=3, company_id=1, code="QUALITY", name="质检"),
            Role(id=4, company_id=1, code="FINANCE", name="财务"),
        ]
    )
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=1),
            StaffRole(staff_id=2, role_id=2),
            StaffRole(staff_id=3, role_id=3),
            StaffRole(staff_id=4, role_id=4),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flows(db) -> None:
    approval_service.create_flow(
        db,
        name="采购退货审批",
        doc_type=purchase_return_service.DOC_TYPE,
        approvers=[
            {"step": 1, "name": "财务", "approver_ids": [4]},
            {"step": 2, "name": "仓库", "approver_ids": [1]},
        ],
        actor=db.get(Staff, 1),
    )
    approval_service.create_flow(
        db,
        name="采购出库审批",
        doc_type=stock_out_service.DOC_TYPE,
        approvers=[2],
        actor=db.get(Staff, 1),
    )


def _patch_serials(monkeypatch) -> None:
    counters: dict[str, int] = {}

    def fake_serial(prefix, dt=None):
        code = prefix.value if hasattr(prefix, "value") else str(prefix)
        counters[code] = counters.get(code, 0) + 1
        return f"{code}20260923-{counters[code]:04d}"

    monkeypatch.setattr(stock_out_service, "generate_serial", fake_serial)
    monkeypatch.setattr(
        purchase_return_service, "generate_serial", fake_serial
    )


def _manual_return(
    db,
    *,
    qty: str = "3.000",
    unit_price: str = "10.00",
) -> tuple:
    record = purchase_return_service.create_return(
        db,
        data={
            "order_id": 1,
            "warehouse_id": 1,
            "auto_out": False,
            "return_reason": "质量问题",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "unit_id": 1,
                    "unit_price": unit_price,
                    "return_qty": qty,
                }
            ],
        },
        staff=db.get(Staff, 1),
    )
    purchase_return_service.submit_return(
        db,
        return_id=record.id,
        staff=db.get(Staff, 1),
    )
    purchase_return_service.approve_return(
        db,
        return_id=record.id,
        staff=db.get(Staff, 4),
    )
    purchase_return_service.approve_return(
        db,
        return_id=record.id,
        staff=db.get(Staff, 1),
    )
    stock_out = db.scalar(
        select(StockOut).where(StockOut.purchase_return_id == record.id)
    )
    assert stock_out is not None
    return record, stock_out


def _stock_out_items(db, stock_out_id: int) -> list[StockOutItem]:
    return list(
        db.scalars(
            select(StockOutItem)
            .where(StockOutItem.stock_out_id == stock_out_id)
            .order_by(StockOutItem.id)
        )
    )


def test_manual_return_outbound_splits_locations_and_finalizes(
    monkeypatch,
):
    _patch_serials(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        record, stock_out = _manual_return(db, qty="3.000")

        assert stock_out.status == "PENDING"
        assert stock_out.audit_status == "PENDING"
        assert stock_out.off_shelf_status == "NONE"
        assert stock_out.total_qty == Decimal("3.000")

        listed = stock_out_service.list_stock_outs(
            db,
            purchase_return_id=record.id,
            keyword=record.return_no,
        )
        assert listed["total"] == 1
        assert listed["items"][0]["purchase_return_no"] == record.return_no

        updated = stock_out_service.update_stock_out(
            db,
            stock_out_id=stock_out.id,
            data={"remark": "按批次下架"},
            staff=db.get(Staff, 1),
        )
        assert updated.remark == "按批次下架"

        stock_out_service.submit_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 1),
        )
        approved = stock_out_service.approve_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 2),
            comment="同意出库",
        )
        assert approved.status == "APPROVED"
        assert approved.audit_status == "APPROVED"

        source_item = _stock_out_items(db, stock_out.id)[0]
        completed = stock_out_service.off_shelf_stock_out(
            db,
            stock_out_id=stock_out.id,
            data={
                "items": [
                    {
                        "stock_out_item_id": source_item.id,
                        "location_id": 1,
                        "qty": "2.000",
                    },
                    {
                        "stock_out_item_id": source_item.id,
                        "location_id": 2,
                        "qty": "1.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert completed.off_shelf_status == "DONE"
        assert db.get(StockLocation, 1).qty == Decimal("8.000")
        assert db.get(StockLocation, 2).qty == Decimal("4.000")
        assert db.get(StockLocation, 3).qty == Decimal("7.000")

        outbound_items = _stock_out_items(db, stock_out.id)
        assert [(row.location_id, row.qty) for row in outbound_items] == [
            (1, Decimal("2.000")),
            (2, Decimal("1.000")),
        ]

        flows = list(
            db.scalars(
                select(StockFlow)
                .where(StockFlow.source_no == stock_out.stock_out_no)
                .order_by(StockFlow.location_id)
            )
        )
        assert [(row.direction, row.biz_type) for row in flows] == [
            ("OUT", "PURCHASE_RETURN"),
            ("OUT", "PURCHASE_RETURN"),
        ]
        assert [row.qty for row in flows] == [
            Decimal("2.000"),
            Decimal("1.000"),
        ]
        assert [row.total_cost for row in flows] == [
            Decimal("20.00"),
            Decimal("10.00"),
        ]

        refreshed = purchase_return_service.get_return_or_404(db, record.id)
        assert refreshed.returned_qty == Decimal("3.000")
        income = db.scalar(
            select(Income).where(
                Income.description == f"采购退货退款:{record.return_no}"
            )
        )
        assert income is not None
        assert income.amount == Decimal("30.00")
        assert income.status == "APPROVED"


def test_off_shelf_quantity_and_stock_errors_do_not_mutate(
    monkeypatch,
):
    _patch_serials(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        _, stock_out = _manual_return(db, qty="6.000")
        stock_out_service.submit_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 1),
        )
        stock_out_service.approve_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 2),
        )
        source_item = _stock_out_items(db, stock_out.id)[0]

        with pytest.raises(BizException) as missing:
            stock_out_service.off_shelf_stock_out(
                db,
                stock_out_id=stock_out.id,
                data={
                    "items": [
                        {
                            "stock_out_item_id": source_item.id,
                            "location_id": 1,
                            "qty": "5.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "合计必须等于" in missing.value.message
        assert db.get(StockLocation, 1).qty == Decimal("10.000")
        assert db.get(StockLocation, 2).qty == Decimal("5.000")
        assert len(_stock_out_items(db, stock_out.id)) == 1
        assert db.scalar(select(func.count(StockFlow.id))) == 0

        with pytest.raises(BizException) as insufficient:
            stock_out_service.off_shelf_stock_out(
                db,
                stock_out_id=stock_out.id,
                data={
                    "items": [
                        {
                            "stock_out_item_id": source_item.id,
                            "location_id": 2,
                            "qty": "6.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "超过库位库存" in insufficient.value.message
        assert db.get(StockLocation, 2).qty == Decimal("5.000")
        assert len(_stock_out_items(db, stock_out.id)) == 1
        assert db.scalar(select(func.count(StockFlow.id))) == 0

        completed = stock_out_service.off_shelf_stock_out(
            db,
            stock_out_id=stock_out.id,
            data={
                "items": [
                    {
                        "stock_out_item_id": source_item.id,
                        "location_id": 2,
                        "qty": "5.000",
                    },
                    {
                        "stock_out_item_id": source_item.id,
                        "location_id": 1,
                        "qty": "1.000",
                    },
                ]
            },
            staff=db.get(Staff, 1),
        )
        assert completed.off_shelf_status == "DONE"
        assert db.get(StockLocation, 2).qty == Decimal("0.000")
        assert db.get(StockLocation, 1).qty == Decimal("9.000")


def test_off_shelf_rejects_foreign_location_and_duplicate_overrun(
    monkeypatch,
):
    _patch_serials(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        _, stock_out = _manual_return(db, qty="3.000")
        stock_out_service.submit_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 1),
        )
        stock_out_service.approve_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 2),
        )
        source_item = _stock_out_items(db, stock_out.id)[0]

        with pytest.raises(BizException) as foreign:
            stock_out_service.off_shelf_stock_out(
                db,
                stock_out_id=stock_out.id,
                data={
                    "items": [
                        {
                            "stock_out_item_id": source_item.id,
                            "location_id": 3,
                            "qty": "3.000",
                        }
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "不属于出库仓库" in foreign.value.message

        with pytest.raises(BizException) as duplicate:
            stock_out_service.off_shelf_stock_out(
                db,
                stock_out_id=stock_out.id,
                data={
                    "items": [
                        {
                            "stock_out_item_id": source_item.id,
                            "location_id": 1,
                            "qty": "2.000",
                        },
                        {
                            "stock_out_item_id": source_item.id,
                            "location_id": 1,
                            "qty": "2.000",
                        },
                    ]
                },
                staff=db.get(Staff, 1),
            )
        assert "超过应出库数量" in duplicate.value.message
        assert db.get(StockLocation, 1).qty == Decimal("10.000")
        assert db.scalar(select(func.count(StockFlow.id))) == 0
        assert len(_stock_out_items(db, stock_out.id)) == 1


def test_stock_out_crud_and_submit_delete_rules(monkeypatch):
    _patch_serials(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        record, stock_out = _manual_return(db, qty="2.000")

        with pytest.raises(BizException) as duplicate:
            stock_out_service.create_stock_out(
                db,
                data={"purchase_return_id": record.id},
                staff=db.get(Staff, 1),
            )
        assert "已生成出库单" in duplicate.value.message

        stock_out_service.delete_stock_out(
            db,
            stock_out_id=stock_out.id,
            staff=db.get(Staff, 1),
        )
        assert db.get(StockOut, stock_out.id) is None
        assert purchase_return_service.get_return_stock_out(
            db, record.id
        ) is None

        recreated = stock_out_service.create_stock_out(
            db,
            data={
                "purchase_return_id": record.id,
                "remark": "重新创建",
            },
            staff=db.get(Staff, 1),
        )
        assert recreated.remark == "重新创建"
        assert recreated.total_qty == Decimal("2.000")

        stock_out_service.submit_stock_out(
            db,
            stock_out_id=recreated.id,
            staff=db.get(Staff, 1),
        )
        with pytest.raises(BizException) as delete_submitted:
            stock_out_service.delete_stock_out(
                db,
                stock_out_id=recreated.id,
                staff=db.get(Staff, 1),
            )
        assert "审批流程" in delete_submitted.value.message


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
    app.include_router(stock_out_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_stock_out_permissions_and_full_off_shelf(monkeypatch):
    _patch_serials(monkeypatch)
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        record, stock_out = _manual_return(db, qty="3.000")

        draft_record, draft_stock_out = _manual_return(db, qty="1.000")
        stock_out_service.delete_stock_out(
            db,
            stock_out_id=draft_stock_out.id,
            staff=db.get(Staff, 1),
        )

    client = TestClient(app)
    warehouse_headers = _auth(1, "仓库")
    admin_headers = _auth(2, "审核员")
    quality_headers = _auth(3, "质检")

    listed = client.get("/stock-outs", headers=quality_headers)
    assert listed.status_code == 200
    # 未配置数据范围的质检员默认只能看到本人制单记录。
    assert listed.json()["data"]["total"] == 0

    denied_create = client.post(
        "/stock-outs",
        json={"purchase_return_id": draft_record.id},
        headers=quality_headers,
    )
    assert denied_create.status_code == 403

    created = client.post(
        "/stock-outs",
        json={"purchase_return_id": draft_record.id},
        headers=warehouse_headers,
    )
    assert created.status_code == 200
    assert created.json()["data"]["remark"] == (
        f"采购退货出库:{draft_record.return_no}"
    )

    submitted = client.post(
        f"/stock-outs/{stock_out.id}/submit",
        headers=warehouse_headers,
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["approval"]["state"][
        "current_step"
    ] == 1

    approved = client.post(
        f"/stock-outs/{stock_out.id}/approve",
        json={"comment": "同意"},
        headers=admin_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"
    item_id = approved.json()["data"]["items"][0]["id"]

    completed = client.post(
        f"/stock-outs/{stock_out.id}/off-shelf",
        json={
            "items": [
                {
                    "stock_out_item_id": item_id,
                    "location_id": 1,
                    "qty": "3.000",
                }
            ]
        },
        headers=warehouse_headers,
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["off_shelf_status"] == "DONE"
    assert completed.json()["data"]["items"][0]["location_id"] == 1

    with Session() as db:
        initialize_scope_policy(db, db.get(Staff, 1))
        refreshed = purchase_return_service.get_return_or_404(db, record.id)
        assert refreshed.returned_qty == Decimal("3.000")
        assert db.get(StockLocation, 1).qty == Decimal("7.000")
        assert db.scalar(select(func.count(Income.id))) == 1
