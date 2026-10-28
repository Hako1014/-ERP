from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.purchase_invoice import router as purchase_invoice_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.basic import Unit
from app.models.org import Company, Staff
from app.models.party import Supplier
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseInvoice, PurchaseOrder
from app.models.rbac import Role, StaffRole
from app.models.stock import StockIn, StockInItem
from app.services import approval_service, purchase_invoice_service


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
            Staff(id=1, company_id=1, name="财务", phone="13900000001"),
            Staff(id=2, company_id=1, name="管理员", phone="13900000002"),
            Staff(id=3, company_id=1, name="采购员", phone="13900000003"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(Unit(id=1, code="PCS", name="件"))
    db.add(
        Product(
            id=1,
            code="P0001",
            name="测试商品",
            main_unit_id=1,
            cost_price=Decimal("10.00"),
        )
    )
    db.add(
        ProductSku(
            id=1,
            product_id=1,
            spec_code="P0001-01",
            spec_name="默认",
            cost_price=Decimal("10.00"),
            is_active=True,
        )
    )
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD20260923-0001",
            supplier_id=1,
            status="RECEIVED",
            audit_status="APPROVED",
            total_qty=Decimal("10.000"),
            total_amount=Decimal("100.00"),
            arrived_qty=Decimal("10.000"),
        )
    )
    db.add(
        StockIn(
            id=1,
            stock_in_no="RKD20260923-0001",
            warehouse_id=1,
            stock_in_type="PURCHASE",
            purchase_order_id=1,
            status="APPROVED",
            audit_status="APPROVED",
            total_qty=Decimal("10.000"),
        )
    )
    db.add_all(
        [
            StockInItem(
                id=1,
                stock_in_id=1,
                product_id=1,
                sku_id=1,
                qty=Decimal("2.000"),
                cost_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            StockInItem(
                id=2,
                stock_in_id=1,
                product_id=1,
                sku_id=1,
                qty=Decimal("8.000"),
                cost_price=Decimal("10.00"),
                amount=Decimal("80.00"),
            ),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="ADMIN", name="管理员"),
            Role(id=2, company_id=1, code="FINANCE", name="财务"),
            Role(id=3, company_id=1, code="PURCHASER", name="采购员"),
        ]
    )
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=2),
            StaffRole(staff_id=2, role_id=1),
            StaffRole(staff_id=3, role_id=3),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flow(db) -> None:
    approval_service.create_flow(
        db,
        name="采购发票审批",
        doc_type=purchase_invoice_service.DOC_TYPE,
        approvers=[1],
        actor=db.get(Staff, 1),
    )


def test_invoice_type_default_rates_and_tax_split(monkeypatch):
    serials = iter(
        [
            "CGFP20260923-0001",
            "CGFP20260923-0002",
            "CGFP20260923-0003",
            "CGFP20260923-0004",
        ]
    )
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        finance = db.get(Staff, 1)

        normal = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "NORMAL",
                "amount": "100.00",
            },
            staff=finance,
        )
        assert normal.tax_rate == Decimal("0.00")
        assert normal.tax_amount == Decimal("0.00")
        assert normal.amount - normal.tax_amount == Decimal("100.00")

        special = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "专用发票",
                "amount": "113.00",
            },
            staff=finance,
        )
        assert special.invoice_type == "SPECIAL"
        assert special.tax_rate == Decimal("13.00")
        assert special.tax_amount == Decimal("13.00")
        assert special.amount - special.tax_amount == Decimal("100.00")

        agricultural = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "AGRICULTURAL",
                "amount": "109.00",
            },
            staff=finance,
        )
        assert agricultural.tax_rate == Decimal("9.00")
        assert agricultural.tax_amount == Decimal("9.00")

        red = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "RED_SPECIAL",
                "amount": "-113.00",
            },
            staff=finance,
        )
        assert red.tax_rate == Decimal("13.00")
        assert red.tax_amount == Decimal("-13.00")
        assert red.amount - red.tax_amount == Decimal("-100.00")

        with pytest.raises(BizException) as invalid_red:
            purchase_invoice_service.create_invoice(
                db,
                data={
                    "supplier_id": 1,
                    "invoice_type": "RED_NORMAL",
                    "amount": "10.00",
                },
                staff=finance,
            )
        assert "红字发票金额必须为负数" in invalid_red.value.message


