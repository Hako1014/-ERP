"""TASK-081：配置权限、密钥保护、预算预占与用量未知语义。"""

from datetime import datetime
from decimal import Decimal
from time import monotonic

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.assistant import _record_tool_audit, router
from app.core.config import Settings
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.assistant_governance import AssistantConfig, AssistantUsage
from app.models.audit_log import AuditLog
from app.models.org import Company, Staff
from app.models.rbac import Role, StaffRole
from app.services import assistant_governance as governance
from app.services.assistant_governance import AssistantRuntime


def _fixture(role_code: str = "ADMIN"):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        db.add(Company(id=1, full_name="治理测试公司"))
        db.add(Role(id=1, company_id=1, code=role_code, name=role_code))
        db.add(
            Staff(
                id=1,
                company_id=1,
                name="测试员工",
                phone="13800000001",
                password_hash=hash_password("123456"),
                is_active=True,
            )
        )
        db.add(
            Staff(
                id=2,
                company_id=1,
                name="另一员工",
                phone="13800000002",
                password_hash=hash_password("123456"),
                is_active=True,
            )
        )
        db.add(StaffRole(staff_id=1, role_id=1))
        db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    token = create_access_token(1, "测试员工", [role_code])
    return TestClient(app), Session, {"Authorization": f"Bearer {token}"}


def _settings(**updates) -> Settings:
    values = {
        "assistant_enabled": True,
        "assistant_provider": "deepseek",
        "assistant_api_key": "",
        "assistant_model": "",
        "assistant_config_encryption_key": Fernet.generate_key().decode("ascii"),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_management_endpoints_are_limited_to_settings_managers(monkeypatch):
    client, Session, headers = _fixture("PURCHASER")
    monkeypatch.setattr(governance, "get_settings", lambda: _settings())

    read = client.get("/assistant/admin/config", headers=headers)
    update = client.put(
        "/assistant/admin/config",
        headers=headers,
        json={"enabled": False},
    )

    assert read.status_code == 403
    assert update.status_code == 403
    with Session() as db:
        assert db.scalar(select(AssistantConfig.id)) is None


def test_admin_secret_is_encrypted_and_never_echoed_or_audited(monkeypatch):
    client, Session, headers = _fixture()
    key = "sk-task081-sensitive-test-key"
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(
        governance,
        "get_settings",
        lambda: _settings(
            assistant_api_key="sk-environment-fallback",
            assistant_model="deepseek-v4-flash",
            assistant_config_encryption_key=encryption_key,
        ),
    )

    response = client.put(
        "/assistant/admin/config",
        headers=headers,
        json={
            "enabled": True,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": key,
            "requests_per_minute": 7,
            "concurrent_requests_per_user": 1,
            "daily_requests_per_user": 60,
            "monthly_budget": None,
            "input_price_per_million": None,
            "output_price_per_million": None,
        },
    )
    body = response.text
    assert response.status_code == 200
    assert key not in body
    assert "sk-environment-fallback" not in body
    assert response.json()["data"]["api_key_configured"] is True
    assert response.json()["data"]["api_key_overrides_environment"] is True

    with Session() as db:
        config = db.scalar(select(AssistantConfig))
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "ai.config.update"))
        assert config is not None
        assert config.api_key_ciphertext != key
        assert Fernet(encryption_key.encode()).decrypt(
            config.api_key_ciphertext.encode()
        ).decode() == key
        assert audit is not None
        assert key not in (audit.detail or "")

    reread = client.get("/assistant/admin/config", headers=headers)
    assert reread.status_code == 200
    assert key not in reread.text


