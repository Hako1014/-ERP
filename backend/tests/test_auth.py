from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router
from app.api.deps import get_current_staff, require_permission
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.permissions import permissions_for_roles
from app.core.security import create_access_token, decode_token, hash_password, verify_password
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.audit_log import AuditLog
from app.models.rbac import Role, StaffRole
from app.services import auth_service


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def incr(self, key, amount=1):
        value = int(self.store.get(key, "0")) + amount
        self.store[key] = str(value)
        return value

    def expire(self, key, seconds):
        return key in self.store


def _make_app(monkeypatch):
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

    fake_redis = _FakeRedis()
    monkeypatch.setattr(auth_service, "get_redis", lambda: fake_redis)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    @app.get("/me")
    def me(staff=Depends(get_current_staff)):
        return {"staff_id": staff.id, "name": staff.name}

    @app.get("/finance-only")
    def finance_only(staff=Depends(require_permission("finance:manage"))):
        return {"ok": True}

    return app, Session, fake_redis


def _seed_staff(Session, phone="13800000001", password="123456"):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add(
            Staff(
                company_id=1,
                name="张三",
                phone=phone,
                login_account=phone,
                password_hash=hash_password(password),
                is_active=True,
            )
        )
        db.commit()


def _seed_staff_with_role(Session, role_code):
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add(Role(id=1, company_id=1, code=role_code, name=role_code))
        db.add(
            Staff(
                id=1,
                company_id=1,
                name="张三",
                phone="13800000001",
                password_hash=hash_password("123456"),
                is_active=True,
            )
        )
        db.add(StaffRole(staff_id=1, role_id=1))
        db.commit()


def test_password_hash_verify():
    hashed = hash_password("123456")
    assert verify_password("123456", hashed)
    assert not verify_password("wrong", hashed)


def test_token_roundtrip():
    token = create_access_token(1, "张三", ["PURCHASER"])
    payload = decode_token(token)
    assert payload["staff_id"] == 1
    assert payload["roles"] == ["PURCHASER"]


def test_token_expired_raises():
    settings = get_settings()
    token = pyjwt.encode(
        {"sub": "1", "staff_id": 1, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(token)


def test_login_success(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff(Session)
    client = TestClient(app)
    resp = client.post("/auth/login", json={"phone": "13800000001", "password": "123456"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "0"
    assert body["data"]["token"]
    assert body["data"]["name"] == "张三"


def test_login_by_login_account(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff(Session)
    with Session() as db:
        staff = db.scalar(
            select(Staff).where(Staff.phone == "13800000001")
        )
        staff.login_account = "zhangsan"
        db.commit()

    client = TestClient(app)
    resp = client.post(
        "/auth/login",
        json={"account": "zhangsan", "password": "123456"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "张三"


def test_login_wrong_password(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff(Session)
    client = TestClient(app)
    resp = client.post("/auth/login", json={"phone": "13800000001", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "40100"


def test_forgot_password_flow(monkeypatch):
    app, Session, cache = _make_app(monkeypatch)
    _seed_staff(Session, password="old-pass")
    client = TestClient(app)

    send = client.post("/auth/forgot-password/send-code", json={"phone": "13800000001"})
    assert send.status_code == 200
    assert send.json()["data"] == {"sent": True}
    code = cache.get("sms:reset:13800000001")
    assert len(code) == 6

    reset = client.post(
        "/auth/forgot-password/reset",
        json={"phone": "13800000001", "code": code, "new_password": "new-pass1"},
    )
    assert reset.status_code == 200
    with Session() as db:
        records = db.scalars(select(AuditLog).where(AuditLog.action == "RESET_PASSWORD")).all()
        assert len(records) == 1
        assert records[0].actor_id == 1
        assert records[0].target_id == "1"

    login = client.post("/auth/login", json={"phone": "13800000001", "password": "new-pass1"})
    assert login.status_code == 200


def test_sms_mock_code_only_visible_for_explicit_loopback_debug(monkeypatch):
    from types import SimpleNamespace
    import app.api.auth as auth_api

    app, Session, _ = _make_app(monkeypatch)
    _seed_staff(Session)
    monkeypatch.setattr(auth_api, "get_settings", lambda: SimpleNamespace(debug=True, sms_mock_show_code=True))
    local = TestClient(app, client=("127.0.0.1", 1234))
    response = local.post("/auth/forgot-password/send-code", json={"phone": "13800000001"})
    assert len(response.json()["data"]["mock_code"]) == 6
    assert local.post("/auth/forgot-password/send-code", json={"phone": "13800000001"}).status_code == 400

    app2, Session2, _ = _make_app(monkeypatch)
    _seed_staff(Session2)
    remote = TestClient(app2, client=("203.0.113.8", 1234))
    assert remote.post("/auth/forgot-password/send-code", json={"phone": "13800000001"}).json()["data"] == {"sent": True}


def test_me_requires_token(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff(Session)
    client = TestClient(app)

    assert client.get("/me").status_code == 401

    login = client.post("/auth/login", json={"phone": "13800000001", "password": "123456"}).json()
    token = login["data"]["token"]
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "张三"


def test_permission_matrix():
    assert permissions_for_roles(["ADMIN"]) == {"*"}
    purchaser = permissions_for_roles(["PURCHASER"])
    assert "purchase:create" in purchaser
    assert "finance:manage" not in purchaser
    finance = permissions_for_roles(["FINANCE"])
    assert "finance:manage" in finance


def test_require_permission_forbidden(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff_with_role(Session, "PURCHASER")
    client = TestClient(app)
    token = client.post("/auth/login", json={"phone": "13800000001", "password": "123456"}).json()["data"]["token"]

    resp = client.get("/finance-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "40300"


def test_require_permission_allowed(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff_with_role(Session, "FINANCE")
    client = TestClient(app)
    token = client.post("/auth/login", json={"phone": "13800000001", "password": "123456"}).json()["data"]["token"]

    resp = client.get("/finance-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_my_permissions_endpoint(monkeypatch):
    app, Session, _ = _make_app(monkeypatch)
    _seed_staff_with_role(Session, "PURCHASER")
    client = TestClient(app)
    token = client.post("/auth/login", json={"phone": "13800000001", "password": "123456"}).json()["data"]["token"]

    resp = client.get("/auth/me/permissions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["roles"] == ["PURCHASER"]
    assert "module:purchase" in data["permissions"]
    assert "purchase" in data["modules"]
