from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.purchase_return import router as purchase_return_router
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
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
)
from app.models.rbac import Role, StaffRole
from app.models.stock import StockFlow, StockOut
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, purchase_return_service


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
            Staff(id=1, company_id=1, name="采购员", phone="13900000001"),
            Staff(id=2, company_id=1, name="财务", phone="13900000002"),
            Staff(id=3, company_id=1, name="商控", phone="13900000003"),
            Staff(id=4, company_id=1, name="仓库", phone="13900000004"),
            Staff(id=5, company_id=1, name="管理员", phone="13900000005"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add(
        Product(
            id=1,
            name="测试商品",
            code="P0001",
            art_no="ART-1",
            main_unit_id=1,
            default_supplier_id=1,
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
                name="实物仓",
                is_enabled=True,
                is_virtual=False,
                lock_type="NONE",
            ),
            Warehouse(
                id=2,
                code="WH02",
                name="虚拟仓",
                is_enabled=True,
                is_virtual=True,
                lock_type="NONE",
            ),
        ]
    )
    db.add_all(
        [
            Location(
                id=1,
                code="WH01-A01",
                warehouse_id=1,
                is_enabled=True,
                priority=1,
            ),
            Location(
                id=2,
                code="WH02-A01",
                warehouse_id=2,
                is_enabled=True,
                priority=1,
            ),
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
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="ADMIN", name="管理员"),
            Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
            Role(id=3, company_id=1, code="FINANCE", name="财务"),
            Role(id=4, company_id=1, code="QUALITY", name="商控"),
            Role(id=5, company_id=1, code="WAREHOUSE", name="仓库"),
        ]
    )
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=2),
            StaffRole(staff_id=2, role_id=3),
            StaffRole(staff_id=3, role_id=4),
            StaffRole(staff_id=4, role_id=5),
            StaffRole(staff_id=5, role_id=1),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flow(db) -> None:
    approval_service.create_flow(
        db,
        name="采购退货审批",
        doc_type=purchase_return_service.DOC_TYPE,
        approvers=[
            {"step": 1, "name": "财务", "approver_ids": [2]},
            {"step": 2, "name": "商控/仓库", "approver_ids": [3, 4]},
        ],
        actor=db.get(Staff, 1),
    )


def _item(qty: str = "3.000", price: str | None = "10.00") -> dict:
    data = {"product_id": 1, "sku_id": 1, "return_qty": qty}
    if price is not None:
        data["unit_price"] = price
    return data


