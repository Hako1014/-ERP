"""API 鉴权覆盖与跨角色、跨数据越权回归。"""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.company import router as company_router
from app.api.deps import get_current_staff, require_permission
from app.api.purchase_plan import router as purchase_plan_router
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app as main_app
from app.models.org import Company, Department, Staff
from app.models.party import Supplier
from app.models.purchase import PurchasePlan
from app.models.rbac import Role, StaffRole


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db):
    db.add_all([Company(id=1, full_name="公司一"), Company(id=2, full_name="公司二")])
    db.add_all([
        Department(id=1, company_id=1, name="采购一部"),
        Department(id=2, company_id=1, name="采购二部"),
    ])
    db.add_all([
        Staff(id=1, company_id=1, department_id=1, name="甲"),
        Staff(id=2, company_id=1, department_id=1, name="乙"),
        Staff(id=3, company_id=1, department_id=2, name="丙"),
        Staff(id=4, company_id=2, name="丁"),
        Staff(id=5, company_id=1, name="管理员"),
    ])
    db.add_all([
        Role(id=1, company_id=1, code="ADMIN", name="管理员"),
        Role(id=2, company_id=1, code="PURCHASER", name="采购员"),
        StaffRole(staff_id=5, role_id=1),
        StaffRole(staff_id=1, role_id=2),
        StaffRole(staff_id=2, role_id=2),
        StaffRole(staff_id=3, role_id=2),
        Supplier(id=1, code="SUP-1", name="供应商", is_active=True),
    ])
    db.add_all([
        PurchasePlan(id=index, plan_no=f"PLAN-{index}", supplier_id=1, created_by=index)
        for index in range(1, 5)
    ])
    db.commit()


PUBLIC_ROUTES = {
    ("GET", "/"),
    ("GET", "/health"),
    ("POST", "/auth/login"),
    ("POST", "/auth/forgot-password/send-code"),
    ("POST", "/auth/forgot-password/reset"),
}
AUTH_ONLY_ROUTES = {
    ("GET", "/auth/me/permissions"),
    ("POST", "/upload"),
    ("GET", "/common/excel-template/{name}"),
    # 助手页面上下文与模块范围由 build_capabilities/assistant API 内部重新校验，
    # 不使用普通 CRUD 动作 checker。
    ("GET", "/assistant/capabilities"),
    ("POST", "/assistant/sessions"),
    ("POST", "/assistant/sessions/{session_id}/clear"),
    ("POST", "/assistant/messages/stream"),
    # 员工用量仅返回当前登录员工的数据，由路由内部固定 staff_id/company_id。
    ("GET", "/assistant/usage/me"),
    # 消息 API 不使用模块级 checker，而是逐条复核接收人、公司、事件状态与当前单据范围。
    ("GET", "/notifications"),
    ("GET", "/notifications/unread-count"),
    ("GET", "/notifications/events"),
    ("GET", "/notifications/{inbox_id}"),
    ("POST", "/notifications/{inbox_id}/read"),
    ("POST", "/notifications/read-all"),
}


def _dependencies(dependant):
    for child in dependant.dependencies:
        yield child.call
        yield from _dependencies(child)


def test_all_api_routes_have_declared_authentication_and_permission():
    routes = [route for route in main_app.routes if isinstance(route, APIRoute)]
    assert len(routes) >= 489
    unguarded = set()
    missing_permission = set()
    for route in routes:
        keys = {(method, route.path) for method in route.methods}
        dependencies = set(_dependencies(route.dependant))
        if get_current_staff not in dependencies:
            unguarded.update(keys)
        elif not any(getattr(dep, "__name__", "") == "checker" for dep in dependencies):
            missing_permission.update(keys)
    assert unguarded == PUBLIC_ROUTES
    assert missing_permission == AUTH_ONLY_ROUTES


def test_anonymous_access_to_protected_modules_is_rejected():
    client = TestClient(main_app)
    for path in ("/purchase-plans", "/staff", "/products", "/common/excel-template/product"):
        assert client.get(path).status_code == 401, path
    assert client.post("/upload", files={"file": ("x.png", b"image", "image/png")}).status_code == 401


def test_cross_role_and_cross_data_access_is_rejected():
    session_factory = _session_factory()
    with session_factory() as db:
        _seed(db)

    def override_get_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company_router)
    app.include_router(purchase_plan_router)
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    purchaser = {"Authorization": f"Bearer {create_access_token(1, '甲', [])}"}
    admin = {"Authorization": f"Bearer {create_access_token(5, '管理员', [])}"}

    assert client.put(
        "/staff/1/data-scope", headers=purchaser, json={"scope_type": "COMPANY"}
    ).status_code == 403
    assert client.get("/staff/4/data-scope", headers=admin).status_code == 404
    assert client.get("/purchase-plans/2", headers=purchaser).status_code == 404
    assert client.get("/purchase-plans/4", headers=admin).status_code == 404
    visible = client.get("/purchase-plans", headers=purchaser)
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()["data"]["items"]] == [1]
