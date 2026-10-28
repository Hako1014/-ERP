"""在本机 erp 为 guanliyuan 新建或复用一张未审核采购付款测试单。

使用项目 .env 和正式创建服务；不会审核付款、生成凭证或改动库存。
运行方式（backend 目录）：.venv/Scripts/python.exe ../scripts/seed_purchase_payment_demo.py
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.data_scope import initialize_scope_policy
from app.core.permissions import permissions_for_roles
from app.core.query_access import enable_company_query_scope
from app.db.session import session_scope
from app.models.approval import ApprovalRecord
from app.models.finance import Payment, Voucher
from app.models.org import Staff
from app.models.party import Supplier
from app.repositories.rbac_repo import list_staff_role_codes
from app.services.pay_receipt_service import create_payment, list_payments

DEMO_TYPE = "测试采购付款-20261014"
DEMO_ACCOUNT = "guanliyuan"
DEMO_AMOUNT = Decimal("100.00")


def seed() -> dict:
    uri = make_url(get_settings().database_url)
    if uri.host not in {"localhost", "127.0.0.1"} or uri.port != 3306 or uri.database != "erp":
        raise ValueError("测试数据仅允许写入本机 3306 的 erp 库")

    with session_scope() as db:
        staff = db.scalar(select(Staff).where(Staff.login_account == DEMO_ACCOUNT))
        if staff is None or not staff.is_active:
            raise ValueError("目标管理员账号不存在或未启用")
        codes = permissions_for_roles(list_staff_role_codes(db, staff.id))
        if "*" not in codes and "finance:manage" not in codes:
            raise ValueError("目标账号没有财务制单权限")
        initialize_scope_policy(db, staff)
        enable_company_query_scope(db, staff.company_id)

        payment = db.scalar(
            select(Payment).where(
                Payment.company_id == staff.company_id,
                Payment.created_by == staff.id,
                Payment.payment_type == DEMO_TYPE,
            ).order_by(Payment.id)
        )
        reused = payment is not None
        if payment is None:
            supplier = db.scalar(
                select(Supplier).where(
                    Supplier.company_id == staff.company_id,
                    Supplier.is_active.is_(True),
                ).order_by(Supplier.code.like("DEMO-%").desc(), Supplier.id)
            )
            if supplier is None:
                raise ValueError("本公司没有可用于测试的启用供应商")
            before = (
                db.scalar(select(func.count(Voucher.id))),
                db.scalar(select(func.count(ApprovalRecord.id))),
            )
            payment = create_payment(
                db,
                staff=staff,
                data={
                    "supplier_id": supplier.id,
                    "amount": DEMO_AMOUNT,
                    "payment_type": DEMO_TYPE,
                    "pay_method": "仅展示测试",
                },
                commit=False,
            )
            after = (
                db.scalar(select(func.count(Voucher.id))),
                db.scalar(select(func.count(ApprovalRecord.id))),
            )
            if before != after or payment.status != "PENDING" or payment.audit_status != "PENDING":
                raise ValueError("创建结果不符合仅新增未审核测试单的约定")
            if payment.company_id != staff.company_id:
                raise ValueError("测试单公司归属不匹配")

        page = list_payments(db, keyword=payment.payment_no, page_size=20)
        if not any(row["id"] == payment.id for row in page["items"]):
            raise ValueError("目标账号的付款列表无法读取测试单")
        result = {
            "database": uri.database,
            "company_id": payment.company_id,
            "created_by": payment.created_by,
            "payment_id": payment.id,
            "payment_no": payment.payment_no,
            "supplier_id": payment.supplier_id,
            "payment_type": payment.payment_type,
            "amount": format(payment.amount, ".2f"),
            "status": payment.status,
            "audit_status": payment.audit_status,
            "reused": reused,
        }
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(seed(), ensure_ascii=True))
    except ValueError as exc:
        print(f"测试数据未创建：{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # 不回显可能包含连接信息、SQL 参数或凭据的异常正文。
        print(f"测试数据创建失败：{type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
