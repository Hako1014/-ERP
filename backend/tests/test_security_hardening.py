"""TASK-063 安全加固清单：CORS/CSRF、密码、金额、审计与权限。"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Float

import app.models  # noqa: F401
from app.core.errors import BizException
from app.core.money import round_money, to_decimal
from app.core.security import validate_new_password
from app.db.base import Base
from app.main import app


def test_cors_allows_configured_origin_without_cookie_credentials():
    response = TestClient(app).options(
        "/purchase-plans",
        headers={"Origin": "http://localhost:5273", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5273"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_rejects_unknown_origin_and_bearer_auth_is_csrf_resistant():
    response = TestClient(app).options(
        "/purchase-plans",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    client = TestClient(app)
    client.cookies.set("access_token", "forged")
    assert client.get("/purchase-plans").status_code == 401


@pytest.mark.parametrize("password", ["12345678", "abcdefgh", "abc123", "a" * 65, "        "])
def test_weak_new_password_is_rejected(password):
    with pytest.raises(BizException):
        validate_new_password(password)


def test_strong_new_password_and_money_precision():
    assert validate_new_password("SafePass123") == "SafePass123"
    assert round_money(Decimal("0.1") + Decimal("0.2")) == Decimal("0.30")
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_database_models_do_not_use_float_columns():
    violations = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float)
    ]
    assert violations == []
