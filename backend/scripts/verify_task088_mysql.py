"""Read-only verification of one TASK-088 purchase plan and its audit trail."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings

DATABASE_NAME = "erp_task088_test"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", required=True, help="E2E 写入的唯一采购计划备注")
    parser.add_argument(
        "--expect-none",
        action="store_true",
        help="只读断言对应备注尚无采购计划，用于保存前/保存后失败的安全核查",
    )
    args = parser.parse_args()

    source_url = make_url(get_settings().database_url)
    if not source_url.drivername.startswith("mysql") or source_url.database not in {
        "erp",
        DATABASE_NAME,
    }:
        raise RuntimeError("验证连接不是约定的 MySQL；拒绝继续")
    engine = create_engine(source_url.set(database=DATABASE_NAME), future=True)

    from app.models.audit_log import AuditLog
    from app.models.org import Staff
    from app.models.purchase import (
        PurchasePlan,
        PurchasePlanCreationRequest,
        PurchasePlanItem,
    )

    try:
        with Session(engine) as db:
            plans = list(
                db.scalars(
                    select(PurchasePlan).where(PurchasePlan.remark == args.marker)
                )
            )
            if args.expect_none:
                if plans:
                    raise AssertionError(
                        f"期望无匹配采购计划，实际找到 {len(plans)} 条"
                    )
                print("只读核查通过：该测试备注没有采购计划记录。")
                return
            if len(plans) != 1:
                raise AssertionError(
                    f"备注匹配采购计划数量应为 1，实际为 {len(plans)}"
                )
            plan = plans[0]
            items = list(
                db.scalars(
                    select(PurchasePlanItem).where(
                        PurchasePlanItem.plan_id == plan.id
                    )
                )
            )
            actor = db.get(Staff, plan.created_by) if plan.created_by else None
            audit = db.scalar(
                select(AuditLog).where(
                    AuditLog.target_type == "purchase_plan",
                    AuditLog.target_id == str(plan.id),
                    AuditLog.action == "CREATE",
                )
            )
            if audit is None:
                raise AssertionError("未找到采购计划 CREATE 审计记录")
            detail = json.loads(audit.detail or "{}")
            request_id = detail.get("request_id")
            assistant_draft_id = detail.get("assistant_draft_id")
            request = db.scalar(
                select(PurchasePlanCreationRequest).where(
                    PurchasePlanCreationRequest.request_id == request_id,
                    PurchasePlanCreationRequest.plan_id == plan.id,
                )
            )
            if plan.status != "IN_PROGRESS" or plan.audit_status != "PENDING":
                raise AssertionError("采购计划状态或审核状态与新建初始值不符")
            if len(items) != 1 or request is None or request.status != "SUCCEEDED":
                raise AssertionError("计划明细或幂等成功记录缺失")
            if detail.get("source") != "assistant_draft" or not assistant_draft_id:
                raise AssertionError("CREATE 审计未记录助手草稿来源")
            if actor is None or audit.actor_id != actor.id or audit.actor_name != actor.name:
                raise AssertionError("制单员工与审计 actor 不一致")
            if actor.login_account != "caigouyuan":
                raise AssertionError("单据不是由测试采购员账号创建")
            if (
                str(items[0].planned_qty) != "2.000"
                or str(items[0].purchase_price) != "3.45"
                or str(items[0].amount) != "6.90"
            ):
                raise AssertionError("计划数量、采购价或金额与表单输入不符")

            print(
                "TASK-088 MySQL 已核实："
                f"plan_no={plan.plan_no}，status={plan.status}，"
                f"audit_status={plan.audit_status}，明细=1，"
                f"request={request.status}，来源=assistant_draft，"
                f"制单人={actor.name}，金额={plan.total_amount}。"
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