def test_cost_accounting_and_write_off_status(monkeypatch):
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: "CGFP20260923-1001",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        finance = db.get(Staff, 1)

        invoice = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "SPECIAL",
                "amount": "135.60",
                "stock_in_ids": [1],
            },
            staff=finance,
        )
        invoice = purchase_invoice_service.submit_invoice(
            db, invoice_id=invoice.id, staff=finance
        )
        invoice = purchase_invoice_service.approve_invoice(
            db, invoice_id=invoice.id, staff=finance
        )

        assert invoice.status == "APPROVED"
        assert invoice.cost_accounted_at is not None
        assert db.get(StockInItem, 1).amount == Decimal("24.00")
        assert db.get(StockInItem, 1).cost_price == Decimal("12.00")
        assert db.get(StockInItem, 2).amount == Decimal("96.00")
        assert db.get(StockInItem, 2).cost_price == Decimal("12.00")

        detail = purchase_invoice_service.invoice_dict(db, invoice)
        assert detail["net_amount"] == Decimal("120.00")
        assert len(detail["cost_allocation"]) == 2
        assert detail["cost_allocation"][0]["allocated_amount"] == Decimal(
            "24.00"
        )

        invoice = purchase_invoice_service.record_write_off(
            db, invoice_id=invoice.id, amount="50.00"
        )
        assert invoice.write_off_status == "PARTIAL"
        invoice = purchase_invoice_service.record_write_off(
            db, invoice_id=invoice.id, amount="85.60"
        )
        assert invoice.write_off_status == "FULL"
        assert invoice.write_off_amount == Decimal("135.60")

        with pytest.raises(BizException):
            purchase_invoice_service.record_write_off(
                db, invoice_id=invoice.id, amount="0.01"
            )


def test_unaudit_restores_stock_in_cost(monkeypatch):
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: "CGFP20260923-2001",
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        finance = db.get(Staff, 1)

        invoice = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "SPECIAL",
                "amount": "135.60",
                "stock_in_ids": [1],
            },
            staff=finance,
        )
        invoice = purchase_invoice_service.submit_invoice(
            db, invoice_id=invoice.id, staff=finance
        )
        invoice = purchase_invoice_service.approve_invoice(
            db, invoice_id=invoice.id, staff=finance
        )
        assert db.get(StockInItem, 1).cost_price == Decimal("12.00")
        assert db.get(StockInItem, 2).cost_price == Decimal("12.00")

        invoice = purchase_invoice_service.unaudit_invoice(
            db,
            invoice_id=invoice.id,
            staff=finance,
            reason="成本调整",
        )
        assert invoice.status == "PENDING"
        assert invoice.audit_status == "PENDING"
        assert invoice.cost_accounted_at is None
        assert invoice.cost_allocation is None
        assert db.get(StockInItem, 1).amount == Decimal("20.00")
        assert db.get(StockInItem, 1).cost_price == Decimal("10.00")
        assert db.get(StockInItem, 2).amount == Decimal("80.00")
        assert db.get(StockInItem, 2).cost_price == Decimal("10.00")


def test_red_invoice_adjusts_existing_cost(monkeypatch):
    serials = iter(
        [
            "CGFP20260923-2101",
            "CGFP20260923-2102",
        ]
    )
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        finance = db.get(Staff, 1)

        normal = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "SPECIAL",
                "amount": "113.00",
                "stock_in_ids": [1],
            },
            staff=finance,
        )
        normal = purchase_invoice_service.submit_invoice(
            db, invoice_id=normal.id, staff=finance
        )
        purchase_invoice_service.approve_invoice(
            db, invoice_id=normal.id, staff=finance
        )
        assert db.get(StockInItem, 1).amount == Decimal("20.00")

        red = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "RED_SPECIAL",
                "amount": "-56.50",
                "stock_in_ids": [1],
            },
            staff=finance,
        )
        red = purchase_invoice_service.submit_invoice(
            db, invoice_id=red.id, staff=finance
        )
        purchase_invoice_service.approve_invoice(
            db, invoice_id=red.id, staff=finance
        )
        assert db.get(StockInItem, 1).amount == Decimal("10.00")
        assert db.get(StockInItem, 1).cost_price == Decimal("5.00")
        assert db.get(StockInItem, 2).amount == Decimal("40.00")
        assert db.get(StockInItem, 2).cost_price == Decimal("5.00")


