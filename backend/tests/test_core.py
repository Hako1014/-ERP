from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import BizException, ErrorCode
from app.core.handlers import register_exception_handlers
from app.core.money import MONEY_QUANT, round_money, to_decimal
from app.core.response import Result


class Query(BaseModel):
    q: int


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise BizException(ErrorCode.NOT_FOUND, "商品不存在")

    @app.get("/validate")
    async def validate(q: int):
        return {"q": q}

    @app.get("/internal")
    async def internal():
        raise RuntimeError("boom")

    return app


def test_result_ok_and_fail():
    ok = Result.ok({"a": 1})
    assert ok.code == "0"
    assert ok.message == "ok"
    assert ok.data == {"a": 1}

    fail = Result.fail(ErrorCode.BAD_REQUEST.value, "参数错误")
    assert fail.code == "40000"
    assert fail.message == "参数错误"
    assert fail.data is None


def test_biz_exception_unified_response():
    resp = TestClient(_make_app()).get("/boom")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "40400"
    assert body["message"] == "商品不存在"


def test_validation_error_unified_response():
    resp = TestClient(_make_app()).get("/validate?q=abc")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "42200"
    assert body["data"][0]["loc"] == ["query", "q"]


def test_unhandled_exception_returns_500():
    # 兜底 Exception 处理器由 ServerErrorMiddleware 承载，处理后会重新抛出；
    # 测试端需关闭 raise_server_exceptions 才能拿到 500 响应。
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/internal")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "50000"


def test_money_precision():
    assert round_money("12.345") == MONEY_QUANT * 1235
    assert round_money("19.999") == MONEY_QUANT * 2000
    assert round_money(12) == MONEY_QUANT * 1200
    assert to_decimal("0.1") + to_decimal("0.2") == to_decimal("0.3")


def test_money_rejects_float():
    import pytest

    with pytest.raises(TypeError):
        round_money(1.23)
