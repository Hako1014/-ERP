import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import notification as notification_api
from app.api.deps import get_current_staff
from app.core.data_scope import clear_scope_policy
from app.core.security import create_access_token
from app.db.base import Base
from app.db.redis import get_redis
from app.db.session import get_db
from app.main import app
from app.models.approval import ApprovalRecord
from app.models.notification import NotificationInbox
from app.models.org import Company, Staff
from app.models.purchase import PurchasePlan
from app.models.rbac import Role, StaffRole
from app.services import approval_service, notification_service
from app.services.notification_query_service import scan_stream_batch


@pytest.fixture
def message_env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as db:
        db.add(Company(id=1, full_name="消息接口测试公司"))
        staff = [
            Staff(id=1, company_id=1, name="制单人"),
            Staff(id=2, company_id=1, name="审批人"),
            Staff(id=3, company_id=1, name="其他员工"),
        ]
        roles = [
            Role(id=1, company_id=1, code="PURCHASER", name="采购员"),
            Role(id=2, company_id=1, code="VP", name="副总经理"),
            Role(id=3, company_id=1, code="AFTER_SALE", name="售后"),
        ]
        db.add_all(staff + roles)
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
                StaffRole(staff_id=3, role_id=3),
            ]
        )
        db.add(
            PurchasePlan(
                id=100,
                plan_no="CGJH-API-100",
                supplier_id=900,
                created_by=1,
            )
        )
        db.commit()
        approval_service.create_flow(
            db,
            name="消息接口审批流程",
            doc_type="purchase_plan",
            approvers=[{"step": 1, "name": "审批", "approver_ids": [2]}],
            actor=staff[0],
        )
        approval_service.submit(
            db,
            doc_type="purchase_plan",
            doc_id=100,
            staff=staff[0],
            doc_no="CGJH-API-100",
        )

    notification_service.process_outbox_batch(session_factory=factory)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            clear_scope_policy(session)
            session.close()

    with factory() as db:
        seeded_staff = db.get(Staff, 2)
        current_staff = {"value": seeded_staff}

    class FakePubSub:
        def __init__(self):
            self.channels = []
            self.closed = False

        def subscribe(self, channel):
            self.channels.append(channel)

        def get_message(self, **_kwargs):
            return None

        def close(self):
            self.closed = True

    class FakeRedis:
        def __init__(self):
            self.pubsub_client = FakePubSub()

        def pubsub(self):
            return self.pubsub_client

    fake_redis = FakeRedis()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_staff] = lambda: current_staff["value"]
    app.dependency_overrides[get_redis] = lambda: fake_redis
    monkeypatch.setattr(notification_api, "SessionLocal", factory)
    try:
        yield factory, current_staff, fake_redis
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_list_read_and_unread_count_are_shared_between_clients(message_env):
    _factory, _current_staff, _fake_redis = message_env
    with TestClient(app) as first_client, TestClient(app) as second_client:
        listed = first_client.get("/notifications").json()["data"]
        assert len(listed["items"]) == 1
        notification_id = listed["items"][0]["id"]
        assert first_client.get("/notifications/unread-count").json()["data"] == {
            "count": 1
        }

        read = first_client.post(f"/notifications/{notification_id}/read")
        assert read.status_code == 200
        assert read.json()["data"]["read_state"] == "READ"
        assert second_client.get("/notifications/unread-count").json()["data"] == {
            "count": 0
        }


def test_notification_routes_require_bearer_authentication():
    with TestClient(app) as client:
        assert client.get("/notifications").status_code == 401
        assert client.get("/notifications/unread-count").status_code == 401
        assert client.get("/notifications/events").status_code == 401
        assert client.get("/notifications/1").status_code == 401
        assert client.post("/notifications/1/read").status_code == 401
        assert client.post("/notifications/read-all").status_code == 401


def test_read_all_updates_only_current_recipient(message_env):
    factory, current_staff, _fake_redis = message_env
    with TestClient(app) as client:
        result = client.post("/notifications/read-all").json()["data"]
        assert result["updated_count"] == 1
        with factory() as db:
            current_staff["value"] = db.get(Staff, 1)
        assert client.get("/notifications/unread-count").json()["data"]["count"] == 0
        with factory() as db:
            inbox = db.scalar(select(NotificationInbox))
            assert inbox.recipient_staff_id == 2
            assert inbox.read_state == "READ"


