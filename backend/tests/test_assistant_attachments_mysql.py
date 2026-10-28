"""TASK-106 本机 MySQL 定向验证；复用外层事务，新增数据全部回滚。"""

import io
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from PIL import Image
from openpyxl import Workbook
from pypdf import PdfWriter
from sqlalchemy import func, select, text

from app.api.assistant_attachments import router
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.assistant_attachment import AssistantAttachment
from app.models.assistant_conversation import AssistantMessage
from app.models.org import Staff
from app.services import assistant_attachment_files as files
from app.services import assistant_attachments as service
from app.services import assistant_conversations as chats
from tests.test_assistant_conversations_mysql import ctx, create, data, send  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("ERP_TASK104_MYSQL") != "1", reason="仅显式启用本机 MySQL 测试")


@pytest.fixture
def env(ctx, monkeypatch, tmp_path):
    settings = get_settings().model_copy(update={"assistant_private_dir": str(tmp_path / "private"), "upload_dir": str(tmp_path / "public")})
    for module in (files, service):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    ctx.client.app.include_router(router)
    ctx.settings, ctx.private = settings, tmp_path / "private"
    try:
        yield ctx
    finally:
        # 仅清理本用例独有临时目录中的测试文件，不接触真实附件目录。
        if ctx.private.exists():
            for target in ctx.private.iterdir():
                target.unlink()


def image_bytes(fmt="PNG"):
    output = io.BytesIO()
    image = Image.new("RGB", (12, 10), "red")
    exif = image.getexif(); exif[270] = "必须去掉的元数据"
    image.save(output, fmt, exif=exif)
    return output.getvalue()


