"""TASK-104 定向 MySQL 验收；全部测试 DML 回滚，不调用真实模型。"""

import asyncio
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.api import assistant as legacy
from app.api.assistant_conversations import router
from app.core.config import get_settings
from app.core.data_scope import clear_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.redis import get_redis
from app.db.session import engine, get_db
from app.integrations.assistant_provider import ProviderFailure, ProviderToolCall, ProviderTurn
from app.models.approval import ApprovalFlow
from app.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.models.audit_log import AuditLog
from app.models.org import Company, Department, Staff
from app.models.party import Supplier
from app.models.purchase import PurchaseOrder
from app.models.rbac import DataScope, Role, StaffRole
from app.services import assistant_conversations as service
from app.services.assistant_conversation_stream import GuardedProvider, events, sse
from app.services.assistant_sessions import AssistantSessionRegistry

pytestmark = pytest.mark.skipif(os.environ.get("ERP_TASK104_MYSQL") != "1", reason="显式启用本机 MySQL TASK-104 验收")


class TextSession:
    usage = None

    def __init__(self):
        self.chunks = iter(["TASK104测试回答", "第二段"])
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.closed = True


class Provider:
    def __init__(self):
        self.calls, self.sessions = [], []
        self.failure = False

    async def start(self, messages):
        self.calls.append(messages)
        if self.failure:
            raise ProviderFailure(ErrorCode.AI_PROVIDER_ERROR, "test", "上游内部秘密")
        session = TextSession()
        self.sessions.append(session)
        return session


class QuotaRedis:
    def eval(self, script, numkeys, *args):
        return 1


@pytest.fixture
def ctx(monkeypatch):
    assert engine.dialect.name == "mysql"
    assert engine.url.host in ("127.0.0.1", "localhost") and engine.url.port == 3306
    assert engine.url.database == "erp"
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
    marker = "TASK104-" + uuid4().hex[:10]
    with factory() as db:
        company = Company(full_name=marker)
        foreign_company = Company(full_name=marker + "-foreign")
        db.add_all([company, foreign_company])
        db.flush()
        department = Department(company_id=company.id, name=marker)
        db.add(department)
        db.flush()
        alice = Staff(company_id=company.id, name=marker + "-alice", department_id=department.id, is_active=True)
        bob = Staff(company_id=company.id, name=marker + "-bob", is_active=True)
        outsider = Staff(company_id=foreign_company.id, name=marker + "-outsider", is_active=True)
        roles = [Role(company_id=company.id, code=code, name=marker + code) for code in ("ADMIN", "PURCHASER", "AFTER_SALE")]
        foreign_role = Role(company_id=foreign_company.id, code="ADMIN", name=marker + "foreignADMIN")
        db.add_all([alice, bob, outsider, *roles, foreign_role])
        db.flush()
        db.add_all([StaffRole(staff_id=alice.id, role_id=roles[0].id),
                    StaffRole(staff_id=bob.id, role_id=roles[0].id),
                    StaffRole(staff_id=outsider.id, role_id=foreign_role.id)])
        db.commit()
        ids = SimpleNamespace(alice=alice.id, bob=bob.id, outsider=outsider.id,
                              company=company.id, foreign_company=foreign_company.id,
                              admin_role=roles[0].id, purchaser_role=roles[1].id, after_sale_role=roles[2].id)
    provider = Provider()
    monkeypatch.setattr(legacy, "_session_registry", AssistantSessionRegistry())
    runtime_settings = get_settings().model_copy(update={"assistant_enabled": True, "assistant_provider": "deepseek", "assistant_api_key": "test-only", "assistant_model": "test-only"})
    monkeypatch.setattr(legacy, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(legacy, "get_assistant_provider", lambda *_: provider)

    def db_dependency():
        with factory() as db:
            try:
                yield db
            finally:
                clear_scope_policy(db)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(legacy.router)
    app.include_router(router)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_redis] = lambda: QuotaRedis()
    token = create_access_token(ids.alice, marker, ["ADMIN"])
    try:
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
            yield SimpleNamespace(factory=factory, client=client, provider=provider, ids=ids, token=token, marker=marker)
    finally:
        transaction.rollback()
        connection.close()
    # 另一个物理连接确认回滚后的 MySQL 中没有测试正文或账号。
    with engine.connect() as fresh:
        assert fresh.scalar(select(func.count(Company.id)).where(Company.full_name == marker)) == 0


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def create(ctx, title="TASK104会话"):
    return data(ctx.client.post("/assistant/conversations", json={"title": title, "request_id": str(uuid4())}))


