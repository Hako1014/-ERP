from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.invoice import router as invoice_router
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import Invoice
from app.models.org import Company, Staff
from app.models.party import Account, Supplier
from app.models.rbac import Role, StaffRole
from app.services import invoice_service


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
            Staff(id=1, company_id=1, name="财务", phone="13800004601"),
            Staff(id=2, company_id=1, name="仓管", phone="13800004602"),
        ]
    )
    db.add(Supplier(id=1, code="S0001", name="示例供应商", is_active=True))
    db.add(
        Account(
            id=1,
            supplier_id=1,
            bank="示例银行",
            account_no="622200000001",
            kingdee_account_code="KINGDEE-S0001-01",
            is_default=True,
        )
    )
    finance_role = Role(id=1, company_id=1, code="FINANCE", name="财务")
    warehouse_role = Role(id=2, company_id=1, code="WAREHOUSE", name="仓库")
    db.add_all([finance_role, warehouse_role])
    db.flush()
    db.add_all(
        [
            StaffRole(staff_id=1, role_id=finance_role.id),
            StaffRole(staff_id=2, role_id=warehouse_role.id),
        ]
    )
    db.commit()


def test_invoice_lifecycle_and_document_type_isolation():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        invoice = invoice_service.create_invoice(
            db,
            data={
                "invoice_title": "示例客户",
                "tax_no": "91310000TEST",
                "invoice_type": "SPECIAL",
                "amount": "100.00",
                "company": "示例公司",
            },
            staff=staff,
        )
        assert invoice.invoice_no.startswith("FPD")
        assert invoice.status == "PENDING"
        assert invoice.applicant == "财务"

        invoice = invoice_service.issue_invoice(
            db, invoice_id=invoice.id, staff=staff
        )
        assert invoice.status == "ISSUED"

        invoice = invoice_service.receive_invoice(
            db,
            invoice_id=invoice.id,
            received_amount="40.00",
            staff=staff,
        )
        assert invoice.status == "ISSUED"
        assert invoice.is_received is False
        assert invoice.received_amount == Decimal("40.00")

        invoice = invoice_service.receive_invoice(
            db,
            invoice_id=invoice.id,
            received_amount="100.00",
            staff=staff,
        )
        assert invoice.status == "RECEIVED"
        assert invoice.is_received is True

        with pytest.raises(BizException) as blocked:
            invoice_service.cancel_invoice(
                db, invoice_id=invoice.id, staff=staff
            )
        assert "不能作废" in blocked.value.message

        db.add(
            Invoice(
                id=100,
                invoice_no="YSK20260924-0001",
                document_type="RECEIVABLE",
                invoice_title="平台应收",
                invoice_type="SETTLEMENT_RECEIVABLE",
                amount=Decimal("500.00"),
                received_amount=Decimal("0.00"),
                is_received=False,
                status="CONFIRMED",
            )
        )
        db.commit()
        listed = invoice_service.list_invoices(db)
        assert listed["total"] == 1
        assert listed["items"][0]["invoice_no"] == invoice.invoice_no

        other = invoice_service.get_invoice_or_404(db, 100)
        with pytest.raises(BizException) as not_invoice:
            invoice_service.issue_invoice(
                db, invoice_id=other.id, staff=staff
            )
        assert "平台应收记录" in not_invoice.value.message


def test_supplier_account_balance_mock_and_failure():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        listed = invoice_service.list_supplier_account_balances(db)
        assert listed["total"] == 1
        assert listed["items"][0]["balance"] == Decimal("0.00")
        assert listed["items"][0]["kingdee_sync_status"] == "NOT_SYNCED"

        def successful_sync(payload):
            assert payload["kingdee_account_code"] == "KINGDEE-S0001-01"
            return {
                "success": True,
                "account_code": payload["kingdee_account_code"],
                "balance": "321.45",
                "message": "同步成功",
            }

        invoice_service.register_kingdee_balance_syncer(successful_sync)
        try:
            result = invoice_service.sync_supplier_account_balance(
                db, account_id=1, staff=staff
            )
        finally:
            invoice_service.register_kingdee_balance_syncer(None)
        assert result["balance"] == Decimal("321.45")
        assert result["kingdee_sync_status"] == "SYNCED"
        assert result["kingdee_sync_message"] == "同步成功"
        assert result["balance_synced_at"] is not None

        def failed_sync(payload):
            del payload
            return {"success": False, "message": "金蝶账户不存在"}

        invoice_service.register_kingdee_balance_syncer(failed_sync)
        try:
            with pytest.raises(BizException) as failed:
                invoice_service.sync_supplier_account_balance(
                    db, account_id=1, staff=staff
                )
        finally:
            invoice_service.register_kingdee_balance_syncer(None)
        assert "金蝶账户不存在" in failed.value.message
        db.expire_all()
        account = db.get(Account, 1)
        assert account.kingdee_sync_status == "FAILED"
        assert account.kingdee_sync_message == "金蝶账户不存在"

        mock_result = invoice_service.sync_supplier_account_balance(
            db, account_id=1, staff=staff
        )
        assert mock_result["kingdee_sync_status"] == "SYNCED"
        assert mock_result["balance"] == Decimal("321.45")
        assert "mock" in mock_result["kingdee_sync_message"]


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
    app.include_router(invoice_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _auth(staff_id: int) -> dict:
    return {
        "Authorization": (
            f"Bearer {create_access_token(staff_id, f'staff-{staff_id}', [])}"
        )
    }


def test_api_invoice_and_supplier_balance_permissions():
    app, Session = _make_api()
    with Session() as db:
        _seed(db)
    client = TestClient(app)

    denied = client.get("/invoices", headers=_auth(2))
    assert denied.status_code == 403

    created = client.post(
        "/invoices",
        json={
            "invoice_title": "示例客户",
            "tax_no": "91310000TEST",
            "invoice_type": "NORMAL",
            "amount": "88.00",
            "company": "示例公司",
        },
        headers=_auth(1),
    )
    assert created.status_code == 200
    invoice_id = created.json()["data"]["id"]

    issued = client.post(
        f"/invoices/{invoice_id}/issue", headers=_auth(1)
    )
    assert issued.status_code == 200
    assert issued.json()["data"]["status"] == "ISSUED"

    received = client.post(
        f"/invoices/{invoice_id}/receive",
        json={"received_amount": "88.00"},
        headers=_auth(1),
    )
    assert received.status_code == 200
    assert received.json()["data"]["is_received"] is True

    balances = client.get(
        "/supplier-account-balances", headers=_auth(1)
    )
    assert balances.status_code == 200
    assert balances.json()["data"]["total"] == 1

    synced = client.post(
        "/supplier-account-balances/1/sync", headers=_auth(1)
    )
    assert synced.status_code == 200
    assert synced.json()["data"]["kingdee_sync_status"] == "SYNCED"


def test_invoice_amount_validation():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        invoice = invoice_service.create_invoice(
            db,
            data={
                "invoice_title": "示例客户",
                "invoice_type": "NORMAL",
                "amount": "10.00",
            },
            staff=staff,
        )
        with pytest.raises(BizException) as over:
            invoice_service.receive_invoice(
                db,
                invoice_id=invoice.id,
                received_amount="11.00",
                staff=staff,
            )
        assert "不能超过开票金额" in over.value.message
        assert db.scalar(select(Invoice.id)) == invoice.id
