import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.approval import router as approval_router
from app.core.handlers import register_exception_handlers
from app.core.errors import BizException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import approval_service
from app.services.approval_service import (
    ApprovalAction,
    ApprovalStatus,
    DocApprovalState,
)


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db):
    db.add(Company(id=1, full_name="示例公司"))
    db.add_all(
        [
            Staff(id=1, company_id=1, name="采购员", phone="13800000001"),
            Staff(id=2, company_id=1, name="主管", phone="13800000002"),
            Staff(id=3, company_id=1, name="财务", phone="13800000003"),
            Staff(id=4, company_id=1, name="总经理", phone="13800000004"),
        ]
    )
    db.commit()


def _staff(db, staff_id: int) -> Staff:
    return db.get(Staff, staff_id)


def _make_flow(db, approvers=None):
    return approval_service.create_flow(
        db,
        name="采购付款审批",
        doc_type="purchase_prepay",
        approvers=approvers
        or [
            {"step": 1, "name": "主管", "approver_ids": [2]},
            {"step": 2, "name": "财务", "approver_ids": [3]},
            {"step": 3, "name": "总经理", "approver_ids": [4]},
        ],
        actor=_staff(db, 1),
    )


def test_normalize_steps_short_form():
    steps = approval_service.normalize_steps([2, 3])
    assert [s.step for s in steps] == [1, 2]
    assert steps[0].approver_ids == (2,)
    assert steps[1].approver_ids == (3,)


def test_normalize_steps_rejects_gap():
    with pytest.raises(BizException) as exc:
        approval_service.normalize_steps(
            [{"step": 1, "approver_ids": [2]}, {"step": 3, "approver_ids": [3]}]
        )
    assert "连续编号" in exc.value.message


def test_normalize_steps_requires_approver():
    with pytest.raises(BizException) as exc:
        approval_service.normalize_steps([{"step": 1, "approver_ids": []}])
    assert "必须选择审批人" in exc.value.message


def test_create_flow_writes_audit():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        flow = _make_flow(db)
        assert flow.id == 1
        assert approval_service.steps_of(flow)[0].approver_ids == (2,)

        logs = db.query(AuditLog).filter(AuditLog.target_type == "approval_flow").all()
        assert len(logs) == 1
        assert logs[0].action == "CREATE"
        assert logs[0].actor_id == 1


def test_create_flow_rejects_unknown_approver():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        with pytest.raises(BizException) as exc:
            approval_service.create_flow(
                db,
                name="异常流程",
                doc_type="purchase_order",
                approvers=[99],
                actor=_staff(db, 1),
            )
        assert "审批人不存在" in exc.value.message


def test_multi_step_flow_moves_in_order():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)

        state = approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=100, staff=_staff(db, 1), doc_no="FK20260923-0001"
        )
        assert state["status"] == DocApprovalState.PENDING
        assert state["current_step"] == 1

        state = approval_service.approve(
            db, doc_type="purchase_prepay", doc_id=100, staff=_staff(db, 2)
        )
        assert state["status"] == DocApprovalState.PENDING
        assert state["current_step"] == 2
        assert state["approved_steps"] == [1]

        state = approval_service.approve(
            db, doc_type="purchase_prepay", doc_id=100, staff=_staff(db, 3)
        )
        assert state["current_step"] == 3

        state = approval_service.approve(
            db, doc_type="purchase_prepay", doc_id=100, staff=_staff(db, 4)
        )
        assert state["status"] == DocApprovalState.APPROVED
        assert state["approved_steps"] == [1, 2, 3]


def test_skip_step_is_rejected():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=101, staff=_staff(db, 1)
        )

        with pytest.raises(BizException) as exc:
            approval_service.approve(
                db, doc_type="purchase_prepay", doc_id=101, staff=_staff(db, 3)
            )
        assert "越级" in exc.value.message


def test_reject_stops_flow():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=102, staff=_staff(db, 1)
        )
        state = approval_service.reject(
            db,
            doc_type="purchase_prepay",
            doc_id=102,
            staff=_staff(db, 2),
            comment="金额有误",
        )
        assert state["status"] == DocApprovalState.REJECTED
        assert state["current_step"] is None

        with pytest.raises(BizException):
            approval_service.approve(
                db, doc_type="purchase_prepay", doc_id=102, staff=_staff(db, 3)
            )