def payload(ctx, conversation, content="TASK104问题"):
    session = data(ctx.client.post("/assistant/sessions"))
    return {"version": conversation["version"], "request_id": str(uuid4()), "client_message_id": str(uuid4()),
            "session_id": session["session_id"], "session_version": session["version"], "content": content}


def parse(response):
    assert response.status_code == 200, response.text
    return [(part.splitlines()[0][7:], json.loads(part.splitlines()[1][6:]))
            for part in response.text.strip().split("\n\n")]


def send(ctx, conversation, body=None):
    return parse(ctx.client.post(f"/assistant/conversations/{conversation['id']}/messages/stream", json=body or payload(ctx, conversation)))


def direct_begin(ctx, conversation, *, content="用户原始问题", client_id=None, request_id=None, page_context=None):
    with ctx.factory() as db:
        staff = db.get(Staff, ctx.ids.alice)
        return service.begin_message(db, staff, conversation["id"], version=conversation["version"],
            client_message_id=client_id or str(uuid4()), request_id=request_id or str(uuid4()), content=content,
            page_context=page_context, session_id=str(uuid4()), session_version=1, login_fingerprint="a" * 64, run_seconds=150)[0]


def finish(ctx, run, content="业务秘密回答", sources=None):
    with ctx.factory() as db:
        if sources:
            service.persist_event(db, run, "source", {"items": sources})
        service.persist_event(db, run, "delta", {"text": content})
        return service.persist_event(db, run, "done", {})


def test_mysql_migration_and_indexes(ctx):
    with ctx.factory() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) in ("0031", "0032", "0033", "0034", "0035", "0036")
    schema = inspect(engine)
    assert {index["name"] for index in schema.get_indexes("assistant_conversation")} >= {"ix_assistant_conversation_owner_status_updated"}
    assert {item["name"] for item in schema.get_unique_constraints("assistant_message")} >= {"uq_assistant_message_client", "uq_assistant_message_seq", "uq_assistant_message_request_role"}


def test_saved_before_provider_failure_and_private_errors(ctx):
    conversation = create(ctx)
    ctx.provider.failure = True
    received = send(ctx, conversation)
    assert received[0][0] == "accepted"
    assert received[-1][0] == "error" and received[-1][1]["status"] == "FAILED"
    assert "上游内部秘密" not in json.dumps(received, ensure_ascii=False)
    messages = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))["items"]
    assert messages[0]["content"] == "TASK104问题"
    assert messages[1]["status"] == "FAILED"


def test_idempotent_replay_and_changed_body_rejected(ctx):
    conversation = create(ctx)
    body = payload(ctx, conversation)
    first = send(ctx, conversation, body)
    replay = send(ctx, conversation, body)
    assert replay[0][1]["user_message_id"] == first[0][1]["user_message_id"]
    assert replay[0][1]["replayed"] is True and replay[-1][1]["finish_reason"] == "replayed"
    assert len(ctx.provider.calls) == 1
    changed = ctx.client.post(f"/assistant/conversations/{conversation['id']}/messages/stream", json={**body, "content": "另一个问题"})
    assert changed.status_code == 409


@pytest.mark.parametrize("other", ["bob", "outsider"])
def test_other_admin_and_company_cannot_read_or_mutate(ctx, other):
    conversation = create(ctx)
    token = create_access_token(getattr(ctx.ids, other), "other", ["ADMIN"])
    headers = {"Authorization": "Bearer " + token}
    base = f"/assistant/conversations/{conversation['id']}"
    assert ctx.client.get(base, headers=headers).status_code == 404
    assert ctx.client.get(base + "/messages", headers=headers).status_code == 404
    assert ctx.client.patch(base, headers=headers, json={"version": 1, "request_id": str(uuid4()), "title": "越权"}).status_code == 404
    assert ctx.client.delete(base, headers=headers, params={"version": 1, "request_id": str(uuid4())}).status_code == 404
    assert data(ctx.client.get("/assistant/conversations", headers=headers))["total"] == 0