def test_create_update_delete_and_order_quantity_limit(monkeypatch):
    serials = iter(
        [
            "CGTH20260923-0001",
            "CGTH20260923-0002",
            "CGTH20260923-0003",
        ]
    )
    monkeypatch.setattr(
        purchase_return_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        purchaser = db.get(Staff, 1)

        record = purchase_return_service.create_return(
            db,
            data={
                "order_id": 1,
                "warehouse_id": 1,
                "auto_out": True,
                "return_reason": "质量问题",
                "items": [_item("3.000", None)],
            },
            staff=purchaser,
        )
        assert record.return_no == "CGTH20260923-0001"
        assert record.supplier_id == 1
        assert record.total_qty == Decimal("3.000")
        assert record.refund_amount == Decimal("30.00")
        assert record.status == "PENDING"

        with pytest.raises(BizException) as over:
            purchase_return_service.create_return(
                db,
                data={
                    "order_id": 1,
                    "warehouse_id": 1,
                    "items": [_item("8.000")],
                },
                staff=purchaser,
            )
        assert "超过可退数量" in over.value.message

        record = purchase_return_service.update_return(
            db,
            return_id=record.id,
            data={"items": [_item("4.000")]},
            staff=purchaser,
        )
        assert record.total_qty == Decimal("4.000")
        assert record.refund_amount == Decimal("40.00")

        deletable = purchase_return_service.create_return(
            db,
            data={
                "supplier_id": 1,
                "warehouse_id": 1,
                "items": [_item("1.000")],
            },
            staff=purchaser,
        )
        purchase_return_service.delete_return(
            db, return_id=deletable.id, staff=purchaser
        )
        assert db.get(PurchaseReturn, deletable.id) is None

        terminated = purchase_return_service.create_return(
            db,
            data={
                "supplier_id": 1,
                "warehouse_id": 1,
                "items": [_item("1.000")],
            },
            staff=purchaser,
        )
        terminated = purchase_return_service.terminate_return(
            db,
            return_id=terminated.id,
            staff=purchaser,
            reason="不再退货",
        )
        assert terminated.status == "TERMINATED"


def test_physical_auto_outbound_finance_and_warehouse(monkeypatch):
    serials = iter(
        [
            "CGTH20260923-1001",
            "CKD20260923-1001",
            "SRD20260923-1001",
        ]
    )
    monkeypatch.setattr(
        purchase_return_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        finance = db.get(Staff, 2)
        quality = db.get(Staff, 3)
        warehouse = db.get(Staff, 4)

        record = purchase_return_service.create_return(
            db,
            data={
                "order_id": 1,
                "warehouse_id": 1,
                "auto_out": True,
                "items": [_item("3.000")],
            },
            staff=purchaser,
        )
        record = purchase_return_service.submit_return(
            db, return_id=record.id, staff=purchaser
        )
        record = purchase_return_service.approve_return(
            db, return_id=record.id, staff=finance, comment="财务同意"
        )
        assert record.status == "FINANCE_APPROVED"
        assert record.audit_status == "PENDING"

        with pytest.raises(BizException) as branch:
            purchase_return_service.approve_return(
                db, return_id=record.id, staff=quality
            )
        assert "仓库人员" in branch.value.message

        record = purchase_return_service.approve_return(
            db, return_id=record.id, staff=warehouse, comment="仓库同意"
        )
        assert record.status == "APPROVED"
        assert record.audit_status == "APPROVED"
        assert record.returned_qty == Decimal("3.000")
        assert db.get(StockLocation, 1).qty == Decimal("7.000")

        stock_out = db.scalar(
            select(StockOut).where(StockOut.purchase_return_id == record.id)
        )
        assert stock_out is not None
        assert stock_out.status == "APPROVED"
        assert stock_out.off_shelf_status == "DONE"
        assert stock_out.total_qty == Decimal("3.000")

        flows = list(
            db.scalars(
                select(StockFlow).where(
                    StockFlow.source_no == stock_out.stock_out_no
                )
            )
        )
        assert len(flows) == 1
        assert flows[0].direction == "OUT"
        assert flows[0].biz_type == "PURCHASE_RETURN"
        assert flows[0].qty == Decimal("3.000")
        assert flows[0].total_cost == Decimal("30.00")

        refund = db.scalar(
            select(Income).where(
                Income.description == f"采购退货退款:{record.return_no}"
            )
        )
        assert refund is not None
        assert refund.income_no == "SRD20260923-1001"
        assert refund.amount == Decimal("30.00")
        assert refund.status == "APPROVED"
        assert purchase_return_service.return_dict(db, record)[
            "refund_status"
        ] == "RECORDED"


def test_virtual_branch_and_manual_outbound_completion(monkeypatch):
    serials = iter(
        [
            "CGTH20260923-2001",
            "CKD20260923-2001",
            "SRD20260923-2001",
        ]
    )
    monkeypatch.setattr(
        purchase_return_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        finance = db.get(Staff, 2)
        quality = db.get(Staff, 3)
        warehouse = db.get(Staff, 4)

        record = purchase_return_service.create_return(
            db,
            data={
                "supplier_id": 1,
                "warehouse_id": 2,
                "auto_out": False,
                "items": [_item("2.000", "12.00")],
            },
            staff=purchaser,
        )
        record = purchase_return_service.submit_return(
            db, return_id=record.id, staff=purchaser
        )
        record = purchase_return_service.approve_return(
            db, return_id=record.id, staff=finance
        )

        with pytest.raises(BizException) as branch:
            purchase_return_service.approve_return(
                db, return_id=record.id, staff=warehouse
            )
        assert "商控人员" in branch.value.message

        record = purchase_return_service.approve_return(
            db, return_id=record.id, staff=quality
        )
        assert record.status == "APPROVED"
        assert record.returned_qty == Decimal("0.000")
        assert db.get(StockLocation, 2).qty == Decimal("5.000")
        stock_out = purchase_return_service.get_return_stock_out(db, record.id)
        assert stock_out is not None
        assert stock_out["status"] == "PENDING"
        assert stock_out["off_shelf_status"] == "NONE"

        record = purchase_return_service.complete_return_outbound(
            db, return_id=record.id, staff=warehouse
        )
        assert record.returned_qty == Decimal("2.000")
        assert db.get(StockLocation, 2).qty == Decimal("3.000")
        refund = purchase_return_service.get_return_refund(db, record.id)
        assert refund is not None
        assert refund["amount"] == Decimal("24.00")


def test_insufficient_stock_rolls_back_approval(monkeypatch):
    serials = iter(["CGTH20260923-3001", "CKD20260923-3001"])
    monkeypatch.setattr(
        purchase_return_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        finance = db.get(Staff, 2)
        warehouse = db.get(Staff, 4)

        record = purchase_return_service.create_return(
            db,
            data={
                "supplier_id": 1,
                "warehouse_id": 1,
                "auto_out": True,
                "items": [_item("11.000")],
            },
            staff=purchaser,
        )
        record = purchase_return_service.submit_return(
            db, return_id=record.id, staff=purchaser
        )
        purchase_return_service.approve_return(
            db, return_id=record.id, staff=finance
        )

        with pytest.raises(BizException) as insufficient:
            purchase_return_service.approve_return(
                db, return_id=record.id, staff=warehouse
            )
        assert "库存不足" in insufficient.value.message

        record = purchase_return_service.get_return_or_404(db, record.id)
        assert record.status == "FINANCE_APPROVED"
        assert record.audit_status == "PENDING"
        assert record.returned_qty == Decimal("0.000")
        assert db.get(StockLocation, 1).qty == Decimal("10.000")
        assert (
            db.scalar(
                select(StockOut).where(
                    StockOut.purchase_return_id == record.id
                )
            )
            is None
        )


def _make_api(monkeypatch):
    Session = _session_factory(pool=True)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(purchase_return_router)
    app.dependency_overrides[get_db] = override_get_db
    serials = iter(
        [
            "CGTH20260923-4001",
            "CKD20260923-4001",
            "SRD20260923-4001",
        ]
    )
    monkeypatch.setattr(
        purchase_return_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    return app, Session


def _seed_api(Session) -> None:
    with Session() as db:
        _seed(db)
        _create_flow(db)


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_return_permissions_outbound_and_refund(monkeypatch):
    app, Session = _make_api(monkeypatch)
    _seed_api(Session)
    client = TestClient(app)

    created = client.post(
        "/purchase-returns",
        json={
            "order_id": 1,
            "warehouse_id": 1,
            "auto_out": True,
            "items": [
                {
                    "product_id": 1,
                    "sku_id": 1,
                    "unit_price": "10.00",
                    "return_qty": "1.000",
                }
            ],
        },
        headers=_auth(1, "采购员"),
    )
    assert created.status_code == 200
    return_id = created.json()["data"]["id"]

    submitted = client.post(
        f"/purchase-returns/{return_id}/submit",
        headers=_auth(1, "采购员"),
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["approval"]["state"][
        "current_step"
    ] == 1

    finance = client.post(
        f"/purchase-returns/{return_id}/approve",
        json={"comment": "财务同意"},
        headers=_auth(2, "财务"),
    )
    assert finance.status_code == 200
    assert finance.json()["data"]["status"] == "FINANCE_APPROVED"

    denied = client.post(
        f"/purchase-returns/{return_id}/approve",
        json={"comment": "越级"},
        headers=_auth(3, "商控"),
    )
    assert denied.status_code == 403

    warehouse = client.post(
        f"/purchase-returns/{return_id}/approve",
        json={"comment": "仓库同意"},
        headers=_auth(4, "仓库"),
    )
    assert warehouse.status_code == 200
    assert warehouse.json()["data"]["status"] == "APPROVED"
    assert warehouse.json()["data"]["refund_income"]["status"] == "APPROVED"

    stock_out = client.get(
        f"/purchase-returns/{return_id}/stock-out",
        headers=_auth(1, "采购员"),
    )
    assert stock_out.status_code == 200
    assert stock_out.json()["data"]["off_shelf_status"] == "DONE"

    refund = client.get(
        f"/purchase-returns/{return_id}/refund",
        headers=_auth(2, "财务"),
    )
    assert refund.status_code == 200
    assert refund.json()["data"]["amount"] == "10.00"

    denied_unaudit = client.post(
        f"/purchase-returns/{return_id}/unaudit",
        json={"reason": "已出库"},
        headers=_auth(1, "采购员"),
    )
    assert denied_unaudit.status_code == 409
