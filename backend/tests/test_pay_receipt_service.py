from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.pay_receipt import router as pay_receipt_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import (
    Expense,
    Income,
    Invoice,
    Payment,
    Receipt,
    Voucher,
)
from app.models.order import Order
from app.models.org import Company, Staff
from app.models.party import Customer, Supplier
from app.models.purchase import PurchaseInvoice, PurchaseOrder, PurchasePrepay
from app.models.rbac import Role, StaffRole
from app.models.stock import StockIn
from app.services import (
    approval_service,
    pay_receipt_service,
    voucher_service,
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
            Staff(id=1, company_id=1, name="财务", phone="13800004001"),
            Staff(id=2, company_id=1, name="仓管", phone="13800004002"),
        ]
    )
    db.add_all(
        [
            Supplier(id=1, code="S0001", name="示例供应商", is_active=True),
            Supplier(id=2, code="S0002", name="其他供应商", is_active=True),
            Customer(id=1, code="C0001", name="示例客户", is_active=True),
        ]
    )
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD20260923-0001",
            supplier_id=1,
            status="APPROVED",
            audit_status="APPROVED",
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
    db.add(
        PurchaseInvoice(
            id=1,
            invoice_no="CGFP20260923-0001",
            supplier_id=1,
            invoice_type="SPECIAL",
            tax_rate=Decimal("13.00"),
            amount=Decimal("1000.00"),
            write_off_amount=Decimal("0.00"),
            write_off_status="NONE",
            status="APPROVED",
            audit_status="APPROVED",
        )
    )
    db.add(
        PurchasePrepay(
            id=1,
            prepay_no="YFSQ20260923-0001",
            supplier_id=1,
            prepay_amount=Decimal("300.00"),
            write_off_amount=Decimal("0.00"),
            status="APPROVED",
            audit_status="APPROVED",
        )
    )
    db.add(
        Income(
            id=1,
            income_no="SRD20260923-0001",
            supplier_id=1,
            income_type="REBATE",
            amount=Decimal("50.00"),
            status="APPROVED",
            audit_status="APPROVED",
        )
    )
    db.add(
        Order(
            id=1,
            order_no="DD20260923-0001",
            shop_id=1,
            status="DONE",
            audit_status="APPROVED",
            order_amount=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
        )
    )
    db.add(
        Invoice(
            id=1,
            invoice_no="FPD20260923-0001",
            invoice_title="示例客户",
            amount=Decimal("500.00"),
            received_amount=Decimal("0.00"),
            is_received=False,
            status="APPROVED",
        )
    )
    db.add(
        Expense(
            id=1,
            expense_no="FYD20260923-0001",
            customer_id=1,
            expense_type="PLATFORM_FEE",
            amount=Decimal("20.00"),
            status="APPROVED",
            audit_status="APPROVED",
        )
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flows(db) -> None:
    finance_role = db.scalar(select(Role).where(Role.code == "FINANCE"))
    if finance_role is None:
        finance_role = Role(id=1, company_id=1, code="FINANCE", name="财务")
        db.add(finance_role)
        db.flush()
    binding = db.scalar(
        select(StaffRole).where(
            StaffRole.staff_id == 1,
            StaffRole.role_id == finance_role.id,
        )
    )
    if binding is None:
        db.add(StaffRole(staff_id=1, role_id=finance_role.id))
        db.flush()
    approval_service.create_flow(
        db,
        name="付款单审批",
        doc_type=pay_receipt_service.PAYMENT_DOC_TYPE,
        approvers=[1],
        actor=db.get(Staff, 1),
    )
    approval_service.create_flow(
        db,
        name="收款单审批",
        doc_type=pay_receipt_service.RECEIPT_DOC_TYPE,
        approvers=[1],
        actor=db.get(Staff, 1),
    )


def _serial_patch(monkeypatch) -> None:
    counters: dict[str, int] = {}

    def fake_generate(prefix, dt=None):
        code = prefix.value if hasattr(prefix, "value") else str(prefix)
        counters[code] = counters.get(code, 0) + 1
        return f"{code}20260923-{counters[code]:04d}"

    monkeypatch.setattr(
        pay_receipt_service, "generate_serial", fake_generate
    )


def test_payment_crud_validation_and_invoice_mismatch(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        payment = pay_receipt_service.create_payment(
            db,
            data={
                "supplier_id": 1,
                "stock_in_id": 1,
                "invoice_id": 1,
                "income_id": 1,
                "payment_type": "PURCHASE",
                "pay_method": "BANK",
                "amount": "800.00",
            },
            staff=staff,
        )
        detail = pay_receipt_service.payment_dict(db, payment)
        assert detail["payment_no"] == "FKD20260923-0001"
        assert detail["has_warning"] is True
        assert detail["warnings"][0]["code"] == (
            "PURCHASE_INVOICE_AMOUNT_MISMATCH"
        )

        updated = pay_receipt_service.update_payment(
            db,
            payment_id=payment.id,
            data={"amount": "1000.00", "pay_method": "CASH"},
            staff=staff,
        )
        assert updated.amount == Decimal("1000.00")
        assert updated.pay_method == "CASH"
        assert pay_receipt_service.payment_dict(
            db, updated
        )["has_warning"] is False

        with pytest.raises(BizException) as mismatch:
            pay_receipt_service.create_payment(
                db,
                data={
                    "supplier_id": 2,
                    "invoice_id": 1,
                    "amount": "10.00",
                },
                staff=staff,
            )
        assert "不一致" in mismatch.value.message

        pay_receipt_service.cancel_payment(
            db, payment_id=payment.id, staff=staff, reason="录入错误"
        )
        assert payment.status == "CANCELLED"


def test_payment_approval_with_prepay_generates_offset_voucher(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        finance = db.get(Staff, 1)
        payment = pay_receipt_service.create_payment(
            db,
            data={
                "supplier_id": 1,
                "invoice_id": 1,
                "prepay_id": 1,
                "amount": "300.00",
            },
            staff=finance,
        )
        pay_receipt_service.submit_payment(
            db, payment_id=payment.id, staff=finance
        )
        payment = pay_receipt_service.approve_payment(
            db, payment_id=payment.id, staff=finance
        )
        assert payment.status == "APPROVED"
        voucher = pay_receipt_service.get_payment_voucher(db, payment.id)
        assert voucher is not None
        assert voucher["reference"] == "付款单:FKD20260923-0001"
        assert [item["subject_code"] for item in voucher["items"]] == [
            "2202",
            "1123",
        ]

        payment = pay_receipt_service.unaudit_payment(
            db,
            payment_id=payment.id,
            staff=finance,
            reason="付款信息调整",
        )
        assert payment.status == "PENDING"
        assert pay_receipt_service.get_payment_voucher(db, payment.id) is None
        assert db.scalar(
            select(Voucher).where(Voucher.id == voucher["id"])
        ).status == "INVALID"


def test_payment_approval_without_prepay_uses_bank(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        finance = db.get(Staff, 1)
        payment = pay_receipt_service.create_payment(
            db,
            data={"supplier_id": 1, "invoice_id": 1, "amount": "1000.00"},
            staff=finance,
        )
        pay_receipt_service.submit_payment(
            db, payment_id=payment.id, staff=finance
        )
        payment = pay_receipt_service.approve_payment(
            db, payment_id=payment.id, staff=finance
        )
        voucher = pay_receipt_service.get_payment_voucher(db, payment.id)
        assert voucher is not None
        assert [item["subject_code"] for item in voucher["items"]] == [
            "2202",
            "1002",
        ]


def test_receipt_crud_and_invoice_mismatch(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        receipt = pay_receipt_service.create_receipt(
            db,
            data={
                "customer_id": 1,
                "order_id": 1,
                "invoice_id": 1,
                "expense_id": 1,
                "sale_type": "ONLINE",
                "settle_type": "BANK",
                "amount": "400.00",
            },
            staff=staff,
        )
        detail = pay_receipt_service.receipt_dict(db, receipt)
        assert detail["receipt_no"] == "SKD20260923-0001"
        assert detail["has_warning"] is True
        assert detail["warnings"][0]["code"] == (
            "SALES_INVOICE_AMOUNT_MISMATCH"
        )

        updated = pay_receipt_service.update_receipt(
            db,
            receipt_id=receipt.id,
            data={"amount": "500.00", "settle_type": "CASH"},
            staff=staff,
        )
        assert updated.amount == Decimal("500.00")
        assert pay_receipt_service.receipt_dict(
            db, updated
        )["has_warning"] is False

        pay_receipt_service.cancel_receipt(
            db, receipt_id=receipt.id, staff=staff, reason="录入错误"
        )
        assert receipt.status == "CANCELLED"


def test_receipt_approval_generates_bank_receivable_voucher(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        finance = db.get(Staff, 1)
        receipt = pay_receipt_service.create_receipt(
            db,
            data={
                "customer_id": 1,
                "order_id": 1,
                "invoice_id": 1,
                "amount": "500.00",
            },
            staff=finance,
        )
        pay_receipt_service.submit_receipt(
            db, receipt_id=receipt.id, staff=finance
        )
        receipt = pay_receipt_service.approve_receipt(
            db, receipt_id=receipt.id, staff=finance
        )
        assert receipt.status == "APPROVED"
        voucher = pay_receipt_service.get_receipt_voucher(db, receipt.id)
        assert voucher is not None
        assert [item["subject_code"] for item in voucher["items"]] == [
            "1002",
            "1122",
        ]
        assert voucher["total_debit"] == Decimal("500.00")
        assert voucher["total_credit"] == Decimal("500.00")

        receipt = pay_receipt_service.unaudit_receipt(
            db,
            receipt_id=receipt.id,
            staff=finance,
            reason="复核调整",
        )
        assert receipt.status == "PENDING"
        assert pay_receipt_service.get_receipt_voucher(db, receipt.id) is None


def _make_api(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory(pool=True)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(pay_receipt_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed_roles_and_flows(Session) -> None:
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="FINANCE", name="财务"),
                Role(id=2, company_id=1, code="WAREHOUSE", name="仓库"),
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
        _create_flows(db)


def _auth(staff_id: int) -> dict:
    return {
        "Authorization": (
            f"Bearer {create_access_token(staff_id, f'staff-{staff_id}', [])}"
        )
    }


def test_api_payment_receipt_permissions_and_workflow(monkeypatch):
    app, Session = _make_api(monkeypatch)
    _seed_roles_and_flows(Session)
    client = TestClient(app)

    denied = client.get("/payments", headers=_auth(2))
    assert denied.status_code == 403

    created = client.post(
        "/payments",
        json={
            "supplier_id": 1,
            "invoice_id": 1,
            "amount": "1000.00",
        },
        headers=_auth(1),
    )
    assert created.status_code == 200
    payment_id = created.json()["data"]["id"]

    submitted = client.post(
        f"/payments/{payment_id}/submit",
        headers=_auth(1),
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"/payments/{payment_id}/approve",
        json={"comment": "同意"},
        headers=_auth(1),
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"
    assert approved.json()["data"]["voucher"]["status"] == "DRAFT"

    receipt_created = client.post(
        "/receipts",
        json={
            "customer_id": 1,
            "order_id": 1,
            "invoice_id": 1,
            "amount": "500.00",
        },
        headers=_auth(1),
    )
    assert receipt_created.status_code == 200
    assert receipt_created.json()["data"]["customer_name"] == "示例客户"
