from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.upload import router, validate_and_save
from app.api.deps import get_current_staff
from app.core.errors import BizException
from app.core.handlers import register_exception_handlers
from app.services import storage as storage_mod
from app.services.storage import LocalStorage


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_local_storage_save(tmp_path):
    storage = LocalStorage(tmp_path)
    url = storage.save(b"hello", "png")
    assert url.startswith("/uploads/")
    assert (tmp_path / url.removeprefix("/uploads/")).read_bytes() == b"hello"


def test_validate_and_save_ok(tmp_path):
    url = validate_and_save(_png_bytes(), "a.png", LocalStorage(tmp_path), 10 * 1024 * 1024)
    assert url.startswith("/uploads/")


def test_validate_rejects_bad_extension(tmp_path):
    with pytest.raises(BizException):
        validate_and_save(b"x", "a.txt", LocalStorage(tmp_path), 1024)


def test_validate_rejects_oversize(tmp_path):
    with pytest.raises(BizException):
        validate_and_save(_png_bytes(), "a.png", LocalStorage(tmp_path), 10)


def test_validate_rejects_non_image(tmp_path):
    with pytest.raises(BizException):
        validate_and_save(b"not an image", "a.png", LocalStorage(tmp_path), 1024)


def test_upload_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "_storage", LocalStorage(tmp_path))
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)
    assert client.post("/upload", files={"file": ("a.png", _png_bytes(), "image/png")}).status_code == 401
    app.dependency_overrides[get_current_staff] = lambda: object()

    resp = client.post(
        "/upload",
        files={"file": ("a.png", _png_bytes(), "image/png")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "0"
    assert body["data"]["url"].startswith("/uploads/")