def test_reject_update_resubmit_and_delete(monkeypatch):
    serials = iter(
        [
            "CGFP20260923-3001",
            "CGFP20260923-3002",
        ]
    )
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        finance = db.get(Staff, 1)

        invoice = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "NORMAL",
                "amount": "100.00",
                "stock_in_ids": [1],
            },
            staff=finance,
        )
        invoice = purchase_invoice_service.submit_invoice(
            db, invoice_id=invoice.id, staff=finance
        )
        invoice = purchase_invoice_service.reject_invoice(
            db, invoice_id=invoice.id, staff=finance, comment="信息有误"
        )
        assert invoice.status == "REJECTED"

        invoice = purchase_invoice_service.update_invoice(
            db,
            invoice_id=invoice.id,
            data={"amount": "120.00"},
            staff=finance,
        )
        assert invoice.status == "PENDING"
        assert invoice.amount == Decimal("120.00")
        invoice = purchase_invoice_service.submit_invoice(
            db, invoice_id=invoice.id, staff=finance
        )
        assert purchase_invoice_service._approval_state(db, invoice.id)[
            "current_step"
        ] == 1

        draft = purchase_invoice_service.create_invoice(
            db,
            data={
                "supplier_id": 1,
                "invoice_type": "NORMAL",
                "amount": "10.00",
            },
            staff=finance,
        )
        purchase_invoice_service.delete_invoice(
            db, invoice_id=draft.id, staff=finance
        )
        assert db.get(PurchaseInvoice, draft.id) is None


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
    app.include_router(purchase_invoice_router)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        purchase_invoice_service,
        "generate_serial",
        lambda prefix, dt=None: "CGFP20260923-4001",
    )
    return app, Session


def _seed_api(Session) -> None:
    with Session() as db:
        _seed(db)
        _create_flow(db)


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_invoice_permissions_cost_detail_and_unaudit(monkeypatch):
    app, Session = _make_api(monkeypatch)
    _seed_api(Session)
    client = TestClient(app)

    denied = client.post(
        "/purchase-invoices",
        json={
            "supplier_id": 1,
            "invoice_type": "SPECIAL",
            "amount": "113.00",
            "stock_in_ids": [1],
        },
        headers=_auth(3, "采购员"),
    )
    assert denied.status_code == 403

    created = client.post(
        "/purchase-invoices",
        json={
            "supplier_id": 1,
            "invoice_type": "SPECIAL",
            "amount": "135.60",
            "stock_in_ids": [1],
        },
        headers=_auth(1, "财务"),
    )
    assert created.status_code == 200
    invoice_id = created.json()["data"]["id"]
    assert created.json()["data"]["tax_amount"] == "15.60"
    assert created.json()["data"]["net_amount"] == "120.00"

    submitted = client.post(
        f"/purchase-invoices/{invoice_id}/submit",
        headers=_auth(1, "财务"),
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"/purchase-invoices/{invoice_id}/approve",
        json={"comment": "审核通过"},
        headers=_auth(1, "财务"),
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"

    cost_detail = client.get(
        f"/purchase-invoices/{invoice_id}/cost-detail",
        headers=_auth(1, "财务"),
    )
    assert cost_detail.status_code == 200
    assert len(cost_detail.json()["data"]) == 2
    assert cost_detail.json()["data"][0]["new_cost_price"] == "12.00"

    unaudited = client.post(
        f"/purchase-invoices/{invoice_id}/unaudit",
        json={"reason": "成本复核"},
        headers=_auth(1, "财务"),
    )
    assert unaudited.status_code == 200
    assert unaudited.json()["data"]["status"] == "PENDING"
    assert unaudited.json()["data"]["cost_allocation"] == []
