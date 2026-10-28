"""TASK-076：AI 助手能力查询的鉴权、授权与配置状态。

只覆盖能力查询接口本身；对话流式、工具分发与快捷录入由后续任务卡负责。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.assistant import router
from app.core.config import Settings
from app.core.handlers import register_exception_handlers
from app.core.permissions import (
    MODULES,
    permissions_for_roles,
    visible_modules,
)
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import assistant_service

ALL_ROLES = ["ADMIN", "VP", "FINANCE", "PURCHASER", "WAREHOUSE", "QUALITY", "AFTER_SALE"]


def _make_app():
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
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    return app, Session


def _seed(Session, role_code: str) -> None:
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


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(1, '张三', ['X'])}"}


def _settings(**overrides) -> Settings:
    values = {"assistant_enabled": True, "assistant_api_key": ""}
    values.update(overrides)
    return Settings(**values)


def _client(monkeypatch, role_code: str = "PURCHASER", **settings_overrides):
    app, Session = _make_app()
    _seed(Session, role_code)
    monkeypatch.setattr(
        assistant_service, "get_settings", lambda: _settings(**settings_overrides)
    )
    return TestClient(app), Session


def test_capabilities_requires_login(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.get("/assistant/capabilities")
    assert response.status_code == 401
    assert response.json()["code"] == "40100"


def test_capabilities_rejects_identity_without_any_module(monkeypatch):
    client, _ = _client(monkeypatch, role_code="GUEST")
    response = client.get("/assistant/capabilities", headers=_auth_header())
    assert response.status_code == 403
    assert response.json()["code"] == "40300"


@pytest.mark.parametrize("role_code", ALL_ROLES)
def test_every_role_can_read_capabilities(monkeypatch, role_code):
    client, _ = _client(monkeypatch, role_code=role_code)
    response = client.get("/assistant/capabilities", headers=_auth_header())
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    expected = visible_modules(permissions_for_roles([role_code]))
    assert body["data"]["modules"] == expected
    assert body["data"]["roles"] == [role_code]
    assert body["data"]["permissions"] == sorted(permissions_for_roles([role_code]))
    assert body["data"]["staff_id"] == 1
    # 七类角色都能看到指引能力，且模块集合非空。
    assert expected
    guide = next(f for f in body["data"]["features"] if f["code"] == "guide")
    assert guide["enabled"] is True


def test_first_version_enables_authorized_read_query_without_enabling_writes(monkeypatch):
    client, _ = _client(monkeypatch, role_code="ADMIN")
    features = {
        item["code"]: item
        for item in client.get(
            "/assistant/capabilities", headers=_auth_header()
        ).json()["data"]["features"]
    }
    # 单公司数据库与 DeepSeek 工具模式可开放固定只读工具；写入仍未开放。
    assert features["read_query"]["enabled"] is True
    assert "reason" not in features["read_query"]
    # TASK-085 首试仅开放经服务端权限白名单确认的采购计划。
    assert features["draft_entry"]["enabled"] is True
    assert features["draft_entry"]["document_types"] == ["purchase_plan"]


def test_multi_company_database_keeps_company_scoped_read_query_enabled(monkeypatch):
    client, Session = _client(monkeypatch, role_code="ADMIN")
    with Session() as db:
        db.add(Company(id=2, full_name="另一家公司"))
        db.commit()

    features = {
        item["code"]: item
        for item in client.get(
            "/assistant/capabilities", headers=_auth_header()
        ).json()["data"]["features"]
    }
    assert features["read_query"]["enabled"] is True
    assert "reason" not in features["read_query"]
    # 快捷录入仍有自己的主数据归属限制，不随只读查询一并放开。
    assert features["draft_entry"]["enabled"] is False


def test_draft_entry_capability_is_limited_to_purchase_create_permission(monkeypatch):
    purchaser, _ = _client(monkeypatch, role_code="PURCHASER")
    after_sale, _ = _client(monkeypatch, role_code="AFTER_SALE")
    purchaser_data = purchaser.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    after_sale_data = after_sale.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    purchaser_feature = next(item for item in purchaser_data["features"] if item["code"] == "draft_entry")
    after_sale_feature = next(item for item in after_sale_data["features"] if item["code"] == "draft_entry")
    assert purchaser_feature["enabled"] is True
    assert purchaser_feature["document_types"] == ["purchase_plan"]
    assert after_sale_feature["enabled"] is False
    assert after_sale_feature["reason"] == "purchase_create_not_authorized"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "unconfigured"),
        ({"assistant_api_key": "sk-test", "assistant_model": "test-model"}, "ready"),
        ({"assistant_enabled": False, "assistant_api_key": "sk-test"}, "unavailable"),
    ],
)
def test_service_status_reflects_configuration(monkeypatch, overrides, expected):
    client, _ = _client(monkeypatch, **overrides)
    data = client.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    assert data["service_status"] == expected


def test_response_never_exposes_secret_or_unverified_model(monkeypatch):
    client, _ = _client(monkeypatch, assistant_api_key="sk-super-secret")
    body = client.get("/assistant/capabilities", headers=_auth_header())
    text = body.text
    assert "sk-super-secret" not in text
    assert "api_key" not in text
    assert "model" not in text


def test_explicit_debug_mock_mode_is_ready_without_key_and_is_identified(monkeypatch):
    client, _ = _client(
        monkeypatch,
        assistant_provider="mock",
        assistant_api_key="",
        assistant_model="",
        debug=True,
    )
    data = client.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    assert data["service_status"] == "ready"
    assert data["provider_mode"] == "mock"
    read_query = next(item for item in data["features"] if item["code"] == "read_query")
    assert read_query["enabled"] is False
    assert read_query["reason"] == "provider_tools_unavailable"


def test_mock_mode_is_not_available_outside_debug(monkeypatch):
    client, _ = _client(
        monkeypatch,
        assistant_provider="mock",
        assistant_api_key="",
        assistant_model="",
        debug=False,
    )
    data = client.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    assert data["service_status"] == "unavailable"
    assert data["provider_mode"] == "unavailable"


def test_admin_wildcard_covers_all_modules(monkeypatch):
    client, _ = _client(monkeypatch, role_code="ADMIN")
    data = client.get("/assistant/capabilities", headers=_auth_header()).json()["data"]
    assert data["modules"] == list(MODULES)


def test_role_matrix_is_not_derived_from_request_payload(monkeypatch):
    """客户端提交的角色/权限字段不能扩大能力范围。"""
    client, _ = _client(monkeypatch, role_code="AFTER_SALE")
    response = client.get(
        "/assistant/capabilities",
        headers=_auth_header(),
        params={"roles": "ADMIN", "permissions": "*", "modules": "finance"},
    )
    data = response.json()["data"]
    assert data["roles"] == ["AFTER_SALE"]
    assert "finance" not in data["modules"]
    assert data["modules"] == visible_modules(permissions_for_roles(["AFTER_SALE"]))
