import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.auth import router as auth_router
from app.api.company import router as company_router
from app.api.purchase_plan import router as purchase_plan_router
from app.api.deps import get_current_staff
from app.core.data_scope import (
    ScopePolicy,
    apply_owner_scope,
    clear_scope_policy,
    document_visible,
    initialize_scope_policy,
    owner_visible,
    require_scope_policy,
    scope_conditions,
)
from app.core.handlers import register_exception_handlers
from app.core.ip_access import IpAccessMiddleware, is_ip_allowed
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Department, Staff
from app.models.party import Supplier
from app.models.purchase import PurchasePlan
from app.models.rbac import DataScope, IpRule, Role, StaffRole


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db: Session):
    db.add_all([Company(id=1, full_name="公司一"), Company(id=2, full_name="公司二")])
    db.add_all(
        [
            Department(id=1, company_id=1, name="采购一部"),
            Department(id=2, company_id=1, name="采购二部"),
        ]
    )
    db.add_all(
        [
            Staff(id=1, company_id=1, department_id=1, name="甲", login_account="user-one", password_hash=hash_password("123456")),
            Staff(id=2, company_id=1, department_id=1, name="乙"),
            Staff(id=3, company_id=1, department_id=2, name="丙"),
            Staff(id=4, company_id=2, name="丁"),
            Staff(id=5, company_id=1, name="管理员"),
        ]
    )
    db.add_all(
        [
            Role(id=1, company_id=1, code="ADMIN", name="管理员"),
            Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
        ]
    )
    db.add_all(
        [StaffRole(staff_id=5, role_id=1)]
        + [StaffRole(staff_id=staff_id, role_id=2) for staff_id in (1, 2, 3)]
    )
    db.add(Supplier(id=1, code="SUP-1", name="供应商", is_active=True))
    db.add_all(
        [
            PurchasePlan(id=index, plan_no=f"PLAN-{index}", supplier_id=1, created_by=index)
            for index in range(1, 5)
        ]
    )
    db.commit()


def _visible_plan_ids(db: Session, staff_id: int):
    staff = db.get(Staff, staff_id)
    clear_scope_policy(db)
    initialize_scope_policy(db, staff)
    stmt = apply_owner_scope(select(PurchasePlan.id), PurchasePlan.created_by, db)
    count_stmt = apply_owner_scope(select(func.count(PurchasePlan.id)), PurchasePlan.created_by, db)
    ids = db.scalars(stmt.order_by(PurchasePlan.id)).all()
    assert db.scalar(count_stmt) == len(ids)
    return ids


def test_data_scope_self_department_company_and_admin():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        assert _visible_plan_ids(db, 1) == [1]
        assert not owner_visible(db, 2)

        db.add(DataScope(staff_id=1, scope_type="DEPARTMENT", department_id=1))
        db.commit()
        assert _visible_plan_ids(db, 1) == [1, 2]
        assert owner_visible(db, 2)
        assert not owner_visible(db, 3)

        db.add(DataScope(staff_id=1, scope_type="COMPANY"))
        db.commit()
        assert _visible_plan_ids(db, 1) == [1, 2, 3]
        assert _visible_plan_ids(db, 5) == [1, 2, 3]


def test_data_scope_helpers_fail_closed_without_policy():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        checks = (
            lambda: apply_owner_scope(
                select(PurchasePlan.id), PurchasePlan.created_by, db
            ),
            lambda: scope_conditions(db, PurchasePlan.created_by),
            lambda: owner_visible(db, 1),
            lambda: document_visible(db, 1, "PURCHASE_PLAN", 1),
        )
        for check in checks:
            with pytest.raises(RuntimeError, match="数据范围策略未初始化"):
                check()


