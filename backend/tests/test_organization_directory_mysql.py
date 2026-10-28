"""TASK-110 本机 MySQL 目录验收；全部测试记录由共享外层事务回滚。"""

import io
import os
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.api import company as company_api
from app.api import organization as organization_api
from app.core.permissions import ROLE_PERMISSIONS
from app.models.org import Department, Position, Staff
from app.models.rbac import Role, StaffRole
from app.services import assistant_attachment_files as private_files
from app.services.assistant_attachment_files import PrivateAttachmentStorage
from tests.test_assistant_conversations_mysql import ctx

pytestmark = pytest.mark.skipif(os.environ.get("ERP_TASK104_MYSQL") != "1", reason="仅显式启用本机 MySQL TASK-110 验收")


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (14, 11), "#22c55e").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def directory_ctx(ctx, monkeypatch, tmp_path):
    settings = private_files.get_settings().model_copy(update={
        "assistant_private_dir": str(tmp_path / "private"),
        "upload_dir": str(tmp_path / "public"),
    })
    monkeypatch.setattr(private_files, "get_settings", lambda: settings)
    ctx.client.app.include_router(organization_api.router)
    ctx.client.app.include_router(company_api.router)
    with ctx.factory() as db:
        alice = db.get(Staff, ctx.ids.alice)
        bob = db.get(Staff, ctx.ids.bob)
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == alice.id))
        assignment.role_id = ctx.ids.purchaser_role

        foreign_department = Department(company_id=ctx.ids.foreign_company, name=ctx.marker + "-foreign-dept")
        department_a = Department(company_id=ctx.ids.company, name=ctx.marker + "-department-a")
        department_b = Department(company_id=ctx.ids.company, name=ctx.marker + "-department-b")
        position = Position(company_id=ctx.ids.company, name=ctx.marker + "-position")
        db.add_all([foreign_department, department_a, department_b, position])
        db.flush()
        # 模拟历史脏关系：目录不能借 staff.department_id 透出外公司部门。
        department_a.parent_id = foreign_department.id
        alice.department_id = department_a.id
        bob.department_id = foreign_department.id
        bob.login_account = ctx.marker + "-private-login"
        bob.phone = "13900001111"
        bob.remark = "directory-private-remark"
        bob.password_hash = "directory-private-hash"

        colleague = Staff(
            company_id=ctx.ids.company,
            name=ctx.marker + "-colleague",
            staff_no=ctx.marker + "-NO",
            login_account=ctx.marker + "-account",
            phone="13700002222",
            password_hash="directory-private-hash",
            remark="directory-private-remark",
            department_id=department_a.id,
            position_id=position.id,
            is_active=True,
        )
        inactive = Staff(
            company_id=ctx.ids.company,
            name=ctx.marker + "-inactive",
            department_id=department_b.id,
            is_active=False,
        )
        db.add_all([colleague, inactive])
        db.flush()
        avatar = PrivateAttachmentStorage().save(_png_bytes())
        colleague.avatar_storage_key = avatar
        colleague.avatar_media_type = "image/png"
        colleague.avatar_size = len(_png_bytes())
        colleague.avatar_sha256 = "a" * 64
        db.commit()
        ctx.directory_ids = {
            "department_a": department_a.id,
            "department_b": department_b.id,
            "foreign_department": foreign_department.id,
            "colleague": colleague.id,
            "inactive": inactive.id,
        }
    ctx.private = tmp_path / "private"
    try:
        yield ctx
    finally:
        if ctx.private.exists():
            for target in ctx.private.iterdir():
                target.unlink()


