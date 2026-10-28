"""受控验收操作：只处理 manifest 的 TASK094 记录，不删除数据。"""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.org import Staff, Company
from app.models.approval import ApprovalFlow
from app.models.notification import NotificationOutbox, NotificationInbox, NotificationDeliveryAttempt
from app.models.purchase import PurchaseOrder, PurchasePlan
from datetime import datetime
from app.services.notification_service import enqueue_purchase_order_arrival


def run(manifest: dict, action: str) -> dict:
    with SessionLocal() as db:
        company = db.get(Company, manifest["company_id"])
        if not company or company.short_name != manifest["marker"] or not manifest["marker"].startswith("TASK094-"):
            raise RuntimeError("公司标识不匹配")
        if action == "new-plans":
            manifest["plans"] = {}
            suffix = datetime.now().strftime("%H%M%S")
            for kind in ("approve", "reject", "reminder", "offline", "revoke"):
                plan = PurchasePlan(plan_no=f"{manifest['marker']}-{suffix}-{kind[:3]}",
                    supplier_id=manifest["supplier_id"], created_by=manifest["staff"]["PURCHASER"]["id"],
                    remark=manifest["marker"])
                db.add(plan)
                db.flush()
                manifest["plans"][kind] = {"id": plan.id, "no": plan.plan_no}
            db.commit()
            return manifest
        if action == "arrival":
            order = db.get(PurchaseOrder, manifest["order_id"])
            if order.created_by != manifest["staff"]["PURCHASER"]["id"] or not order.order_no.startswith(manifest["marker"]):
                raise RuntimeError("采购订单不是本次测试夹具")
            enqueue_purchase_order_arrival(db, company_id=company.id, stock_in_id=manifest["stock_in_id"], order=order)
            db.commit()
            return {"arrival_enqueued": True}
        if action in {"disable-vp", "restore-vp"}:
            staff = db.get(Staff, manifest["staff"]["VP"]["id"])
            if staff.company_id != company.id or not staff.login_account.startswith(manifest["marker"]):
                raise RuntimeError("不是测试员工")
            staff.is_active = action == "restore-vp"
            db.commit()
            return {"staff_active": staff.is_active}
        if action == "archive":
            flow = db.get(ApprovalFlow, manifest["flow_id"])
            if flow.name != manifest["marker"]:
                raise RuntimeError("审批流程不是测试记录")
            flow.doc_type = "task094_archived"
            for data in manifest["staff"].values():
                staff = db.get(Staff, data["id"])
                if staff.login_account != data["account"]:
                    raise RuntimeError("测试账号标识变化")
                staff.is_active = False
            db.commit()
        outboxes = list(db.scalars(select(NotificationOutbox).where(NotificationOutbox.company_id == company.id)))
        inboxes = list(db.scalars(select(NotificationInbox).where(NotificationInbox.company_id == company.id)))
        attempts = list(db.scalars(select(NotificationDeliveryAttempt).join(NotificationInbox,
            NotificationInbox.id == NotificationDeliveryAttempt.inbox_id).where(NotificationInbox.company_id == company.id)))
        pairs = [(row.outbox_id, row.recipient_staff_id, row.channel) for row in inboxes]
        return {"marker": manifest["marker"], "outbox_ids": [row.id for row in outboxes],
            "outbox_statuses": {s: sum(r.status == s for r in outboxes) for s in {r.status for r in outboxes}},
            "inbox_ids": [row.id for row in inboxes], "unique_recipients": len(pairs) == len(set(pairs)),
            "read_states": {s: sum(r.read_state == s for r in inboxes) for s in {r.read_state for r in inboxes}},
            "business_states": {s: sum(r.business_state == s for r in inboxes) for s in {r.business_state for r in inboxes}},
            "realtime_states": {s: sum(r.status == s for r in attempts) for s in {r.status for r in attempts}},
            "archived": action == "archive"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("action", choices=["new-plans", "arrival", "disable-vp", "restore-vp", "report", "archive"])
    args = parser.parse_args()
    print(json.dumps(run(json.loads(args.manifest.read_text(encoding="utf-8")), args.action), ensure_ascii=False))