def test_other_users_message_id_and_cursor_are_rejected(message_env):
    factory, current_staff, _fake_redis = message_env
    with factory() as db:
        inbox = db.scalar(select(NotificationInbox))
        notification_id = inbox.id
        cursor = inbox.id
    with TestClient(app) as client:
        with factory() as db:
            current_staff["value"] = db.get(Staff, 1)
        assert client.get(f"/notifications/{notification_id}").status_code == 404
        assert client.post(f"/notifications/{notification_id}/read").status_code == 404
        assert client.get(f"/notifications?cursor={cursor}").status_code == 400


def test_sse_replays_persisted_notification_without_sensitive_body(message_env):
    factory, current_staff, fake_redis = message_env
    staff = current_staff["value"]
    token = create_access_token(staff.id, staff.name, ["VP"])
    db = factory()
    response = notification_api.notification_events(
        last_event_id=None,
        credentials=HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=token
        ),
        staff=staff,
        db=db,
        redis=fake_redis,
    )

    async def read_first_notification():
        stream = response.body_iterator
        body = (await anext(stream)).encode("utf-8")
        while b"event: notification" not in body:
            body += (await anext(stream)).encode("utf-8")
        await stream.aclose()
        return body

    body = asyncio.run(read_first_notification())
    assert response.headers["x-accel-buffering"] == "no"
    assert b"event: notification" in body
    assert b'"inbox_id":1' in body
    assert b'"category":"approval"' in body
    assert b"CGJH-API-100" not in body
    assert "已提交审批".encode("utf-8") not in body
    assert token.encode("ascii") not in body
    with factory() as db:
        stream_staff = db.get(Staff, 2)
        visible, scanned_to, has_more = scan_stream_batch(
            db, stream_staff, after_id=1
        )
        assert visible == []
        assert scanned_to == 1
        assert not has_more
    assert fake_redis.pubsub_client.channels == ["erp:notifications:1:2"]
    assert fake_redis.pubsub_client.closed


def test_revoked_document_permission_hides_list_count_and_detail(message_env):
    factory, current_staff, _fake_redis = message_env
    with factory() as db:
        inbox = db.scalar(select(NotificationInbox))
        notification_id = inbox.id
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == 2))
        db.delete(assignment)
        db.commit()
    with TestClient(app) as client:
        assert client.get("/notifications").json()["data"]["items"] == []
        assert client.get("/notifications/unread-count").json()["data"]["count"] == 0
        assert client.get(f"/notifications/{notification_id}").status_code == 404


def test_stale_approval_task_is_not_exposed(message_env):
    factory, _current_staff, _fake_redis = message_env
    with factory() as db:
        pending = db.scalar(
            select(ApprovalRecord).where(ApprovalRecord.status == "PENDING")
        )
        pending.status = "APPROVED"
        notification_id = db.scalar(select(NotificationInbox.id))
        db.commit()
    with TestClient(app) as client:
        assert client.get("/notifications").json()["data"]["items"] == []
        assert client.get(f"/notifications/{notification_id}").status_code == 404


def test_sse_uses_authorization_header_and_closes_on_token_expiry(
    message_env, monkeypatch
):
    _factory, current_staff, fake_redis = message_env
    staff = current_staff["value"]
    token = create_access_token(staff.id, staff.name, ["VP"])
    monkeypatch.setattr(
        notification_api,
        "utc_now",
        lambda: datetime.now(timezone.utc) + timedelta(days=1),
    )
    with TestClient(app) as client:
        with client.stream(
            "GET", "/notifications/events", headers={"Authorization": f"Bearer {token}"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = b"".join(response.iter_raw())
            assert b"retry: 5000" in body
            assert b"auth_expired" in body
    assert fake_redis.pubsub_client.channels == ["erp:notifications:1:2"]
    assert fake_redis.pubsub_client.closed


def test_sse_proxy_disables_buffering():
    from pathlib import Path

    config = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"
    content = config.read_text(encoding="utf-8")
    sse_location = content.split("location = /api/notifications/events", 1)[1].split(
        "location /api/", 1
    )[0]
    assert "proxy_buffering off" in sse_location
    assert "proxy_read_timeout 1h" in sse_location
    assert "proxy_cache off" in sse_location