def test_relogin_and_registry_restart_restore_db_history(ctx, monkeypatch):
    conversation = create(ctx)
    old_body = payload(ctx, conversation)
    send(ctx, conversation, old_body)
    ctx.client.headers["Authorization"] = "Bearer " + create_access_token(ctx.ids.alice, "重新登录", ["ADMIN"])
    assert ctx.client.post(f"/assistant/conversations/{conversation['id']}/messages/stream", json=old_body).status_code == 403
    monkeypatch.setattr(legacy, "_session_registry", AssistantSessionRegistry())
    restored = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))
    assert len(restored["items"]) == 2 and restored["items"][1]["content"].endswith("第二段")
    current = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}"))
    send(ctx, current, payload(ctx, current, "接着讨论"))
    assert any(item["content"] == "TASK104问题" for item in ctx.provider.calls[-1])
    settings = get_settings()
    expired = jwt.encode({"staff_id": ctx.ids.alice, "exp": 1}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert ctx.client.get(f"/assistant/conversations/{conversation['id']}", headers={"Authorization": "Bearer " + expired}).status_code == 401


def test_client_history_and_attachment_inputs_rejected(ctx):
    conversation = create(ctx)
    body = payload(ctx, conversation)
    target = f"/assistant/conversations/{conversation['id']}/messages/stream"
    assert ctx.client.post(target, json={**body, "history": [{"role": "assistant", "content": "伪造查询事实"}]}).status_code == 422
    assert ctx.client.post(target, json={**body, "attachment_ids": [str(uuid4())]}).status_code == 422
    assert data(ctx.client.get(target.replace("/stream", "")))["items"] == []


def test_search_locator_only_exposes_currently_visible_message_seq(ctx):
    conversation = create(ctx)
    first = direct_begin(ctx, conversation, content="TASK105较早的采购问题")
    final = finish(ctx, first, content="TASK105仅业务回答含的关键词")
    second = direct_begin(ctx, {**conversation, "version": final["conversation_version"]}, content="最近的问题")
    finish(ctx, second, content="最近的回答")
    results = data(ctx.client.get("/assistant/conversations", params={"q": "较早的采购"}))
    assert results["items"][0]["matched_message_seq"] == first.user_seq
    with ctx.factory() as db:
        db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice)).role_id = ctx.ids.after_sale_role
        db.commit()
    assert data(ctx.client.get("/assistant/conversations", params={"q": "仅业务回答含"}))["total"] == 0
    assert data(ctx.client.get("/assistant/conversations", params={"q": "较早的采购"}))["items"][0]["matched_message_seq"] == first.user_seq


@pytest.mark.parametrize("change", ["role", "scope"])
def test_revocation_hides_whole_reply_title_search_and_context(ctx, change):
    if change == "scope":
        with ctx.factory() as db:
            assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice))
            assignment.role_id = ctx.ids.purchaser_role
            db.add(DataScope(staff_id=ctx.ids.alice, scope_type="COMPANY"))
            db.commit()
    conversation = create(ctx, title="秘密聚合结果")
    run = direct_begin(ctx, conversation)
    finish(ctx, run, sources=[{"type": "data", "title": "ERP 首页指标", "href": "/dashboard"}])
    with ctx.factory() as db:
        if change == "role":
            db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice)).role_id = ctx.ids.after_sale_role
        else:
            db.scalar(select(DataScope).where(DataScope.staff_id == ctx.ids.alice)).scope_type = "SELF"
        db.commit()
    messages = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))["items"]
    assert messages[0]["content"] == "用户原始问题"
    assert messages[1]["hidden"] is True and messages[1]["sources"] == []
    assert messages[1]["page_context"] is None and "秘密" not in messages[1]["content"]
    assert data(ctx.client.get("/assistant/conversations", params={"q": "业务秘密"}))["total"] == 0
    assert data(ctx.client.get("/assistant/conversations", params={"q": "秘密聚合"}))["total"] == 0
    assert data(ctx.client.get("/assistant/conversations", params={"q": "用户原始"}))["total"] == 1
    current = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}"))
    send(ctx, current)
    assert all("业务秘密" not in item.get("content", "") for item in ctx.provider.calls[-1])


def test_approval_acl_change_hides_aggregate_without_tool_replay(ctx):
    conversation = create(ctx)
    run = direct_begin(ctx, conversation)
    finish(ctx, run, sources=[{"type": "data", "title": "ERP 我的审批", "href": "/approval/mine"}])
    with ctx.factory() as db:
        db.add(ApprovalFlow(company_id=ctx.ids.company, name=ctx.marker, doc_type="purchase_plan", approvers=json.dumps([ctx.ids.bob])))
        db.commit()
    page = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))
    assert page["items"][1]["hidden"] is True
    assert not ctx.provider.calls