def test_budget_cannot_be_enabled_without_both_prices(monkeypatch):
    client, Session, headers = _fixture()
    monkeypatch.setattr(governance, "get_settings", lambda: _settings())
    response = client.put(
        "/assistant/admin/config",
        headers=headers,
        json={
            "enabled": True,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "monthly_budget": "2.00",
            "input_price_per_million": None,
            "output_price_per_million": "0.60",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "40000"
    with Session() as db:
        assert db.scalar(select(AssistantConfig.id)) is None


def test_unknown_usage_keeps_budget_reservation_and_is_not_zero(monkeypatch):
    _client, Session, _headers = _fixture()
    settings = _settings()
    runtime = AssistantRuntime(
        settings=settings,
        requests_per_minute=10,
        concurrent_requests_per_user=1,
        daily_requests_per_user=None,
        monthly_budget=Decimal("0.3"),
        input_price_per_million=Decimal("1000"),
        output_price_per_million=Decimal("1000"),
        config_row_id=1,
        api_key_configured=True,
    )
    with Session() as db:
        db.add(
            AssistantConfig(
                company_id=1,
                enabled=True,
                provider="deepseek",
                model="deepseek-flash",
                requests_per_minute=10,
                concurrent_requests_per_user=1,
                monthly_budget=Decimal("0.3"),
                input_price_per_million=Decimal("1000"),
                output_price_per_million=Decimal("1000"),
            )
        )
        db.commit()
        usage = governance.reserve_usage(
            db,
            runtime=runtime,
            company_id=1,
            staff_id=1,
            request_id="request-unknown-000000000000000001",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=1,
            tool_calls_enabled=False,
        )
        assert usage.reserved_cost is not None and usage.reserved_cost > 0
        db.commit()

        governance.finalize_usage(
            db,
            company_id=1,
            request_id=usage.request_id,
            status="cancelled",
            failure_category="client_cancelled",
            started_at=monotonic() - 1,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            tool_names=[],
            runtime=runtime,
        )

    with Session() as db:
        settled = db.scalar(select(AssistantUsage).where(AssistantUsage.request_id == usage.request_id))
        assert settled is not None
        assert settled.cost_status == "unknown"
        assert settled.estimated_cost is None
        assert settled.reserved_cost is not None and settled.reserved_cost > 0
        with pytest.raises(BizException) as exhausted:
            governance.reserve_usage(
                db,
                runtime=runtime,
                company_id=1,
                staff_id=1,
                request_id="request-budget-000000000000000002",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=1,
                tool_calls_enabled=False,
            )
        assert exhausted.value.code.value == "40201"


def test_runtime_rejects_non_official_provider_host():
    _client, Session, _headers = _fixture()
    with Session() as db, pytest.raises(BizException) as captured:
        governance.resolve_runtime(
            db,
            1,
            env_settings=_settings(assistant_base_url="https://attacker.example/collect"),
        )
    assert captured.value.code.value == "50301"


def test_tool_audit_is_actor_bound_and_contains_no_arguments_or_result():
    _client, Session, _headers = _fixture()
    with Session() as db:
        _record_tool_audit(
            db,
            actor_id=1,
            request_id="00000000-0000-4000-8000-000000000081",
            tool_name="purchase_order_lookup",
            outcome="success",
        )
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "ai.tool.query"))
        assert audit is not None
        assert audit.actor_id == 1
        assert audit.target_id == "00000000-0000-4000-8000-000000000081"
        assert "purchase_order_lookup" in (audit.detail or "")
        assert "arguments" not in (audit.detail or "")
        assert "result" not in (audit.detail or "")


def test_usage_views_scope_admin_to_company_and_employee_to_self(monkeypatch):
    client, Session, headers = _fixture()
    monkeypatch.setattr(governance, "get_settings", lambda: _settings())
    with Session() as db:
        db.add_all(
            [
                AssistantUsage(
                    company_id=1,
                    staff_id=1,
                    request_id="00000000-0000-4000-8000-000000000101",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="success",
                    cost_status="unknown",
                    tool_names=[],
                    started_at=datetime(2026, 10, 1),
                ),
                AssistantUsage(
                    company_id=1,
                    staff_id=2,
                    request_id="00000000-0000-4000-8000-000000000102",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="success",
                    cost_status="unknown",
                    tool_names=[],
                    started_at=datetime(2026, 10, 1),
                ),
            ]
        )
        db.commit()

    my_usage = client.get("/assistant/usage/me", headers=headers)
    admin_usage = client.get("/assistant/admin/usage", headers=headers)
    assert my_usage.status_code == 200
    assert len(my_usage.json()["data"]["items"]) == 1
    assert my_usage.json()["data"]["items"][0]["request_id"].endswith("101")
    assert admin_usage.status_code == 200
    assert admin_usage.json()["data"]["summary"]["request_count"] == 2
    assert all(item["staff_name"] for item in admin_usage.json()["data"]["items"])