def _data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_seven_roles_get_same_company_directory_without_management_grants(directory_ctx):
    ctx = directory_ctx
    role_codes = ("ADMIN", "VP", "FINANCE", "PURCHASER", "WAREHOUSE", "QUALITY", "AFTER_SALE")
    with ctx.factory() as db:
        role_ids = {}
        for code in role_codes:
            role = db.scalar(select(Role).where(Role.company_id == ctx.ids.company, Role.code == code))
            if role is None:
                role = Role(company_id=ctx.ids.company, code=code, name=ctx.marker + "-" + code)
                db.add(role)
                db.flush()
            role_ids[code] = role.id
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice))

        for code in role_codes:
            assignment.role_id = role_ids[code]
            db.commit()
            assert "organization:read" in ROLE_PERMISSIONS[code]
            page = _data(ctx.client.get("/organization/directory", params={"page_size": 1}))
            assert page["total"] == 3 and len(page["items"]) == 1
            management = ctx.client.get("/staff")
            assert management.status_code == (200 if code == "ADMIN" else 403)

        # 去掉目录角色后，前端隐藏按钮也不能替代服务端授权。
        db.delete(assignment)
        db.commit()
        assert ctx.client.get("/organization/directory").status_code == 403


def test_directory_company_filter_search_pagination_details_and_avatar_are_safe(directory_ctx):
    ctx = directory_ctx
    departments = _data(ctx.client.get("/organization/departments?company_id=" + str(ctx.ids.foreign_company)))
    listed_department_ids = {row["id"] for row in departments["items"]}
    assert {ctx.directory_ids["department_a"], ctx.directory_ids["department_b"]} <= listed_department_ids
    assert ctx.directory_ids["foreign_department"] not in listed_department_ids
    assert next(row for row in departments["items"] if row["id"] == ctx.directory_ids["department_a"])["parent_id"] is None

    first = _data(ctx.client.get("/organization/directory", params={
        "company_id": ctx.ids.foreign_company, "page": 1, "page_size": 2,
    }))
    second = _data(ctx.client.get("/organization/directory", params={"page": 2, "page_size": 2}))
    assert first["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert {row["staff_id"] for row in first["items"] + second["items"]} == {
        ctx.ids.alice, ctx.ids.bob, ctx.directory_ids["colleague"],
    }
    assert ctx.directory_ids["inactive"] not in {row["staff_id"] for row in first["items"] + second["items"]}

    safe_fields = {
        "staff_id", "name", "department_id", "department_name",
        "position_id", "position_name", "avatar_available",
    }
    for person in first["items"] + second["items"]:
        assert set(person) == safe_fields
    bob = next(row for row in first["items"] + second["items"] if row["staff_id"] == ctx.ids.bob)
    assert bob["department_id"] is None and bob["department_name"] is None
    serialized = str(first) + str(second)
    for private_value in ("private-login", "13900001111", "directory-private-remark", "directory-private-hash"):
        assert private_value not in serialized

    matches = _data(ctx.client.get("/organization/directory", params={"q": "colleague", "department_id": ctx.directory_ids["department_a"]}))
    assert matches["total"] == 1 and matches["items"][0]["staff_id"] == ctx.directory_ids["colleague"]
    department_matches = _data(ctx.client.get("/organization/directory", params={"q": ctx.marker + "-department-a"}))
    assert department_matches["total"] == 2
    assert ctx.client.get("/organization/directory", params={"department_id": ctx.directory_ids["foreign_department"]}).status_code == 404

    colleague_id = ctx.directory_ids["colleague"]
    detail = _data(ctx.client.get(f"/organization/directory/{colleague_id}"))
    assert detail["name"].endswith("-colleague") and detail["avatar_available"] is True
    assert set(detail) == safe_fields
    assert ctx.client.get(f"/organization/directory/{ctx.ids.outsider}").status_code == 404
    assert ctx.client.get(f"/organization/directory/{ctx.directory_ids['inactive']}").status_code == 404

    avatar = ctx.client.get(f"/organization/directory/{colleague_id}/avatar")
    assert avatar.status_code == 200 and avatar.headers["cache-control"] == "private, no-store"
    assert avatar.headers["x-content-type-options"] == "nosniff"
    outsider = ctx.client.get(f"/organization/directory/{ctx.ids.outsider}/avatar")
    assert outsider.status_code == 404
