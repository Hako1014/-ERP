from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.prepay import router as prepay_router
from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.finance import AccountSubject, Voucher, VoucherItem
from app.models.org import Company, Staff
from app.models.party import Supplier
from app.models.purchase import PurchaseOrder, PurchasePrepay
from app.models.rbac import Role, StaffRole
from app.services import approval_service, prepay_service


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
            Staff(id=2, company_id=1, name="分管副总", phone="13900000002"),
            Staff(id=3, company_id=1, name="财务", phone="13900000003"),
            Staff(id=4, company_id=1, name="总经理", phone="13900000004"),
        ]
    )
    db.add_all(
        [
            Supplier(
                id=1,
                code="S0001",
                name="示例供应商",
                is_active=True,
            ),
            Supplier(
                id=2,
                code="S0002",
                name="其他供应商",
                is_active=True,
            ),
        ]
    )
    db.add(
        PurchaseOrder(
            id=1,
            order_no="CGDD20260923-0001",
            supplier_id=1,
            status="APPROVED",
            audit_status="APPROVED",
            total_qty=Decimal("10.000"),
            total_amount=Decimal("1000.00"),
        )
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flow(db, approvers: list[int] | None = None) -> None:
    approval_service.create_flow(
        db,
        name="采购预付款审批",
        doc_type=prepay_service.DOC_TYPE,
        approvers=approvers or [2, 3, 4],
        actor=db.get(Staff, 1),
    )


def test_create_prepay_order_supplier_and_crud(monkeypatch):
    serials = iter(
        [
            "YFSQ20260923-0001",
            "YFSQ20260923-0002",
            "YFSQ20260923-0003",
        ]
    )
    monkeypatch.setattr(
        prepay_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        purchaser = db.get(Staff, 1)

        prepay = prepay_service.create_prepay(
            db,
            data={
                "order_id": 1,
                "prepay_amount": "300.00",
                "bank": "工商银行",
                "account_no": "62220001",
                "account_balance": "10000.00",
                "pay_note": "预付 30%",
            },
            staff=purchaser,
        )
        assert prepay.prepay_no == "YFSQ20260923-0001"
        assert prepay.supplier_id == 1
        assert prepay.order_amount == Decimal("1000.00")
        assert prepay.prepay_amount == Decimal("300.00")
        assert prepay.status == "PENDING"
        assert prepay.audit_status == "PENDING"

        with pytest.raises(BizException) as mismatch:
            prepay_service.create_prepay(
                db,
                data={
                    "order_id": 1,
                    "supplier_id": 2,
                    "prepay_amount": "100.00",
                },
                staff=purchaser,
            )
        assert "供应商不一致" in mismatch.value.message

        no_order = prepay_service.create_prepay(
            db,
            data={"supplier_id": 1, "prepay_amount": "50.00"},
            staff=purchaser,
        )
        assert no_order.order_id is None
        assert no_order.order_amount is None

        updated = prepay_service.update_prepay(
            db,
            prepay_id=no_order.id,
            data={"prepay_amount": "60.00", "pay_note": "调整金额"},
            staff=purchaser,
        )
        assert updated.prepay_amount == Decimal("60.00")
        assert updated.pay_note == "调整金额"

        terminated = prepay_service.terminate_prepay(
            db,
            prepay_id=no_order.id,
            staff=purchaser,
            reason="不再需要",
        )
        assert terminated.status == "TERMINATED"
        with pytest.raises(BizException):
            prepay_service.delete_prepay(
                db, prepay_id=no_order.id, staff=purchaser
            )

        third = prepay_service.create_prepay(
            db,
            data={"supplier_id": 2, "prepay_amount": "20.00"},
            staff=purchaser,
        )
        prepay_service.delete_prepay(
            db, prepay_id=third.id, staff=purchaser
        )
        assert db.get(PurchasePrepay, third.id) is None


def test_three_level_approval_generates_balanced_voucher(monkeypatch):
    serials = iter(["YFSQ20260923-1001", "PZ20260923-1001"])
    monkeypatch.setattr(
        prepay_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        vp = db.get(Staff, 2)
        finance = db.get(Staff, 3)
        general_manager = db.get(Staff, 4)

        prepay = prepay_service.create_prepay(
            db,
            data={
                "order_id": 1,
                "prepay_amount": "300.00",
            },
            staff=purchaser,
        )
        prepay = prepay_service.submit_prepay(
            db, prepay_id=prepay.id, staff=purchaser
        )
        state = prepay_service._approval_state(db, prepay.id)
        assert state["current_step"] == 1

        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=vp, comment="副总同意"
        )
        state = prepay_service._approval_state(db, prepay.id)
        assert state["current_step"] == 2
        assert prepay.status == "PENDING"

        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=finance, comment="财务同意"
        )
        state = prepay_service._approval_state(db, prepay.id)
        assert state["current_step"] == 3

        prepay = prepay_service.approve_prepay(
            db,
            prepay_id=prepay.id,
            staff=general_manager,
            comment="总经理同意",
        )
        assert prepay.status == "APPROVED"
        assert prepay.audit_status == "APPROVED"
        assert prepay.auditor_id == general_manager.id

        voucher = prepay_service.get_prepay_voucher(db, prepay.id)
        assert voucher is not None
        assert voucher["voucher_no"] == "PZ20260923-1001"
        assert voucher["status"] == "DRAFT"
        assert voucher["total_debit"] == Decimal("300.00")
        assert voucher["total_credit"] == Decimal("300.00")
        assert [item["subject_code"] for item in voucher["items"]] == [
            "1123",
            "1002",
        ]
        assert sum(item["debit"] for item in voucher["items"]) == Decimal(
            "300.00"
        )
        assert sum(item["credit"] for item in voucher["items"]) == Decimal(
            "300.00"
        )
        assert db.scalar(
            select(AccountSubject).where(AccountSubject.code == "1123")
        ) is not None


def test_unaudit_invalidates_voucher_and_regenerates(monkeypatch):
    serials = iter(
        [
            "YFSQ20260923-2001",
            "PZ20260923-2001",
            "PZ20260923-2002",
        ]
    )
    monkeypatch.setattr(
        prepay_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        prepay = prepay_service.create_prepay(
            db,
            data={"supplier_id": 1, "prepay_amount": "80.00"},
            staff=purchaser,
        )
        prepay = prepay_service.submit_prepay(
            db, prepay_id=prepay.id, staff=purchaser
        )
        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=db.get(Staff, 2)
        )
        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=db.get(Staff, 3)
        )
        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=db.get(Staff, 4)
        )
        first_voucher = db.scalar(
            select(Voucher).where(
                Voucher.reference == f"采购预付款申请:{prepay.prepay_no}"
            )
        )
        assert first_voucher is not None

        prepay = prepay_service.unaudit_prepay(
            db,
            prepay_id=prepay.id,
            staff=purchaser,
            reason="付款信息调整",
        )
        assert prepay.status == "PENDING"
        assert prepay.audit_status == "PENDING"
        assert first_voucher.status == "INVALID"
        assert prepay_service.get_prepay_voucher(db, prepay.id) is None
        state = prepay_service._approval_state(db, prepay.id)
        assert state["current_step"] == 3

        prepay = prepay_service.approve_prepay(
            db, prepay_id=prepay.id, staff=db.get(Staff, 4)
        )
        assert prepay.status == "APPROVED"
        new_voucher = prepay_service.get_prepay_voucher(db, prepay.id)
        assert new_voucher is not None
        assert new_voucher["voucher_no"] == "PZ20260923-2002"
        assert new_voucher["id"] != first_voucher.id


