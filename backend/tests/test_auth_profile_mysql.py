"""TASK-109 本机 MySQL 验收；所有员工/角色写入均由共享外层事务回滚。"""

import io
import os
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.api import auth as auth_api
from app.core.security import create_access_token
from app.models.org import Position, Staff
from app.models.rbac import Role, StaffRole
from app.services import assistant_attachment_files as private_files
from tests.test_assistant_conversations_mysql import ctx, data  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("ERP_TASK104_MYSQL") != "1", reason="仅显式启用本机 MySQL TASK-109 验收")


@pytest.fixture
def profile_ctx(ctx, monkeypatch, tmp_path):
    settings = auth_api.get_settings().model_copy(update={
        "assistant_private_dir": str(tmp_path / "private"),
        "upload_dir": str(tmp_path / "public"),
    })
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)
    monkeypatch.setattr(private_files, "get_settings", lambda: settings)
    ctx.client.app.include_router(auth_api.router)
    with ctx.factory() as db:
        staff = db.get(Staff, ctx.ids.alice)
        staff.staff_no = ctx.marker + "-NO"
        staff.login_account = ctx.marker + "-login"
        staff.phone = "13800001234"
        staff.password_hash = "must-never-be-returned"
        staff.remark = "must-never-be-returned-either"
        position = Position(company_id=ctx.ids.company, name=ctx.marker + "-position")
        db.add(position)
        db.flush()
        staff.position_id = position.id
        db.commit()
    ctx.private = tmp_path / "private"
    try:
        yield ctx
    finally:
        if ctx.private.exists():
            for target in ctx.private.iterdir():
                target.unlink()


def _png_bytes() -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (16, 12), "#3b82f6")
    exif = image.getexif()
    exif[270] = "private metadata must be stripped"
    image.save(output, format="PNG", exif=exif)
    return output.getvalue()


def test_profile_is_self_only_whitelisted_and_masks_private_fields(profile_ctx):
    ctx = profile_ctx
    response = ctx.client.get("/auth/me/profile")
    profile = data(response)
    assert profile["staff_id"] == ctx.ids.alice
    assert profile["name"] == ctx.marker + "-alice"
    assert profile["phone_masked"] == "138****1234"
    serialized = response.text
    for secret in ("must-never-be-returned", "password_hash", "avatar_storage_key", "13800001234"):
        assert secret not in serialized

    changed = data(ctx.client.put("/auth/me/profile", json={
        "display_name": "本人显示名", "gender": "其他", "region": "杭州",
    }))
    assert changed["display_name"] == "本人显示名"
    assert changed["name"] == ctx.marker + "-alice"  # 正式姓名保持不变
    assert ctx.client.get(f"/auth/me/profile?staff_id={ctx.ids.bob}").json()["data"]["staff_id"] == ctx.ids.alice

    for forbidden in (
        {"staff_id": ctx.ids.bob}, {"company_id": ctx.ids.foreign_company},
        {"roles": ["ADMIN"]}, {"is_active": False}, {"phone": "13911112222"},
        {"login_account": "attacker"}, {"password_hash": "attacker"},
    ):
        assert ctx.client.put("/auth/me/profile", json=forbidden).status_code == 422

    with ctx.factory() as db:
        staff = db.get(Staff, ctx.ids.alice)
        assert staff.name == ctx.marker + "-alice"
        assert staff.phone == "13800001234"
        assert staff.company_id == ctx.ids.company
        assert staff.display_name == "本人显示名" and staff.gender == "其他" and staff.region == "杭州"
    assert data(ctx.client.get("/auth/me/profile"))["display_name"] == "本人显示名"


def test_profile_available_to_all_seven_roles(profile_ctx):
    ctx = profile_ctx
    codes = ("ADMIN", "VP", "FINANCE", "PURCHASER", "WAREHOUSE", "QUALITY", "AFTER_SALE")
    with ctx.factory() as db:
        role_by_code = {}
        for code in codes:
            role = db.scalar(select(Role).where(Role.company_id == ctx.ids.company, Role.code == code))
            if role is None:
                role = Role(company_id=ctx.ids.company, code=code, name=ctx.marker + "-" + code)
                db.add(role)
                db.flush()
            role_by_code[code] = role.id
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice))
        for code in codes:
            assignment.role_id = role_by_code[code]
            db.commit()
            profile = data(ctx.client.get("/auth/me/profile"))
            assert profile["roles"] == [code]
            assert profile["staff_id"] == ctx.ids.alice


def test_avatar_validates_sanitizes_and_is_private_to_authenticated_owner(profile_ctx):
    ctx = profile_ctx
    image = _png_bytes()
    uploaded = ctx.client.post("/auth/me/avatar", files={"file": ("avatar.png", image, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    profile = uploaded.json()["data"]
    assert profile["avatar_version"]
    assert "avatar_storage_key" not in uploaded.text

    avatar = ctx.client.get("/auth/me/avatar")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    assert avatar.headers["cache-control"] == "private, no-store"
    assert avatar.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(avatar.content)) as clean:
        assert clean.getexif().get(270) is None

    invalid_extension = ctx.client.post("/auth/me/avatar", files={"file": ("avatar.txt", image, "image/png")})
    invalid_content = ctx.client.post("/auth/me/avatar", files={"file": ("avatar.jpg", image, "image/jpeg")})
    too_large = ctx.client.post("/auth/me/avatar", files={"file": ("avatar.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")})
    assert invalid_extension.status_code == invalid_content.status_code == too_large.status_code == 422

    outsider = create_access_token(ctx.ids.outsider, "其他公司用户", ["ADMIN"])
    assert ctx.client.get("/auth/me/avatar", headers={"Authorization": "Bearer " + outsider}).status_code == 404
    removed = data(ctx.client.delete("/auth/me/avatar"))
    assert removed["avatar_version"] is None
    assert ctx.client.get("/auth/me/avatar").status_code == 404
