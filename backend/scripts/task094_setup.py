"""仅向明确确认的本机 erp 新增 TASK094 验收记录；不覆盖/删除原数据。"""

import argparse
import json
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import make_url

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.approval import ApprovalFlow
from app.models.org import Company, Department, Staff
from app.models.party import Supplier
from app.models.purchase import PurchasePlan, PurchaseOrder
from app.models.rbac import Role, StaffRole
from app.models.stock import StockIn
from app.models.warehouse import Warehouse

ROLES = ("ADMIN", "VP", "FINANCE", "PURCHASER", "WAREHOUSE", "QUALITY", "AFTER_SALE")


def setup() -> dict:
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("mysql") or url.database != "erp" or url.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("只允许明确确认的本机 MySQL erp")
    marker = "TASK094-" + datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    password = os.environ.get("TASK094_PASSWORD")
    if not password:
        raise RuntimeError("必须显式提供本地测试密码 TASK094_PASSWORD")
    with SessionLocal() as db:
        if db.scalar(select(ApprovalFlow.id).where(ApprovalFlow.doc_type == "purchase_plan")):
            raise RuntimeError("采购计划已有全局审批流，拒绝覆盖；请先确认新的隔离方案")
        company = Company(full_name=f"{marker} 验收公司", short_name=marker)
        other = Company(full_name=f"{marker} 跨公司", short_name=marker + "B")
        db.add_all([company, other])
        db.flush()
        department = Department(company_id=company.id, name=f"{marker} 部门")
        db.add(department)
        db.flush()
        people = {}
        for code in ROLES:
            role = Role(company_id=company.id, code=code, name=f"TASK094 {code}")
            staff = Staff(company_id=company.id, department_id=department.id, name=f"TASK094 {code}",
                staff_no=f"{marker}-{code}", login_account=f"{marker}-{code.lower()}",
                password_hash=hash_password(password), is_active=True)
            db.add_all([role, staff])
            db.flush()
            db.add(StaffRole(staff_id=staff.id, role_id=role.id))
            people[code] = {"id": staff.id, "account": staff.login_account}
        other_role = Role(company_id=other.id, code="ADMIN", name="TASK094 跨公司管理员")
        outsider = Staff(company_id=other.id, name="TASK094 跨公司", login_account=marker + "-other",
                         password_hash=hash_password(password), is_active=True)
        db.add_all([other_role, outsider])
        db.flush()
        db.add(StaffRole(staff_id=outsider.id, role_id=other_role.id))
        people["OTHER"] = {"id": outsider.id, "account": outsider.login_account}
        supplier = Supplier(code=marker, name=f"{marker} 供应商", is_active=True)
        warehouse = Warehouse(code=marker, name=f"{marker} 仓库", is_enabled=True)
        db.add_all([supplier, warehouse])
        db.flush()
        flow = ApprovalFlow(name=marker, doc_type="purchase_plan", created_by=people["ADMIN"]["id"],
            approvers=json.dumps([
                {"step": 1, "name": "TASK094 初审", "approver_ids": [people["VP"]["id"], people["QUALITY"]["id"]]},
                {"step": 2, "name": "TASK094 复审", "approver_ids": [people["FINANCE"]["id"]]},
            ]))
        db.add(flow)
        plans = {}
        for suffix in ("approve", "reject", "reminder", "offline", "revoke"):
            plan = PurchasePlan(plan_no=f"{marker}-{suffix[:3]}", supplier_id=supplier.id,
                                created_by=people["PURCHASER"]["id"], remark=marker)
            db.add(plan)
            db.flush()
            plans[suffix] = {"id": plan.id, "no": plan.plan_no}
        order = PurchaseOrder(order_no=marker + "-PO", supplier_id=supplier.id, warehouse_id=warehouse.id,
            created_by=people["PURCHASER"]["id"], purchaser_id=people["ADMIN"]["id"],
            status="RECEIVING", description=marker)
        db.add(order)
        db.flush()
        stock_in = StockIn(stock_in_no=marker + "-IN", warehouse_id=warehouse.id,
            stock_in_type="PURCHASE", purchase_order_id=order.id, status="APPROVED",
            created_by=people["WAREHOUSE"]["id"])
        db.add(stock_in)
        db.flush()
        db.commit()
        return {"marker": marker, "company_id": company.id, "other_company_id": other.id,
                "flow_id": flow.id, "staff": people, "plans": plans,
                "order_id": order.id, "stock_in_id": stock_in.id, "warehouse_id": warehouse.id,
                "supplier_id": supplier.id, "active_from": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-write", required=True, choices=["erp"])
    parser.parse_args()
    print(json.dumps(setup(), ensure_ascii=False))