@pytest.mark.parametrize("change", ["owner", "company"])
def test_selected_document_visibility_change_hides_reply_with_same_role(ctx, change):
    with ctx.factory() as db:
        db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice)).role_id = ctx.ids.purchaser_role
        supplier = Supplier(company_id=ctx.ids.company, code=ctx.marker, name=ctx.marker)
        db.add(supplier)
        db.flush()
        order = PurchaseOrder(company_id=ctx.ids.company, supplier_id=supplier.id, order_no=ctx.marker,
                              created_by=ctx.ids.alice)
        db.add(order)
        db.commit()
        order_id = order.id
    conversation = create(ctx)
    run = direct_begin(ctx, conversation, page_context={"path": "/purchase/orders", "filters": {},
        "selected_document": {"doc_type": "purchase_order", "doc_id": order_id}})
    finish(ctx, run, sources=[{"type": "data", "title": "ERP 采购订单", "href": "/purchase/orders"}])
    with ctx.factory() as db:
        row = db.get(PurchaseOrder, order_id)
        if change == "owner":
            row.created_by = ctx.ids.bob
        else:
            row.company_id = ctx.ids.foreign_company
        db.commit()
    reply = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))["items"][1]
    assert reply["hidden"] and not reply["sources"] and reply["page_context"] is None
    assert data(ctx.client.get("/assistant/conversations", params={"q": "业务秘密"}))["total"] == 0


def test_department_membership_change_invalidates_aggregate(ctx):
    with ctx.factory() as db:
        db.scalar(select(StaffRole).where(StaffRole.staff_id == ctx.ids.alice)).role_id = ctx.ids.purchaser_role
        department_id = db.get(Staff, ctx.ids.alice).department_id
        db.get(Staff, ctx.ids.bob).department_id = department_id
        db.add(DataScope(staff_id=ctx.ids.alice, scope_type="DEPARTMENT"))
        db.commit()
    conversation = create(ctx)
    run = direct_begin(ctx, conversation)
    finish(ctx, run)
    with ctx.factory() as db:
        db.get(Staff, ctx.ids.bob).department_id = None
        db.commit()
    assert data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))["items"][1]["hidden"]


def test_real_mysql_tool_result_and_sources_are_persisted(ctx, monkeypatch):
    class ToolProvider:
        def __init__(self):
            self.rounds = 0

        async def start_with_tools(self, messages, tools):
            self.rounds += 1
            if self.rounds == 1:
                return ProviderTurn("", (ProviderToolCall("call1", "authorized_metric", '{"metric":"today_order_count"}'),))
            result = json.loads(messages[-1]["content"])
            assert result["ok"] and result["data"]["value"] == 0
            return ProviderTurn("当前测试公司今日订单为零。")

    provider = ToolProvider()
    monkeypatch.setattr(legacy, "get_assistant_provider", lambda *_: provider)
    conversation = create(ctx)
    received = send(ctx, conversation)
    assert received[-1][0] == "done" and received[-1][1]["status"] == "COMPLETED"
    page = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))
    assert page["items"][1]["content"] == "当前测试公司今日订单为零。"
    assert any(item["type"] == "data" for item in page["items"][1]["sources"])
    assert provider.rounds == 2


@pytest.mark.parametrize("action", ["clear", "delete", "cancel"])
def test_competing_turn_and_late_delta_cannot_revive_messages(ctx, action):
    conversation = create(ctx)
    run = direct_begin(ctx, conversation)
    with ctx.factory() as db:
        staff = db.get(Staff, ctx.ids.alice)
        current = service.get_conversation(db, staff, conversation["id"])
    with pytest.raises(BizException) as conflict:
        direct_begin(ctx, current)
    assert conflict.value.code == ErrorCode.CONFLICT
    with ctx.factory() as db:
        service.persist_event(db, run, "delta", {"text": "已经收到的部分"})
        staff = db.get(Staff, ctx.ids.alice)
        if action == "cancel":
            service.cancel_message(db, staff, conversation["id"], run.assistant_id, request_id=str(uuid4()))
        else:
            service.clear_or_delete(db, staff, conversation["id"], version=run.version, request_id=str(uuid4()), deleting=action == "delete")
    with ctx.factory() as db:
        with pytest.raises(BizException):
            service.persist_event(db, run, "delta", {"text": "迟到内容不应写入"})
    with ctx.factory() as db:
        reply = db.get(AssistantMessage, run.assistant_id)
        assert reply.content == "已经收到的部分" and reply.status == "INTERRUPTED"


