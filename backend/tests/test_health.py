from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_index():
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "0"
    assert body["message"] == "ok"
    assert body["data"]["name"] == "电商 ERP API"


def test_request_id_header():
    resp = client.get("/health")
    assert resp.headers.get("x-request-id")
