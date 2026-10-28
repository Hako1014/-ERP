"""Redis 中断/恢复验收；只提交 manifest 的未使用测试计划。"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.org import Company
from app.models.approval import ApprovalRecord
from app.models.notification import NotificationOutbox, NotificationInbox


def run(manifest: dict, phase: str) -> dict:
    plan = manifest["plans"]["revoke"]
    with SessionLocal() as db:
        company = db.get(Company, manifest["company_id"])
        if not company or company.short_name != manifest["marker"] or not plan["no"].startswith(manifest["marker"]):
            raise RuntimeError("测试对象护栏校验失败")
    if phase == "produce":
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            login = client.post("/auth/login", json={"account": manifest["staff"]["PURCHASER"]["account"],
                                                     "password": os.environ["TASK094_PASSWORD"]})
            login.raise_for_status()
            token = login.json()["data"]["token"]
            response = client.post(f"/approval/records/purchase_plan/{plan['id']}/submit",
                json={"doc_no": plan["no"]}, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        expected = "PENDING"
    else:
        expected = "PUBLISHED"
    deadline = time.monotonic() + (45 if phase == "recover" else 1)
    while True:
        with SessionLocal() as db:
            event = db.scalar(select(NotificationOutbox).where(
                NotificationOutbox.company_id == manifest["company_id"],
                NotificationOutbox.aggregate_type == "purchase_plan",
                NotificationOutbox.aggregate_id == plan["id"],
                NotificationOutbox.event_code == "approval.task_assigned",
            ))
            inboxes = list(db.scalars(select(NotificationInbox).where(NotificationInbox.outbox_id == event.id))) if event else []
            if event and event.status == expected:
                recipients = [row.recipient_staff_id for row in inboxes]
                if phase == "produce" and inboxes:
                    raise AssertionError("中断期间已产生收件箱，不符合此次故障场景")
                if phase == "recover" and sorted(recipients) != sorted([manifest["staff"]["VP"]["id"], manifest["staff"]["QUALITY"]["id"]]):
                    raise AssertionError("恢复后接收人不完整或重复")
                return {"phase": phase, "plan_id": plan["id"], "outbox_id": event.id,
                        "status": event.status, "inbox_ids": [row.id for row in inboxes],
                        "recipient_count": len(recipients), "unique_recipients": len(set(recipients)) == len(recipients)}
        if time.monotonic() >= deadline:
            raise AssertionError(f"未观察到期望状态 {expected}")
        time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("phase", choices=["produce", "recover"])
    args = parser.parse_args()
    print(json.dumps(run(json.loads(args.manifest.read_text(encoding="utf-8")), args.phase), ensure_ascii=False))
