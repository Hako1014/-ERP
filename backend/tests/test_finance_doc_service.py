from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.finance_doc import router as finance_doc_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import Expense, Income, Invoice, Payment, Receipt
from app.models.org import Company, Staff
from app.models.party import Customer, Supplier
from app.models.platform import Platform, Shop
from app.models.rbac import Role, StaffRole
from app.services import (
    approval_service,
    finance_doc_service,
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
            Staff(id=1, company_id=1, name="财务", phone="13800004501"),
            Staff(id=2, company_id=1, name="仓管", phone="13800004502"),
        ]
    )
    db.add_all(
        [
            Supplier(id=1, code="S0001", name="示例供应商", is_active=True),
            Customer(id=1, code="C0001", name="示例客户", is_active=True),
        ]
    )
    db.add(Platform(id=1, name="示例平台", code="TEST"))
    db.add(
        Shop(
            id=1,
            platform_id=1,
            name="示例旗舰店",
            is_locked=False,
        )
    )
    db.flush()
    role = Role(id=1, company_id=1, code="FINANCE", name="财务")
    db.add(role)
    db.flush()
    db.add(StaffRole(staff_id=1, role_id=role.id))
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flows(db) -> None:
    for doc_type, name in (
        (finance_doc_service.EXPENSE_DOC_TYPE, "费用单审批"),
        (finance_doc_service.INCOME_DOC_TYPE, "收入单审批"),
        (pay_receipt_service.PAYMENT_DOC_TYPE, "付款单审批"),
        (pay_receipt_service.RECEIPT_DOC_TYPE, "收款单审批"),
    ):
        approval_service.create_flow(
            db,
            name=name,
            doc_type=doc_type,
            approvers=[1],
            actor=db.get(Staff, 1),
        )


def _serial_patch(monkeypatch) -> None:
    counters: dict[str, int] = {}

    def fake_generate(prefix, dt=None):
        code = prefix.value if hasattr(prefix, "value") else str(prefix)
        counters[code] = counters.get(code, 0) + 1
        return f"{code}20260923-{counters[code]:04d}"

    monkeypatch.setattr(finance_doc_service, "generate_serial", fake_generate)
    monkeypatch.setattr(pay_receipt_service, "generate_serial", fake_generate)