def pdf_bytes(encrypted=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(100, 100)
    if encrypted:
        writer.encrypt("test-only")
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def xlsx_bytes():
    workbook = Workbook(); workbook.active.append(["商品", "=1+1", "<script>alert(1)</script>"])
    output = io.BytesIO(); workbook.save(output); workbook.close()
    return output.getvalue()


def upload(env, conversation, name="测试.txt", content=b"test"):
    return env.client.post("/assistant/attachments", data={"conversation_id": conversation["id"]}, files={"file": (name, content, "application/octet-stream")})


def path(row, suffix=""):
    return f"/assistant/attachments/{row['id']}{suffix}?conversation_id={row['conversation_id']}"


def save_body(conversation, *rows):
    return {"conversation_id": conversation["id"], "version": conversation["version"],
            "request_id": str(uuid4()), "client_message_id": str(uuid4()),
            "attachment_ids": [row["id"] for row in rows], "content": "这段仅私有保存，不允许自动进入后续模型历史"}


@pytest.mark.parametrize("name,content", [("图片.jpg", lambda: image_bytes("JPEG")), ("图片.png", image_bytes),
    ("图片.webp", lambda: image_bytes("WEBP")), ("文本.txt", lambda: "你好".encode()),
    ("表.csv", lambda: b"name,value\na,=1+1"), ("表.xlsx", xlsx_bytes), ("单据.pdf", pdf_bytes)])
def test_supported_formats_private_content_and_preview(env, name, content):
    conversation = create(env)
    row = data(upload(env, conversation, name, content()))
    assert row["status"] == "READY" and row["purpose"] == "CHAT" and not row["model_sent"]
    assert not {"storage_key", "url", "sha256"} & row.keys()
    response = env.client.get(path(row, "/content"))
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    preview = data(env.client.get(path(row, "/preview")))
    if row["media_type"].startswith("image/"):
        assert preview["kind"] == "image"
        with Image.open(io.BytesIO(response.content)) as image:
            assert not image.getexif()
    elif name.endswith("xlsx"):
        assert preview["rows"][0][1] == "=1+1" and preview["truncated"]
    elif name.endswith("pdf"):
        assert preview["pages"] == 1 and preview["kind"] == "metadata"
    else:
        assert preview["kind"] == "text"


def test_owner_company_conversation_and_unauthenticated_isolation(env):
    conversation = create(env); row = data(upload(env, conversation))
    other = create(env)
    for suffix in ("", "/content", "/preview"):
        assert env.client.get(path(row, suffix), headers={"Authorization": ""}).status_code == 401
        for staff_id in (env.ids.bob, env.ids.outsider):
            token = create_access_token(staff_id, "test-only", ["ADMIN"])
            assert env.client.get(path(row, suffix), headers={"Authorization": "Bearer " + token}).status_code == 404
        assert env.client.get(path(row, suffix).replace(conversation["id"], other["id"])).status_code == 404
    token = create_access_token(env.ids.bob, "test-only", ["ADMIN"])
    assert env.client.delete(path(row), headers={"Authorization": "Bearer " + token}).status_code == 404
    assert env.client.post("/assistant/attachments", headers={"Authorization": "Bearer " + token},
        data={"conversation_id": conversation["id"]}, files={"file": ("x.txt", b"x")}).status_code == 404
    assert env.client.get(path(row).replace(row["id"], str(uuid4()))).status_code == 404
    from starlette.staticfiles import StaticFiles
    env.private.parent.joinpath("public").mkdir()
    env.client.app.mount("/uploads", StaticFiles(directory=str(env.private.parent / "public")))
    with env.factory() as db:
        stored = db.get(AssistantAttachment, row["id"])
        assert env.client.get("/uploads/" + stored.storage_key).status_code == 404


@pytest.mark.parametrize("name,content", [("假.png", lambda: b"not-image"), ("错.jpg", image_bytes),
    ("a.svg", lambda: b"<svg/>"), ("a.html", lambda: b"<script>1</script>"), ("a.xlsm", xlsx_bytes),
    ("坏.pdf", lambda: b"%PDF-invalid"), ("加密.pdf", lambda: pdf_bytes(True)),
    ("太多页.pdf", lambda: pdf_bytes(pages=21)), ("坏.xlsx", lambda: b"not-zip"),
    ("二进制.txt", lambda: b"\x00\x01data"), ("空.txt", lambda: b"")])
def test_reject_invalid_types_corrupt_encrypted_and_page_limit(env, name, content):
    response = upload(env, create(env), name, content())
    assert response.status_code == 422
    assert not env.private.exists()


def test_zip_bomb_macros_external_and_xml_entities_rejected(env):
    conversation = create(env)
    for extra, value in [("xl/vbaProject.bin", b"bad"), ("xl/externalLinks/x.xml", b"bad"),
                         ("../bad.xml", b"bad"), ("xl/test.xml", b"<!DOCTYPE x [<!ENTITY a 'boom'>]><x>&a;</x>"),
                         ("xl/big.xml", b" " * 100_000)]:
        output = io.BytesIO(xlsx_bytes())
        with ZipFile(output, "a", ZIP_DEFLATED) as archive:
            archive.writestr(extra, value)
        assert upload(env, conversation, "坏.xlsx", output.getvalue()).status_code == 422


def test_size_count_total_pixels_and_owner_quota(env):
    conversation = create(env)
    env.settings.assistant_file_max_mb = 1
    assert upload(env, conversation, content=b"x" * (1024 * 1024 + 1)).status_code == 422
    env.settings.assistant_image_max_pixels = 10
    assert upload(env, conversation, "x.png", image_bytes()).status_code == 422
    env.settings.assistant_files_owner_quota_mb = 1
    data(upload(env, conversation, content=b"x" * (700 * 1024)))
    assert upload(env, create(env), content=b"x" * (400 * 1024)).status_code == 409
    env.settings.assistant_files_owner_quota_mb = 250
    env.settings.assistant_files_total_mb = 1
    assert upload(env, conversation, content=b"x" * (400 * 1024)).status_code == 422
    env.settings.assistant_files_per_message = 1
    assert upload(env, conversation).status_code == 422
    current = data(env.client.get("/assistant/attachments/policy"))
    assert current["max_files"] == 1 and current["max_pdf_pages"] == 20


def test_save_history_idempotency_and_not_sent_to_model(env):
    conversation = create(env); row = data(upload(env, conversation))
    body = save_body(conversation, row)
    saved = data(env.client.post("/assistant/attachments/messages", json=body))
    replay = data(env.client.post("/assistant/attachments/messages", json=body))
    assert saved["message"]["id"] == replay["message"]["id"]
    assert saved["message"]["attachments"][0]["id"] == row["id"]
    assert env.provider.calls == []
    assert data(env.client.get("/assistant/attachments", params={"conversation_id": conversation["id"]})) == []
    messages = data(env.client.get(f"/assistant/conversations/{conversation['id']}/messages"))["items"]
    assert messages[0]["attachments"][0]["id"] == row["id"]
    assert env.client.delete(path(row)).status_code == 409
    current = data(env.client.get(f"/assistant/conversations/{conversation['id']}"))
    send(env, current)
    assert body["content"] not in str(env.provider.calls)
    assert env.client.post("/assistant/attachments/messages", json={**body, "content": "different"}).status_code == 409
    # 文件只能关联所属会话，不能挪用到自己的另一会话。
    other = create(env)
    assert env.client.post("/assistant/attachments/messages", json=save_body(other, row)).status_code == 404


def test_remove_orphans_clear_cleanup_and_business_exclusion(env):
    conversation = create(env)
    orphan = data(upload(env, conversation))
    kept = data(upload(env, conversation, name="业务.txt"))
    with env.factory() as db:
        business = db.get(AssistantAttachment, kept["id"])
        business.purpose = "BUSINESS"; business.purge_after = chats.utcnow() - timedelta(hours=1)
        db.commit()
        business_path = files.PrivateAttachmentStorage().path(business.storage_key)
    assert env.client.get(path(kept, "/content")).status_code == 404
    data(env.client.delete(path(orphan)))
    assert env.client.get(path(orphan, "/content")).status_code == 404
    linked = data(upload(env, conversation))
    saved = data(env.client.post("/assistant/attachments/messages", json=save_body(conversation, linked)))
    data(env.client.post(f"/assistant/conversations/{conversation['id']}/clear", json={"version": saved["version"], "request_id": str(uuid4())}))
    assert env.client.get(path(linked, "/content")).status_code == 404
    fresh = data(upload(env, data(env.client.get(f"/assistant/conversations/{conversation['id']}"))))
    with env.factory() as db:
        result = service.cleanup_expired(db, now=chats.utcnow() + timedelta(days=8), company_id=env.ids.company)
        assert result["attachments_purged"] == 3
        assert business_path.exists()
        assert db.get(AssistantAttachment, fresh["id"]).status == "PURGED"
        assert db.get(AssistantAttachment, linked["id"]).status == "PURGED"
    assert env.client.get(path(fresh)).status_code == 404


def test_storage_path_and_filename_safety_and_failed_write(env, monkeypatch):
    conversation = create(env)
    row = data(upload(env, conversation, name="../../危险<script>\u202e.txt", content=b"<script>alert(1)</script>"))
    assert "/" not in row["display_name"] and "<" not in row["display_name"] and "\u202e" not in row["display_name"]
    assert data(env.client.get(path(row, "/preview")))["text"] == "<script>alert(1)</script>"
    with pytest.raises(Exception):
        files.PrivateAttachmentStorage().path("../../secret")
    def disk_full(*args, **kwargs):
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.INTERNAL_ERROR, "空间不足")
    monkeypatch.setattr(files.PrivateAttachmentStorage, "save", disk_full)
    assert upload(env, conversation).status_code == 500
    with env.factory() as db:
        assert db.scalar(select(func.count()).select_from(AssistantAttachment).where(AssistantAttachment.company_id == env.ids.company, AssistantAttachment.status == "SCANNING")) == 0
    env.settings.assistant_private_dir = str(Path(env.settings.upload_dir) / "unsafe")
    with pytest.raises(Exception):
        files.PrivateAttachmentStorage()


def test_migration_head(env):
    with env.factory() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0033"


def test_multipart_request_limit_even_without_content_length(env):
    env.settings.assistant_file_max_mb = 1
    conversation = create(env)
    content = b"x" * (1024 * 1024 + 70 * 1024)
    request = env.client.build_request("POST", "/assistant/attachments", data={"conversation_id": conversation["id"]}, files={"file": ("large.txt", content)})
    del request.headers["content-length"]
    assert env.client.send(request).status_code == 422
    assert not env.private.exists()
