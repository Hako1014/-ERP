"""TASK-113 定向 MySQL API 验收；所有测试写入位于外层事务并最终回滚。"""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api import quick_entry
from app.api.deps import get_current_staff
from app.core.data_scope import clear_scope_policy
from app.core.handlers import register_exception_handlers
from app.db.session import engine, get_db
from app.models.org import Company, Staff
from app.models.purchase import PurchasePlan
from app.models.quick_entry_draft import QuickEntryDraft

pytestmark = pytest.mark.skipif(
    os.environ.get("ERP_TASK113_MYSQL") != "1",
    reason="显式启用本机 MySQL TASK-113 验收",
)


@pytest.fixture
def ctx(monkeypatch):
    assert engine.dialect.name == "mysql"
    assert engine.url.host in ("127.0.0.1", "localhost") and engine.url.port == 3306
    assert engine.url.database == "erp"

    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    marker = "TASK113-" + uuid4().hex[:10]
    with factory() as db:
        company = Company(full_name=marker)
        db.add(company)
        db.flush()
        owner = Staff(company_id=company.id, name=marker + "-owner", is_active=True)
        peer = Staff(company_id=company.id, name=marker + "-peer", is_active=True)
        db.add_all([owner, peer])
        db.commit()

    actor = {"staff": owner}
    monkeypatch.setattr(
        quick_entry,
        "current_actor_access",
        lambda _db, _staff_id: (
            actor["staff"],
            frozenset(),
            frozenset({"module:purchase", "purchase:create"}),
        ),
    )

    def db_dependency():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(quick_entry.router)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_staff] = lambda: actor["staff"]
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, factory=factory, actor=actor, owner=owner, peer=peer, marker=marker)
    finally:
        app.dependency_overrides.clear()
        transaction.rollback()
        connection.close()

    with engine.connect() as fresh:
        assert fresh.scalar(select(func.count(Company.id)).where(Company.full_name == marker)) == 0
        assert fresh.scalar(select(func.count(QuickEntryDraft.id)).where(QuickEntryDraft.title.like(marker + "%"))) == 0


def test_mysql_private_draft_crud_permissions_and_idempotent_import(ctx):
    client = ctx.client
    catalog = client.get("/quick-entry/catalog")
    assert catalog.status_code == 200
    assert [item["document_type"] for item in catalog.json()["data"]] == ["purchase_plan"]

    request_id = str(uuid4())
    create = client.post(
        "/quick-entry/drafts",
        json={
            "request_id": request_id,
            "document_type": "purchase_plan",
            "source_mode": "table",
            "title": ctx.marker + " 采购计划",
            "input_text": "内容只在私人草稿中",
            "draft_data": {"items": [{"product_name": "样例商品", "quantity": "1"}]},
        },
    )
    assert create.status_code == 200, create.text
    draft = create.json()["data"]
    assert draft["version"] == 1
    duplicate = client.post(
        "/quick-entry/drafts",
        json={
            "request_id": request_id,
            "document_type": "purchase_plan",
            "source_mode": "table",
            "title": ctx.marker + " 采购计划",
            "input_text": "内容只在私人草稿中",
            "draft_data": {"items": [{"product_name": "样例商品", "quantity": "1"}]},
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["draft_id"] == draft["draft_id"]

    # 同公司另一员工仍不可读取或枚举草稿。
    ctx.actor["staff"] = ctx.peer
    assert client.get(f"/quick-entry/drafts/{draft['draft_id']}").status_code == 404
    assert client.get("/quick-entry/drafts").json()["data"] == []
    ctx.actor["staff"] = ctx.owner

    update = client.put(
        f"/quick-entry/drafts/{draft['draft_id']}",
        json={
            "draft_version": 1,
            "title": ctx.marker + " 已编辑",
            "input_text": "已核对",
            "source_mode": "table",
            "draft_data": {"items": [{"product_name": "样例商品", "quantity": "2"}]},
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["version"] == 2

    confirm_path = f"/quick-entry/drafts/{draft['draft_id']}/confirm-import"
    first = client.post(confirm_path, json={"draft_version": 2})
    second = client.post(confirm_path, json={"draft_version": 2})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["status"] == "APPLIED"
    assert second.json()["data"]["applied_version"] == 2
    with ctx.factory() as db:
        assert db.scalar(select(func.count(PurchasePlan.id)).where(PurchasePlan.company_id == ctx.owner.company_id)) == 0

    deleted = client.delete(f"/quick-entry/drafts/{draft['draft_id']}")
    assert deleted.status_code == 200
    assert client.get(f"/quick-entry/drafts/{draft['draft_id']}").status_code == 404