def test_reject_then_update_resubmit(monkeypatch):
    serials = iter(["YFSQ20260923-3001"])
    monkeypatch.setattr(
        prepay_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        purchaser = db.get(Staff, 1)
        prepay = prepay_service.create_prepay(
            db,
            data={"supplier_id": 1, "prepay_amount": "90.00"},
            staff=purchaser,
        )
        prepay = prepay_service.submit_prepay(
            db, prepay_id=prepay.id, staff=purchaser
        )
        prepay = prepay_service.reject_prepay(
            db,
            prepay_id=prepay.id,
            staff=db.get(Staff, 2),
            comment="金额有误",
        )
        assert prepay.status == "REJECTED"
        assert prepay.audit_status == "REJECTED"

        prepay = prepay_service.update_prepay(
            db,
            prepay_id=prepay.id,
            data={"prepay_amount": "70.00"},
            staff=purchaser,
        )
        assert prepay.status == "PENDING"
        assert prepay.audit_status == "PENDING"

        prepay = prepay_service.submit_prepay(
            db, prepay_id=prepay.id, staff=purchaser
        )
        state = prepay_service._approval_state(db, prepay.id)
        assert state["current_step"] == 1


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
    app.include_router(prepay_router)
    app.dependency_overrides[get_db] = override_get_db
    serials = iter(["YFSQ20260923-4001", "PZ20260923-4001"])
    monkeypatch.setattr(
        prepay_service,
        "generate_serial",
        lambda prefix, dt=None: next(serials),
    )
    return app, Session


def _seed_api(Session) -> None:
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="ADMIN", name="管理员"),
                Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
                Role(id=3, company_id=1, code="VP", name="分管副总"),
                Role(id=4, company_id=1, code="FINANCE", name="财务"),
            ]
        )
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=2),
                StaffRole(staff_id=2, role_id=3),
                StaffRole(staff_id=3, role_id=4),
                StaffRole(staff_id=4, role_id=1),
            ]
        )
        db.commit()
        _create_flow(db)


