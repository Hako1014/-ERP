"""TASK094 权限范围与后台公司范围回归（隔离 SQLite）。"""

import pytest
from sqlalchemy import select

from app.models.notification import NotificationOutbox
from app.models.org import Company, Department, Staff
from app.models.purchase import PurchasePlan
from app.models.rbac import DataScope, Role
from app.services import notification_service as notices
from tests.test_notification_api import message_env  # noqa: F401
from tests.test_notification_service import session_factory, _seed, _submit  # noqa: F401
from fastapi.testclient import TestClient
from app.main import app


@pytest.mark.parametrize("role,visible", [
    ("ADMIN", True), ("VP", True), ("FINANCE", True), ("QUALITY", True),
    ("PURCHASER", False), ("WAREHOUSE", False), ("AFTER_SALE", False),
])
def test_seven_roles_recheck_current_permissions(message_env, role, visible):
    factory, _staff, _redis = message_env
    with factory() as db:
        for existing in db.scalars(select(Role).where(Role.code == role, Role.id != 2)):
            existing.code = "TASK094_OTHER_ROLE"
        db.flush()
        db.get(Role, 2).code = role
        db.commit()
    with TestClient(app) as client:
        assert bool(client.get("/notifications").json()["data"]["items"]) is visible
        assert bool(client.get("/notifications/unread-count").json()["data"]["count"]) is visible
        assert client.get("/notifications/1").status_code == (200 if visible else 404)
        assert client.post("/notifications/1/read").status_code == (200 if visible else 404)


@pytest.mark.parametrize("scope,owner,visible", [
    ("SELF", 2, True), ("SELF", 1, False), ("DEPARTMENT", 1, True),
    ("DEPARTMENT", 3, False), ("COMPANY", 3, True), ("COMPANY", 4, False),
])
def test_owner_scopes_do_not_expand_across_company(session_factory, scope, owner, visible):
    _seed(session_factory)
    with session_factory() as db:
        db.add(Company(id=2, full_name="另一个公司"))
        db.add_all([Department(id=1, company_id=1, name="部门甲"),
                    Department(id=2, company_id=1, name="部门乙")])
        db.flush()
        db.get(Staff, 1).department_id = 1
        db.get(Staff, 2).department_id = 1
        db.get(Staff, 3).department_id = 2
        db.add(Staff(id=4, company_id=2, name="其他公司制单人"))
        db.add(DataScope(staff_id=2, scope_type=scope, department_id=1))
        db.add(PurchasePlan(id=200, plan_no="SCOPE-200", supplier_id=900, created_by=owner))
        db.commit()
        assert notices._recipient_can_view(db, staff_id=2, company_id=1,
            doc_type="purchase_plan", doc_id=200, require_approval=False) is visible


def test_company_scoped_worker_does_not_modify_other_outbox(session_factory):
    _seed(session_factory)
    _submit(session_factory)
    with session_factory() as db:
        source = db.scalar(select(NotificationOutbox))
        source.attempt_count = notices.MAX_ATTEMPTS
        db.commit()
    assert notices.process_outbox_batch(session_factory=session_factory, company_id=2)["claimed"] == 0
    with session_factory() as db:
        source = db.scalar(select(NotificationOutbox))
        assert source.status == "PENDING" and source.last_error_code is None
    notices.process_outbox_batch(session_factory=session_factory, company_id=1)
    with session_factory() as db:
        assert db.scalar(select(NotificationOutbox)).status == "FAILED"


@pytest.mark.parametrize("company_id", [0, -1, True])
def test_company_filter_rejects_invalid_context(company_id):
    with pytest.raises(ValueError):
        notices.process_outbox_batch(company_id=company_id)
    with pytest.raises(ValueError):
        notices.dispatch_realtime_batch(company_id=company_id)


def test_notification_timestamps_use_application_utc(session_factory, monkeypatch):
    from datetime import datetime
    from app.models import notification as model
    frozen = datetime(2026, 10, 10, 9, 0, 0)
    monkeypatch.setattr(model, "_utc_timestamp", lambda: frozen)
    _seed(session_factory)
    _submit(session_factory)
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.created_at == frozen and event.updated_at == frozen
