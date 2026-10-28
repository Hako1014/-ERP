from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.approval_manage import router as approval_manage_router
from app.core.data_scope import clear_scope_policy, initialize_scope_policy
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.party import Supplier
from app.models.purchase import PurchasePlan
from app.models.rbac import Role, StaffRole
from app.services import (
    approval_manage_service,
    approval_service,
    purchase_plan_service,
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
            Staff(id=1, company_id=1, name="采购员", phone="13800001001"),
            Staff(id=2, company_id=1, name="采购主管", phone="13800001002"),
            Staff(id=3, company_id=1, name="财务", phone="13800001003"),
            Staff(id=4, company_id=1, name="非审批人", phone="13800001004"),
        ]
    )
    db.add(Supplier(
        id=1, company_id=1, code="S0001", name="示例供应商", is_active=True,
    ))
    db.add_all(
        [
            PurchasePlan(
                id=1,
                company_id=1,
                plan_no="CGJH-001",
                supplier_id=1,
                status="IN_PROGRESS",
                audit_status="PENDING",
                total_qty=Decimal("1.000"),
                total_amount=Decimal("10.00"),
                created_by=1,
            ),
            PurchasePlan(
                id=2,
                company_id=1,
                plan_no="CGJH-002",
                supplier_id=1,
                status="IN_PROGRESS",
                audit_status="PENDING",
                total_qty=Decimal("1.000"),
                total_amount=Decimal("10.00"),
                created_by=1,
            ),
        ]
    )
    db.commit()
    initialize_scope_policy(db, db.get(Staff, 1))


def _create_flow(db) -> None:
    approval_service.create_flow(
        db,
        name="采购计划审批",
        doc_type="purchase_plan",
        approvers=[
            {"step": 1, "name": "主管", "approver_ids": [2]},
            {"step": 2, "name": "财务", "approver_ids": [3]},
        ],
        actor=db.get(Staff, 1),
    )


def _staff(db, staff_id: int) -> Staff:
    staff = db.get(Staff, staff_id)
    clear_scope_policy(db)
    initialize_scope_policy(db, staff)
    return staff


def _submit(db, plan_id: int = 1) -> None:
    approval_service.submit(
        db,
        doc_type="purchase_plan",
        doc_id=plan_id,
        doc_no=f"CGJH-00{plan_id}",
        staff=_staff(db, 1),
    )


def test_manage_flow_config_is_enriched():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        flow = approval_manage_service.create_manage_flow(
            db,
            name="采购计划审批",
            doc_type="purchase_plan",
            approvers=[
                {"step": 1, "name": "主管", "approver_ids": [2]},
                {"step": 2, "name": "财务", "approver_ids": [3]},
            ],
            staff=_staff(db, 1),
        )

        assert flow["doc_type_label"] == "采购计划"
        assert flow["created_by_name"] == "采购员"
        assert flow["steps"][0]["approvers"][0]["name"] == "采购主管"
        assert flow["flow_display"] == "主管：采购主管 → 财务：财务"
        assert approval_manage_service.list_manage_flows(db)[0]["id"] == flow["id"]


def test_pending_list_is_filtered_by_current_step_approver():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        _submit(db)

        approver_rows = approval_manage_service.list_my_approvals(
            db, staff=_staff(db, 2)
        )
        later_step_rows = approval_manage_service.list_my_approvals(
            db, staff=_staff(db, 3)
        )
        outsider_rows = approval_manage_service.list_my_approvals(
            db, staff=_staff(db, 4)
        )

        assert approver_rows["total"] == 1
        assert approver_rows["items"][0]["doc_no"] == "CGJH-001"
        assert approver_rows["items"][0]["current_step"] == 1
        assert later_step_rows["total"] == 0
        assert outsider_rows["total"] == 0

        with pytest.raises(BizException) as exc:
            approval_manage_service.get_my_approval_detail(
                db,
                doc_type="purchase_plan",
                doc_id=1,
                staff=_staff(db, 4),
            )
        assert "无权查看" in exc.value.message


def test_approve_reject_and_unaudit_dispatch_to_business_service():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _create_flow(db)
        _submit(db)

        result = approval_manage_service.approve_my_approval(
            db,
            doc_type="purchase_plan",
            doc_id=1,
            staff=_staff(db, 2),
            comment="同意",
        )
        assert result["approval"]["status"] == "PENDING"
        assert result["approval"]["current_step"] == 2

        result = approval_manage_service.approve_my_approval(
            db,
            doc_type="purchase_plan",
            doc_id=1,
            staff=_staff(db, 3),
        )
        assert result["approval"]["status"] == "APPROVED"
        plan = db.get(PurchasePlan, 1)
        assert plan.audit_status == "APPROVED"
        assert plan.auditor_id == 3

        with pytest.raises(BizException) as exc:
            approval_manage_service.unaudit_my_approval(
                db,
                doc_type="purchase_plan",
                doc_id=1,
                staff=_staff(db, 1),
                reason="   ",
            )
        assert "原因不能为空" in exc.value.message

        result = approval_manage_service.unaudit_my_approval(
            db,
            doc_type="purchase_plan",
            doc_id=1,
            staff=_staff(db, 1),
            reason="数量需要调整",
            step=1,
        )
        assert result["approval"]["status"] == "PENDING"
        assert result["approval"]["current_step"] == 1
        assert result["unaudit_reasons"][0]["reason"] == "数量需要调整"
        db.refresh(plan)
        assert plan.audit_status == "PENDING"

        _submit(db, plan_id=2)
        result = approval_manage_service.reject_my_approval(
            db,
            doc_type="purchase_plan",
            doc_id=2,
            staff=_staff(db, 2),
            comment="价格不合适",
        )
        assert result["approval"]["status"] == "REJECTED"
        db.refresh(db.get(PurchasePlan, 2))
        assert db.get(PurchasePlan, 2).audit_status == "REJECTED"


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
    app.include_router(approval_manage_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed_roles(Session) -> None:
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="PURCHASER", name="采购员"),
                Role(id=2, company_id=1, code="VP", name="副总"),
                Role(id=3, company_id=1, code="FINANCE", name="财务"),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
                StaffRole(staff_id=3, role_id=3),
                StaffRole(staff_id=4, role_id=1),
            ]
        )
        db.commit()
        _create_flow(db)
        _submit(db)


def _auth(staff_id: int) -> dict:
    token = create_access_token(staff_id, f"staff-{staff_id}", [])
    return {"Authorization": f"Bearer {token}"}


def test_api_permissions_and_my_approval_flow():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    denied = client.post(
        "/approval-manage/my-approvals/purchase_plan/1/approve",
        json={"comment": "越权"},
        headers=_auth(4),
    )
    assert denied.status_code == 403

    pending = client.get(
        "/approval-manage/my-approvals",
        headers=_auth(2),
    )
    assert pending.status_code == 200
    assert pending.json()["data"]["total"] == 1

    approved = client.post(
        "/approval-manage/my-approvals/purchase_plan/1/approve",
        json={"comment": "同意"},
        headers=_auth(2),
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["approval"]["current_step"] == 2