def test_disconnect_persists_received_partial_and_closes_upstream(ctx):
    conversation = create(ctx)
    run = direct_begin(ctx, conversation)
    closed = []

    async def upstream_frames():
        try:
            yield sse("delta", {"text": "断连前收到"})
            yield sse("delta", {"text": "不应读取"})
        finally:
            closed.append(True)

    async def start_stream(db, check):
        return StreamingResponse(upstream_frames())

    async def exercise():
        stream = events(run=run, session_factory=ctx.factory, validate_login=lambda: None, start_stream=start_stream)
        await stream.__anext__()  # accepted
        await stream.__anext__()  # first durable delta
        await stream.aclose()

    asyncio.run(exercise())
    with ctx.factory() as db:
        row = db.get(AssistantMessage, run.assistant_id)
        assert row.content == "断连前收到" and row.status == "INTERRUPTED"
    assert closed == [True]


def test_cancel_during_upstream_wait_cancels_model_task(ctx):
    conversation = create(ctx)
    run = direct_begin(ctx, conversation)
    cancelled = []

    class WaitingProvider:
        async def start(self, messages):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    def check():
        with ctx.factory() as db:
            service.assert_run(db, run)

    async def start_stream(db, _):
        await GuardedProvider(WaitingProvider(), check).start([])

    async def exercise():
        stream = events(run=run, session_factory=ctx.factory, validate_login=lambda: None, start_stream=start_stream)
        await stream.__anext__()
        pending = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0.02)
        with ctx.factory() as db:
            service.cancel_message(db, db.get(Staff, ctx.ids.alice), run.conversation_id, run.assistant_id, request_id=str(uuid4()))
        frame = await asyncio.wait_for(pending, timeout=2)
        assert b'"status": "INTERRUPTED"' in frame
        await stream.aclose()

    asyncio.run(exercise())
    assert cancelled == [True]


def test_long_history_pagination_bounded_context_and_stale_recovery(ctx):
    conversation = create(ctx)
    version = conversation["version"]
    for number in range(30):
        run = direct_begin(ctx, {**conversation, "version": version}, content=f"问题{number}" + "文" * 3900)
        final = finish(ctx, run, content="回答" + "字" * 3900)
        version = final["conversation_version"]
    first = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages", params={"limit": 50}))
    second = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages", params={"limit": 50, "before_seq": first["next_before_seq"]}))
    assert len(first["items"]) == 50 and len(second["items"]) == 10
    assert [item["seq"] for item in second["items"] + first["items"]] == list(range(1, 61))
    send(ctx, {**conversation, "version": version})
    history = ctx.provider.calls[-1][1:-1]
    assert len(history) <= get_settings().assistant_history_max_messages
    assert sum(len(item["content"]) for item in history) <= 12000
    current = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}"))
    abandoned = direct_begin(ctx, current)
    with ctx.factory() as db:
        for message_id in (abandoned.user_id, abandoned.assistant_id):
            db.get(AssistantMessage, message_id).run_until = service.utcnow() - timedelta(seconds=1)
        db.commit()
    restored = data(ctx.client.get(f"/assistant/conversations/{conversation['id']}/messages"))
    assert restored["items"][-1]["status"] == "INTERRUPTED"


def test_archive_pin_rename_clear_tombstone_cleanup_and_no_raw_audit(ctx):
    conversation = create(ctx)
    run = direct_begin(ctx, conversation, content="不会进入审计的私人正文")
    final = finish(ctx, run)
    base = f"/assistant/conversations/{conversation['id']}"
    modified = data(ctx.client.patch(base, json={"version": final["conversation_version"], "request_id": str(uuid4()), "title": "重命名", "is_pinned": True, "status": "ARCHIVED"}))
    assert modified["is_pinned"] and modified["title"] == "重命名"
    assert data(ctx.client.get("/assistant/conversations"))["total"] == 0
    assert data(ctx.client.get("/assistant/conversations", params={"status": "ARCHIVED"}))["total"] == 1
    assert ctx.client.patch(base, json={"version": 1, "request_id": str(uuid4()), "title": "过期修改"}).status_code == 409
    data(ctx.client.delete(base, params={"version": modified["version"], "request_id": str(uuid4())}))
    assert ctx.client.get(base).status_code == 404
    with ctx.factory() as db:
        future = service.utcnow() + timedelta(days=8)
        result = service.cleanup_expired(db, now=future)
        assert result["messages_purged"] >= 2
        reply = db.get(AssistantMessage, run.assistant_id)
        assert reply.content == "" and reply.sources == [] and reply.visibility_guard is None
        assert db.get(AssistantConversation, conversation["id"]).title == "已删除会话"
        audits = db.scalars(select(AuditLog.detail).where(AuditLog.actor_id == ctx.ids.alice)).all()
        assert all("私人正文" not in (detail or "") and "业务秘密" not in (detail or "") for detail in audits)