def test_unaudit_requires_reason_and_invalidates_later_steps():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=103, staff=_staff(db, 1), doc_no="FK-103"
        )
        approval_service.approve(db, doc_type="purchase_prepay", doc_id=103, staff=_staff(db, 2))
        approval_service.approve(db, doc_type="purchase_prepay", doc_id=103, staff=_staff(db, 3))

        with pytest.raises(BizException) as exc:
            approval_service.unaudit(
                db,
                doc_type="purchase_prepay",
                doc_id=103,
                staff=_staff(db, 1),
                reason="   ",
            )
        assert "原因不能为空" in exc.value.message

        state = approval_service.unaudit(
            db,
            doc_type="purchase_prepay",
            doc_id=103,
            staff=_staff(db, 1),
            reason="操作失误",
            step=1,
        )
        assert state["status"] == DocApprovalState.PENDING
        assert state["current_step"] == 1

        history = approval_service.get_state(
            db, doc_type="purchase_prepay", doc_id=103
        )["records"]
        assert any(r["action"] == ApprovalAction.UNAUDIT and r["reason"] == "操作失误" for r in history)
        invalid_steps = {
            r["step"] for r in history if r["status"] == ApprovalStatus.INVALID
        }
        # 第 1 环节弃审后，第 1、2 环节的已审记录与第 3 环节的待审核记录全部失效。
        assert invalid_steps == {1, 2, 3}


def test_unaudit_requires_submitter_or_auditor():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=104, staff=_staff(db, 1)
        )
        approval_service.approve(db, doc_type="purchase_prepay", doc_id=104, staff=_staff(db, 2))

        with pytest.raises(BizException) as exc:
            approval_service.unaudit(
                db,
                doc_type="purchase_prepay",
                doc_id=104,
                staff=_staff(db, 3),
                reason="非授权人员",
            )
        assert "仅制单人或已审核人" in exc.value.message


def test_unaudit_then_reapprove():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        approval_service.submit(
            db, doc_type="purchase_prepay", doc_id=105, staff=_staff(db, 1)
        )
        approval_service.approve(db, doc_type="purchase_prepay", doc_id=105, staff=_staff(db, 2))
        approval_service.approve(db, doc_type="purchase_prepay", doc_id=105, staff=_staff(db, 3))
        approval_service.unaudit(
            db,
            doc_type="purchase_prepay",
            doc_id=105,
            staff=_staff(db, 2),
            reason="复核有误",
            step=2,
        )

        state = approval_service.approve(
            db, doc_type="purchase_prepay", doc_id=105, staff=_staff(db, 3)
        )
        assert state["status"] == DocApprovalState.PENDING
        assert state["current_step"] == 3


def test_missing_flow_rejected():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        with pytest.raises(BizException) as exc:
            approval_service.submit(
                db, doc_type="not_configured", doc_id=1, staff=_staff(db, 1)
            )
        assert "未配置审批流" in exc.value.message


def test_duplicate_doc_type_flow_rejected():
    Session = _session_factory()
    with Session() as db:
        _seed(db)
        _make_flow(db)
        with pytest.raises(BizException) as exc:
            approval_service.create_flow(
                db,
                name="重复流程",
                doc_type="purchase_prepay",
                approvers=[2],
                actor=_staff(db, 1),
            )
        assert "已配置审批流" in exc.value.message


def _make_api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(approval_router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed_roles(Session):
    with Session() as db:
        _seed(db)
        db.add_all(
            [
                Role(id=1, company_id=1, code="ADMIN", name="管理员"),
                Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
                Role(id=3, company_id=1, code="FINANCE", name="财务"),
            ]
        )
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=2),
                StaffRole(staff_id=2, role_id=2),
                StaffRole(staff_id=2, role_id=3),
                StaffRole(staff_id=3, role_id=3),
            ]
        )
        db.commit()


def _auth(staff_id: int) -> dict:
    token = create_access_token(staff_id, f"staff-{staff_id}", [])
    return {"Authorization": f"Bearer {token}"}


def test_api_requires_permission_and_runs_flow():
    app, Session = _make_api()
    _seed_roles(Session)
    client = TestClient(app)

    # PURCHASER 无审批流配置权限
    denied = client.post(
        "/approval/flows",
        json={"name": "采购付款审批", "doc_type": "purchase_prepay", "approvers": [2, 3]},
        headers=_auth(1),
    )
    assert denied.status_code == 403

    with Session() as db:
        approval_service.create_flow(
            db,
            name="采购付款审批",
            doc_type="purchase_prepay",
            approvers=[2, 3],
            actor=db.get(Staff, 1),
        )

    submit = client.post(
        "/approval/records/purchase_prepay/200/submit",
        json={"doc_no": "FK-200"},
        headers=_auth(1),
    )
    assert submit.status_code == 200
    assert submit.json()["data"]["current_step"] == 1

    # 第 2 环节审批人越级审批被拒绝
    skip = client.post(
        "/approval/records/purchase_prepay/200/approve",
        json={},
        headers=_auth(3),
    )
    assert skip.status_code == 403

    approved = client.post(
        "/approval/records/purchase_prepay/200/approve",
        json={"comment": "同意"},
        headers=_auth(2),
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["current_step"] == 2

    final = client.post(
        "/approval/records/purchase_prepay/200/approve",
        json={},
        headers=_auth(3),
    )
    assert final.json()["data"]["status"] == DocApprovalState.APPROVED
