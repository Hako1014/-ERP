"""TASK-111 MySQL acceptance; all records roll back with the shared outer transaction."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api import workbench
from app.models.announcement import Announcement
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.org import Department, Staff
from app.models.party import Supplier
from app.models.purchase import PurchasePlan
from app.models.rbac import StaffRole
from tests.test_assistant_conversations_mysql import ctx

pytestmark = pytest.mark.skipif(
    os.environ.get("ERP_TASK104_MYSQL") != "1",
    reason="仅显式启用本机 MySQL TASK-111 验收",
)


@pytest.fixture
def workbench_ctx(ctx):
    ctx.client.app.include_router(workbench.router)
    return ctx


def _data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_announcements_filter_company_audience_status_and_dates(workbench_ctx):
    ctx = workbench_ctx
    now = _now()
    published_at = now - timedelta(minutes=1)
    with ctx.factory() as db:
        alice = db.get(Staff, ctx.ids.alice)
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == alice.id))
        assignment.role_id = ctx.ids.purchaser_role
        other_department = Department(company_id=ctx.ids.company, name=ctx.marker + "-other")
        db.add(other_department)
        db.flush()
        alice_department = alice.department_id

        rows = [
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-all", content="common", status="PUBLISHED", published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-role", content="role scoped", status="PUBLISHED", audience_roles=["PURCHASER"], published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-department", content="dept scoped", status="PUBLISHED", audience_department_ids=[alice_department], published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-wrong-role", content="hidden role", status="PUBLISHED", audience_roles=["FINANCE"], published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-wrong-dept", content="hidden dept", status="PUBLISHED", audience_department_ids=[other_department.id], published_at=published_at),
            Announcement(company_id=ctx.ids.foreign_company, title=ctx.marker + "-foreign", content="foreign", status="PUBLISHED", published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-draft", content="draft", status="DRAFT", published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-expired", content="expired", status="PUBLISHED", expires_at=now - timedelta(seconds=1), published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-future", content="future", status="PUBLISHED", starts_at=now + timedelta(days=1), published_at=published_at),
            Announcement(company_id=ctx.ids.company, title=ctx.marker + "-unpublished", content="unpublished", status="PUBLISHED"),
        ]
        db.add_all(rows)
        db.commit()

    page = _data(ctx.client.get("/announcements", params={"company_id": ctx.ids.foreign_company, "page_size": 50}))
    assert page["total"] == 3
    assert {row["title"] for row in page["items"]} == {
        ctx.marker + "-all",
        ctx.marker + "-role",
        ctx.marker + "-department",
    }
    assert all(set(row) == {"id", "title", "preview", "published_at", "expires_at"} for row in page["items"])

    role_item = next(row for row in page["items"] if row["title"] == ctx.marker + "-role")
    detail = _data(ctx.client.get(f"/announcements/{role_item['id']}"))
    assert detail["content"] == "role scoped"
    hidden = ctx.client.get(f"/announcements/{rows[3].id}")
    assert hidden.status_code == 404


def test_todos_reuse_current_approver_validation_and_role_scope(workbench_ctx):
    ctx = workbench_ctx
    with ctx.factory() as db:
        alice = db.get(Staff, ctx.ids.alice)
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == alice.id))
        assignment.role_id = ctx.ids.admin_role
        supplier = Supplier(
            company_id=ctx.ids.company,
            code=ctx.marker + "-supplier",
            name=ctx.marker + " supplier",
            is_active=True,
        )
        db.add(supplier)
        db.flush()
        plan = PurchasePlan(
            company_id=ctx.ids.company,
            plan_no=ctx.marker + "-PLAN",
            supplier_id=supplier.id,
            created_by=alice.id,
            status="IN_PROGRESS",
            audit_status="PENDING",
        )
        db.add(plan)
        db.flush()
        flow = ApprovalFlow(
            company_id=ctx.ids.company,
            name=ctx.marker + "-approval-flow",
            doc_type="purchase_plan",
            approvers=json.dumps([{"step": 1, "name": "当前环节", "approver_ids": [alice.id]}]),
            created_by=alice.id,
        )
        db.add(flow)
        db.flush()
        db.add(ApprovalRecord(
            company_id=ctx.ids.company,
            flow_id=flow.id,
            doc_type="purchase_plan",
            doc_id=plan.id,
            doc_no=plan.plan_no,
            step=1,
            approver_id=alice.id,
            action="APPROVE",
            status="PENDING",
        ))
        db.commit()
        plan_id, flow_id = plan.id, flow.id

    result = _data(ctx.client.get("/workbench/todos"))
    assert result["available"] is True and result["total"] == 1
    item = result["items"][0]
    assert item["doc_id"] == plan_id and item["doc_no"] == ctx.marker + "-PLAN"
    assert item["target"] == {
        "path": "/approval/mine",
        "query": {"notificationDocType": "purchase_plan", "notificationDocId": str(plan_id)},
    }
    assert not ({"amount", "total_amount", "business_status", "audit_status"} & set(item))

    with ctx.factory() as db:
        alice = db.get(Staff, ctx.ids.alice)
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == alice.id))
        assignment.role_id = ctx.ids.purchaser_role
        db.get(ApprovalFlow, flow_id).approvers = json.dumps([ctx.ids.bob])
        db.commit()
    no_approval_action = _data(ctx.client.get("/workbench/todos"))
    assert no_approval_action["available"] is False and no_approval_action["total"] == 0


def test_help_filters_page_knowledge_by_live_role_permissions(workbench_ctx):
    ctx = workbench_ctx
    with ctx.factory() as db:
        alice = db.get(Staff, ctx.ids.alice)
        assignment = db.scalar(select(StaffRole).where(StaffRole.staff_id == alice.id))
        assignment.role_id = ctx.ids.purchaser_role
        db.commit()

    purchase = _data(ctx.client.get("/workbench/help", params={"route_key": "/purchase/plans"}))
    assert purchase["title"]
    assert "采购计划" in purchase["guidance"] or "采购" in purchase["guidance"]
    assert purchase["assistant_available"] is True
    assert ctx.client.get("/workbench/help", params={"route_key": "/finance/expenses"}).status_code == 403

    generic = _data(ctx.client.get("/workbench/help"))
    assert generic["title"] == "帮助中心" and generic["faq"]
    assert generic["sources"] == []
