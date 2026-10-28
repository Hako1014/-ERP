"""TASK-098 本机 erp 只读核验；不签发凭据、不审计写入、不变更业务数据。"""

import argparse
import json

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.data_scope import clear_scope_policy, initialize_scope_policy
from app.core.errors import BizException, ErrorCode
from app.models.org import Staff
from app.models.rbac import Role, StaffRole
from app.services import metrics_service, shared_queries


def _only_select(_conn, _cursor, statement, _parameters, _context, _many):
    normalized = statement.strip().upper()
    if not (normalized.startswith("SELECT ") or normalized == "SET TRANSACTION READ ONLY"):
        raise RuntimeError("拒绝非只读语句")


def _no_flush(*_args):
    raise RuntimeError("拒绝ORM写入")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-read-only-erp", action="store_true")
    if not parser.parse_args().confirm_read_only_erp:
        parser.error("需要 --confirm-read-only-erp；仅运行现有本机erp的SELECT查询")
    url = make_url(get_settings().database_url)
    if url.drivername != "mysql+pymysql" or url.host not in {"127.0.0.1", "localhost"} or (url.port or 3306) != 3306 or url.database != "erp":
        print("拒绝执行：目标不是约定的本机 MySQL erp，连接信息不输出。")
        return 1
    engine = create_engine(url, connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10}, pool_size=1, max_overflow=0)
    event.listen(engine, "before_cursor_execute", _only_select)
    results = []
    try:
        with Session(engine, autoflush=False) as db:
            event.listen(db, "before_flush", _no_flush)
            db.execute(text("SET TRANSACTION READ ONLY"))
            version = db.scalar(text("SELECT VERSION()"))
            credential_table = bool(db.scalar(text(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'mcp_credential'"
            )))
            roles = ("ADMIN", "VP", "FINANCE", "PURCHASER", "WAREHOUSE", "QUALITY", "AFTER_SALE")
            checks = {
                "ADMIN": [("采购列表", shared_queries.purchase_orders), ("采购入库报表", metrics_service.purchase_inbound_report)],
                "FINANCE": [("采购发票报表", metrics_service.purchase_invoice_report)],
                "PURCHASER": [("采购列表", shared_queries.purchase_orders), ("采购订单报表", metrics_service.purchase_order_report)],
                "WAREHOUSE": [("库存设置", shared_queries.stock_settings)],
                "VP": [("拒绝全采购列表", shared_queries.purchase_orders)],
                "QUALITY": [("拒绝全库存设置", shared_queries.stock_settings)],
                "AFTER_SALE": [("拒绝全采购列表", shared_queries.purchase_orders)],
            }
            for role in roles:
                staff = db.scalar(select(Staff).join(StaffRole, StaffRole.staff_id == Staff.id)
                                  .join(Role, Role.id == StaffRole.role_id)
                                  .where(Role.code == role, Staff.is_active.is_(True))
                                  .order_by(Staff.id).limit(1))
                if staff is None:
                    results.append({"role": role, "result": "SKIP_NO_ACTIVE_STAFF"})
                    continue
                clear_scope_policy(db)
                initialize_scope_policy(db, staff)
                for name, query in checks[role]:
                    expected_denial = name.startswith("拒绝")
                    try:
                        query(db)
                    except BizException as exc:
                        ok = expected_denial and exc.code == ErrorCode.FORBIDDEN
                    else:
                        ok = not expected_denial
                    results.append({"role": role, "check": name, "result": "PASS" if ok else "FAIL"})
            clear_scope_policy(db)
            db.rollback()
        print(json.dumps({"mysql_version": version, "mcp_credential_table_exists": credential_table,
                          "checks": results, "business_values_exported": False, "writes": 0}, ensure_ascii=False))
        return 0 if results and all(row["result"] == "PASS" for row in results) else 1
    except Exception as exc:
        # 数据库异常可能含主机/口令/SQL参数，仅打印异常类名。
        print(json.dumps({"result": "FAILED", "error_type": type(exc).__name__}, ensure_ascii=False))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