def test_scope_policy_rejects_identity_and_policy_type_mismatch():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        staff = db.get(Staff, 1)
        initialize_scope_policy(db, staff)

        with pytest.raises(RuntimeError, match="当前身份不匹配"):
            require_scope_policy(db, staff_id=2)
        with pytest.raises(RuntimeError, match="当前身份不匹配"):
            require_scope_policy(db, company_id=2)
        with pytest.raises(RuntimeError, match="当前身份不匹配"):
            initialize_scope_policy(db, db.get(Staff, 4))

        db.info["data_scope_policy"] = ScopePolicy(
            staff_id=1, company_id=1, department_id=1, scope_type="SYSTEM"
        )
        with pytest.raises(RuntimeError, match="策略未初始化"):
            owner_visible(db, 1)


def test_ip_rules_deny_precedence_allow_list_and_company_isolation():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        assert is_ip_allowed(db, 1, "192.168.1.10")
        db.add_all(
            [
                IpRule(company_id=1, rule_type="ALLOW", ip_address="192.168.1.0/24"),
                IpRule(company_id=1, rule_type="DENY", ip_address="192.168.1.10"),
            ]
        )
        db.commit()
        assert is_ip_allowed(db, 1, "192.168.1.11")
        assert not is_ip_allowed(db, 1, "192.168.1.10")
        assert not is_ip_allowed(db, 1, "10.0.0.1")
        assert is_ip_allowed(db, 2, "10.0.0.1")


def test_purchase_plan_api_list_and_detail_respect_scope():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)

    def override_get_db():
        with SessionLocal() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(purchase_plan_router)
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(1, '甲', [])}"}

    first = client.get("/purchase-plans", headers=headers)
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["data"]["items"]] == [1]
    assert client.get("/purchase-plans/2", headers=headers).status_code == 404

    with SessionLocal() as db:
        db.add(DataScope(staff_id=1, scope_type="DEPARTMENT", department_id=1))
        db.commit()
    department = client.get("/purchase-plans", headers=headers)
    assert department.json()["data"]["total"] == 2
    assert client.get("/purchase-plans/2", headers=headers).status_code == 200
    assert client.get("/purchase-plans/3", headers=headers).status_code == 404

    with SessionLocal() as db:
        db.add(DataScope(staff_id=1, scope_type="COMPANY"))
        db.commit()
    company = client.get("/purchase-plans", headers=headers)
    assert company.json()["data"]["total"] == 3
    assert client.get("/purchase-plans/4", headers=headers).status_code == 404


def test_data_scope_settings_api_is_company_bounded():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)

    def override_get_db():
        with SessionLocal() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company_router)
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    admin_headers = {"Authorization": f"Bearer {create_access_token(5, '管理员', [])}"}
    staff_headers = {"Authorization": f"Bearer {create_access_token(1, '甲', [])}"}

    assert client.get("/staff/1/data-scope", headers=admin_headers).json()["data"]["scope_type"] == "SELF"
    updated = client.put(
        "/staff/1/data-scope",
        headers=admin_headers,
        json={"scope_type": "DEPARTMENT"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["scope_type"] == "DEPARTMENT"
    assert client.get("/staff/1/data-scope", headers=admin_headers).json()["data"]["scope_type"] == "DEPARTMENT"
    assert client.put("/staff/1/data-scope", headers=staff_headers, json={"scope_type": "COMPANY"}).status_code == 403
    assert client.get("/staff/4/data-scope", headers=admin_headers).status_code == 404


def test_login_and_authenticated_requests_blocked_by_ip():
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        db.add(IpRule(company_id=1, rule_type="ALLOW", ip_address="192.168.1.0/24"))
        db.commit()

    def override_get_db():
        with SessionLocal() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(IpAccessMiddleware, session_factory=SessionLocal)
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = override_get_db

    @app.get("/protected")
    def protected(staff: Staff = Depends(get_current_staff)):
        return {"staff_id": staff.id}

    denied_client = TestClient(app, client=("10.0.0.9", 4567))
    login = denied_client.post("/auth/login", json={"account": "user-one", "password": "123456"})
    assert login.status_code == 403
    token = create_access_token(1, "甲", [])
    assert denied_client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 403

    allowed_client = TestClient(app, client=("192.168.1.11", 4567))
    allowed_login = allowed_client.post("/auth/login", json={"account": "user-one", "password": "123456"})
    assert allowed_login.status_code == 200
    assert allowed_client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 200
