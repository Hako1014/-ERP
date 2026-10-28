"""页面、内置助手和后续 MCP 适配器可复用的固定查询入口。

返回值是内部业务结构；外发前还必须通过 query_projection 白名单。
禁止将此模块的方法名当作客户端可调用的反射式路由。
"""

from sqlalchemy.orm import Session

from app.core.query_access import authorize_query
from app.services import approval_manage_service, approval_service, metrics_service
from app.services import purchase_order_service, stock_settlement_service


def dashboard(db: Session, **filters) -> dict:
    authorize_query(db, "dashboard")
    return metrics_service.dashboard_overview(db, **filters)


def purchase_orders(db: Session, **filters) -> dict:
    authorize_query(db, "purchase_orders")
    return purchase_order_service.list_orders(db, **filters)


def purchase_order(db: Session, order_id: int) -> dict:
    access = authorize_query(db, "purchase_orders")
    order = purchase_order_service.get_order_or_404(
        db, order_id, company_id=access.policy.company_id,
    )
    return purchase_order_service.order_dict(
        db, order, with_items=True, with_approval=True,
        company_id=access.policy.company_id,
    )


def stock_settings(db: Session) -> dict:
    access = authorize_query(db, "stock_settings")
    row = stock_settlement_service.read_stock_setting(db, access.policy.company_id)
    return stock_settlement_service.stock_setting_dict(row)


def approval_flows(db: Session, *, configuration: bool = False) -> list[dict]:
    access = authorize_query(db, "approval_configuration" if configuration else "approval_business")
    if configuration:
        return approval_manage_service.list_manage_flows(db)
    # 业务选择器只公开本人职责内可使用的类型，不包含员工名单、审批意见或配置。
    role_types = {
        "PURCHASER": {"purchase_plan", "purchase_order", "purchase_prepay", "purchase_return"},
        "VP": {"purchase_prepay"},
        "FINANCE": {"purchase_order", "purchase_prepay", "purchase_return", "purchase_invoice", "order"},
        "QUALITY": {"purchase_return"},
        "WAREHOUSE": {"stock_in", "stock_out", "transfer", "transfer_out", "transfer_in", "stock_check", "location_adjust", "purchase_return"},
    }
    types = set().union(*(role_types.get(role, set()) for role in access.roles))
    return [
        {key: row[key] for key in ("id", "name", "doc_type")}
        for row in approval_service.list_flow_dicts(db)
        if "ADMIN" in access.roles or row["doc_type"] in types
    ]


def print_batch(db: Session, *, batch_no: str) -> dict:
    from app.core.errors import BizException, ErrorCode
    from app.services import order_service, print_service

    authorize_query(db, "print_batch")
    data = print_service.get_print_batch(db, batch_no=batch_no)
    # 快照可能包含已删除或不一致的关联，整批拒绝，避免仅过滤顶层而泄漏嵌套内容。
    ids = {row["id"] for row in data["orders"]}
    related = {row["order_id"] for row in data["waybills"]}
    related.update(row.get("order_id") for row in data["delivery_lists"])
    related.update(row.get("order_id") for row in (data.get("pick_list") or {}).get("rows", []))
    if not ids or None in related or not related <= ids:
        raise BizException(ErrorCode.NOT_FOUND, "打印批次关联不可用")
    for order_id in ids:
        order_service.get_order_or_404(db, order_id)
    data["order_count"] = len(ids)
    return data


def approval_state(db: Session, *, doc_type: str, doc_id: int) -> dict:
    authorize_query(db, "approval_business")
    approval_manage_service.check_document_access(db, doc_type, doc_id)
    return approval_service.get_state(db, doc_type=doc_type, doc_id=doc_id)


def external_purchase_order(db: Session, order_id: int) -> dict:
    from app.services import query_projection

    data = purchase_order(db, order_id)
    return query_projection.purchase_order(data, authorize_query(db, "purchase_orders"))


def external_print_batch(db: Session, *, batch_no: str) -> dict:
    from app.services import query_projection

    data = print_batch(db, batch_no=batch_no)
    return query_projection.print_batch(data, authorize_query(db, "print_batch"))