def _settlement_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["平台结算单号", "结算日期", "店铺ID", "结算金额", "备注"])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_expense_offsets_receipt_and_generates_vouchers(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        finance = db.get(Staff, 1)

        expense = finance_doc_service.create_expense(
            db,
            data={
                "customer_id": 1,
                "expense_type": "PLATFORM_FEE",
                "amount": "20.00",
                "description": "平台手续费",
            },
            staff=finance,
        )
        receipt = pay_receipt_service.create_receipt(
            db,
            data={
                "customer_id": 1,
                "expense_id": expense.id,
                "amount": "500.00",
            },
            staff=finance,
        )
        assert pay_receipt_service.receipt_dict(
            db, receipt
        )["expense_no"] == expense.expense_no

        finance_doc_service.submit_expense(
            db, expense_id=expense.id, staff=finance
        )
        expense = finance_doc_service.approve_expense(
            db, expense_id=expense.id, staff=finance
        )
        assert expense.status == "APPROVED"
        assert expense.voucher_no is not None
        expense_voucher = finance_doc_service.get_expense_voucher(
            db, expense.id
        )
        assert [
            item["subject_code"] for item in expense_voucher["items"]
        ] == ["6601", "1122"]

        pay_receipt_service.submit_receipt(
            db, receipt_id=receipt.id, staff=finance
        )
        receipt = pay_receipt_service.approve_receipt(
            db, receipt_id=receipt.id, staff=finance
        )
        receipt_voucher = pay_receipt_service.get_receipt_voucher(
            db, receipt.id
        )
        assert receipt_voucher["total_debit"] == Decimal("480.00")
        assert receipt_voucher["total_credit"] == Decimal("480.00")
        assert [
            item["subject_code"] for item in receipt_voucher["items"]
        ] == ["1002", "1122"]

        with pytest.raises(BizException) as blocked:
            finance_doc_service.unaudit_expense(
                db,
                expense_id=expense.id,
                staff=finance,
                reason="调整费用",
            )
        assert "收款单已审核" in blocked.value.message


def test_income_payment_write_off_capacity(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flows(db)
        finance = db.get(Staff, 1)

        income = finance_doc_service.create_income(
            db,
            data={
                "supplier_id": 1,
                "income_type": "REBATE",
                "amount": "100.00",
            },
            staff=finance,
        )
        finance_doc_service.submit_income(
            db, income_id=income.id, staff=finance
        )
        income = finance_doc_service.approve_income(
            db, income_id=income.id, staff=finance
        )
        assert income.voucher_no is not None
        income_voucher = finance_doc_service.get_income_voucher(
            db, income.id
        )
        assert [
            item["subject_code"] for item in income_voucher["items"]
        ] == ["2202", "1405"]

        first = pay_receipt_service.create_payment(
            db,
            data={
                "supplier_id": 1,
                "income_id": income.id,
                "amount": "60.00",
            },
            staff=finance,
        )
        pay_receipt_service.submit_payment(
            db, payment_id=first.id, staff=finance
        )
        pay_receipt_service.approve_payment(
            db, payment_id=first.id, staff=finance
        )
        detail = finance_doc_service.income_dict(db, income)
        assert detail["write_off_amount"] == Decimal("60.00")
        assert detail["remaining_amount"] == Decimal("40.00")
        assert detail["write_off_status"] == "PARTIAL"

        second = pay_receipt_service.create_payment(
            db,
            data={
                "supplier_id": 1,
                "income_id": income.id,
                "amount": "50.00",
            },
            staff=finance,
        )
        pay_receipt_service.submit_payment(
            db, payment_id=second.id, staff=finance
        )
        with pytest.raises(BizException) as over:
            pay_receipt_service.approve_payment(
                db, payment_id=second.id, staff=finance
            )
        assert "不能超过收入单金额" in over.value.message

        with pytest.raises(BizException) as blocked:
            finance_doc_service.unaudit_income(
                db,
                income_id=income.id,
                staff=finance,
                reason="调整返利",
            )
        assert "已有付款核销" in blocked.value.message


def test_settlement_import_check_confirm(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        finance = db.get(Staff, 1)
        file_bytes = _settlement_workbook(
            [["PLAT-001", date(2026, 9, 22), 1, "500.00", "9月结算"]]
        )
        imported = finance_doc_service.import_settlements(
            db,
            file_bytes=file_bytes,
            file_name="settlement.xlsx",
            staff=finance,
        )
        assert imported["created_count"] == 1
        assert imported["error_count"] == 0
        settlement = db.scalar(select(finance_doc_service.Settlement))
        assert settlement.status == "IMPORTED"

        duplicated = finance_doc_service.import_settlements(
            db,
            file_bytes=file_bytes,
            file_name="settlement.xlsx",
            staff=finance,
        )
        assert duplicated["created_count"] == 0
        assert duplicated["error_count"] == 1
        assert "已存在" in duplicated["errors"][0]["message"]

        settlement = finance_doc_service.check_settlement(
            db,
            settlement_id=settlement.id,
            staff=finance,
            remark="核对无误",
        )
        assert settlement.status == "CHECKED"
        settlement = finance_doc_service.confirm_settlement(
            db,
            settlement_id=settlement.id,
            staff=finance,
        )
        assert settlement.status == "CONFIRMED"
        receivable = db.get(Invoice, settlement.receivable_id)
        assert receivable is not None
        assert receivable.amount == Decimal("500.00")
        assert receivable.invoice_type == "SETTLEMENT_RECEIVABLE"
        voucher = finance_doc_service.get_settlement_voucher(
            db, settlement.id
        )
        assert [
            item["subject_code"] for item in voucher["items"]
        ] == ["1122", "6001"]

        confirmed_again = finance_doc_service.confirm_settlement(
            db,
            settlement_id=settlement.id,
            staff=finance,
        )
        assert confirmed_again.receivable_id == receivable.id
        assert db.scalar(select(Payment.id)) is None
        assert db.scalar(select(Receipt.id)) is None


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
    app.include_router(finance_doc_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int) -> dict:
    return {
        "Authorization": (
            f"Bearer {create_access_token(staff_id, f'staff-{staff_id}', [])}"
        )
    }


def test_api_expense_permissions_and_settlement_template(monkeypatch):
    app, Session = _make_api(monkeypatch)
    with Session() as db:
        _seed(db)

    warehouse_role = Role(id=2, company_id=1, code="WAREHOUSE", name="仓库")
    with Session() as db:
        db.add(warehouse_role)
        db.flush()
        db.add(StaffRole(staff_id=2, role_id=warehouse_role.id))
        db.commit()

    client = TestClient(app)
    denied = client.get("/expenses", headers=_auth(2))
    assert denied.status_code == 403

    created = client.post(
        "/expenses",
        json={
            "customer_id": 1,
            "expense_type": "PLATFORM_FEE",
            "amount": "20.00",
        },
        headers=_auth(1),
    )
    assert created.status_code == 200
    assert created.json()["data"]["expense_no"].startswith("FYD")

    template = client.get("/settlements/template", headers=_auth(1))
    assert template.status_code == 200
    assert template.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )

    income_created = client.post(
        "/incomes",
        json={
            "supplier_id": 1,
            "income_type": "REBATE",
            "amount": "100.00",
        },
        headers=_auth(1),
    )
    assert income_created.status_code == 200
    assert income_created.json()["data"]["income_no"].startswith("SRD")
    assert db.scalar(select(Expense.id)) is not None


def test_missing_expense_customer_is_rejected(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        finance = db.get(Staff, 1)
        with pytest.raises(BizException):
            finance_doc_service.create_expense(
                db,
                data={"customer_id": 999, "amount": "10.00"},
                staff=finance,
            )


def test_expense_money_edit_cancel_and_approved_receipt_guard(monkeypatch):
    _serial_patch(monkeypatch)
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        finance = db.get(Staff, 1)
        with pytest.raises(BizException, match="合法金额"):
            finance_doc_service.create_expense(
                db, data={"customer_id": 1, "amount": 0.1}, staff=finance,
            )

        editable = finance_doc_service.create_expense(
            db, data={"customer_id": 1, "amount": "20.00"}, staff=finance,
        )
        updated = finance_doc_service.update_expense(
            db, expense_id=editable.id, data={"amount": "20.55", "description": "调整"}, staff=finance,
        )
        assert updated.amount == Decimal("20.55")
        assert finance_doc_service.list_expenses(db, customer_id=1, keyword=editable.expense_no)["total"] == 1
        finance_doc_service.cancel_expense(db, expense_id=editable.id, staff=finance, reason="重复费用")
        assert editable.status == "CANCELLED"
        with pytest.raises(BizException):
            finance_doc_service.update_expense(
                db, expense_id=editable.id, data={"amount": "21.00"}, staff=finance,
            )

        linked = finance_doc_service.create_expense(
            db, data={"customer_id": 1, "amount": "15.00"}, staff=finance,
        )
        receipt = pay_receipt_service.create_receipt(
            db, data={"customer_id": 1, "expense_id": linked.id, "amount": "15.00"}, staff=finance,
        )
        receipt.status = "APPROVED"
        db.commit()
        with pytest.raises(BizException, match="收款单已审核"):
            finance_doc_service.cancel_expense(db, expense_id=linked.id, staff=finance)
        assert linked.status != "CANCELLED"