def _auth(staff_id: int, name: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(staff_id, name, [])}"}


def test_api_prepay_permissions_approval_and_voucher(monkeypatch):
    app, Session = _make_api(monkeypatch)
    _seed_api(Session)
    client = TestClient(app)

    created = client.post(
        "/purchase-prepays",
        json={
            "order_id": 1,
            "prepay_amount": "300.00",
            "bank": "工商银行",
        },
        headers=_auth(1, "采购员"),
    )
    assert created.status_code == 200
    prepay_id = created.json()["data"]["id"]
    assert created.json()["data"]["order_amount"] == "1000.00"

    submitted = client.post(
        f"/purchase-prepays/{prepay_id}/submit",
        headers=_auth(1, "采购员"),
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["approval"]["state"][
        "current_step"
    ] == 1

    denied = client.post(
        f"/purchase-prepays/{prepay_id}/approve",
        json={"comment": "越权"},
        headers=_auth(1, "采购员"),
    )
    assert denied.status_code == 403

    for staff_id, name in ((2, "分管副总"), (3, "财务"), (4, "总经理")):
        approved = client.post(
            f"/purchase-prepays/{prepay_id}/approve",
            json={"comment": "同意"},
            headers=_auth(staff_id, name),
        )
        assert approved.status_code == 200

    detail = client.get(
        f"/purchase-prepays/{prepay_id}",
        headers=_auth(3, "财务"),
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "APPROVED"
    assert detail.json()["data"]["voucher"]["status"] == "DRAFT"

    denied_unaudit = client.post(
        f"/purchase-prepays/{prepay_id}/unaudit",
        json={"reason": "越权弃审"},
        headers=_auth(3, "财务"),
    )
    assert denied_unaudit.status_code == 403

    unaudited = client.post(
        f"/purchase-prepays/{prepay_id}/unaudit",
        json={"reason": "复核调整"},
        headers=_auth(1, "采购员"),
    )
    assert unaudited.status_code == 200
    assert unaudited.json()["data"]["status"] == "PENDING"
    assert unaudited.json()["data"]["voucher"] is None

    voucher = client.get(
        f"/purchase-prepays/{prepay_id}/voucher",
        headers=_auth(3, "财务"),
    )
    assert voucher.status_code == 200
    assert voucher.json()["data"] is None
