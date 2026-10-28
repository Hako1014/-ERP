"""审批管理与我的审批服务（TASK-041）。

本模块复用 `approval_service` 的状态机和审批留痕，只补充协同办公页面需要的：
- 审批流配置展示与维护；
- 按当前审批人聚合“我的审批”；
- 单据详情、审批流与留痕聚合；
- 审核通过、审核不通过、弃审的业务服务分派。

审核和弃审必须分发到各业务服务，不能只更新审批记录，否则会绕过库存、
采购订单到货、预付款凭证等既有业务副作用。
"""

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import and_, case, func, or_, select, tuple_, union_all
from sqlalchemy.orm import Session

from app.core.errors import BizException, ErrorCode
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.org import Staff
from app.repositories.approval_repo import (
    get_flow_by_id,
    list_flows,
    list_records,
)
from app.repositories.org_repo import get_staff_by_id
from app.services import (
    approval_service,
    order_service,
    prepay_service,
    purchase_invoice_service,
    purchase_order_service,
    purchase_plan_service,
    purchase_return_service,
    stock_check_service,
    stock_in_service,
    stock_out_service,
    stock_query_service,
    transfer_service,
)


class MyApprovalScope:
    PENDING = "pending"
    SUBMITTED = "submitted"
    PROCESSED = "processed"
    ALL = {PENDING, SUBMITTED, PROCESSED}


_APPROVAL_LIST_SCAN_BATCH_SIZE = 200


DOC_TYPE_LABELS: dict[str, str] = {
    "purchase_plan": "采购计划",
    "purchase_order": "采购订单",
    "purchase_prepay": "采购预付款申请",
    "purchase_return": "采购退货单",
    "purchase_invoice": "采购发票",
    "stock_in": "采购入库单",
    "stock_out": "采购出库单",
    "transfer": "调拨单",
    "transfer_out": "调拨出库单",
    "transfer_in": "调拨入库单",
    "stock_check": "库存盘点单",
    "location_adjust": "库位调整单",
    "order": "销售订单",
}

_DOC_NO_FIELDS = {
    "purchase_plan": "plan_no",
    "purchase_order": "order_no",
    "purchase_prepay": "prepay_no",
    "purchase_return": "return_no",
    "purchase_invoice": "invoice_no",
    "stock_in": "stock_in_no",
    "stock_out": "stock_out_no",
    "transfer": "transfer_no",
    "transfer_out": "transfer_out_no",
    "transfer_in": "transfer_in_no",
    "stock_check": "check_no",
    "location_adjust": "adjust_no",
    "order": "order_no",
}

_APPROVE_HANDLERS: dict[str, tuple[str, Callable[..., Any]]] = {
    "purchase_plan": ("plan_id", purchase_plan_service.approve_plan),
    "purchase_order": ("order_id", purchase_order_service.approve_order),
    "purchase_prepay": ("prepay_id", prepay_service.approve_prepay),
    "purchase_return": ("return_id", purchase_return_service.approve_return),
    "purchase_invoice": ("invoice_id", purchase_invoice_service.approve_invoice),
    "stock_in": ("stock_in_id", stock_in_service.approve_stock_in),
    "stock_out": ("stock_out_id", stock_out_service.approve_stock_out),
    "transfer": ("transfer_id", transfer_service.approve_transfer),
    "transfer_out": (
        "transfer_out_id",
        transfer_service.approve_transfer_out,
    ),
    "transfer_in": ("transfer_in_id", transfer_service.approve_transfer_in),
    "stock_check": ("check_id", stock_check_service.approve_stock_check),
    "location_adjust": (
        "adjust_id",
        stock_query_service.approve_location_adjust,
    ),
    "order": ("order_id", order_service.approve_order),
}

_REJECT_HANDLERS: dict[str, tuple[str, Callable[..., Any]]] = {
    "purchase_plan": ("plan_id", purchase_plan_service.reject_plan),
    "purchase_order": ("order_id", purchase_order_service.reject_order),
    "purchase_prepay": ("prepay_id", prepay_service.reject_prepay),
    "purchase_return": ("return_id", purchase_return_service.reject_return),
    "purchase_invoice": ("invoice_id", purchase_invoice_service.reject_invoice),
    "stock_in": ("stock_in_id", stock_in_service.reject_stock_in),
    "stock_out": ("stock_out_id", stock_out_service.reject_stock_out),
    "transfer": ("transfer_id", transfer_service.reject_transfer),
    "transfer_out": (
        "transfer_out_id",
        transfer_service.reject_transfer_out,
    ),
    "transfer_in": ("transfer_in_id", transfer_service.reject_transfer_in),
    "stock_check": ("check_id", stock_check_service.reject_stock_check),
    "location_adjust": (
        "adjust_id",
        stock_query_service.reject_location_adjust,
    ),
    "order": ("order_id", order_service.reject_order),
}

# 只有实现了业务逆向处理的服务才开放弃审，避免库存、凭证或下游单据失真。
_UNAUDIT_HANDLERS: dict[str, tuple[str, Callable[..., Any]]] = {
    "purchase_plan": ("plan_id", purchase_plan_service.unaudit_plan),
    "purchase_order": ("order_id", purchase_order_service.unaudit_order),
    "purchase_prepay": ("prepay_id", prepay_service.unaudit_prepay),
    "purchase_return": ("return_id", purchase_return_service.unaudit_return),
    "purchase_invoice": (
        "invoice_id",
        purchase_invoice_service.unaudit_invoice,
    ),
    "order": ("order_id", order_service.unaudit_order),
}


def _detail_purchase_plan(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = purchase_plan_service.get_plan_or_404(db, doc_id)
    return purchase_plan_service.plan_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_purchase_order(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = purchase_order_service.get_order_or_404(db, doc_id)
    return purchase_order_service.order_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_prepay(db: Session, doc_id: int, *, with_items: bool) -> dict:
    del with_items
    record = prepay_service.get_prepay_or_404(db, doc_id)
    return prepay_service.prepay_dict(db, record, with_approval=False)


def _detail_purchase_return(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = purchase_return_service.get_return_or_404(db, doc_id)
    return purchase_return_service.return_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_purchase_invoice(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    del with_items
    record = purchase_invoice_service.get_invoice_or_404(db, doc_id)
    return purchase_invoice_service.invoice_dict(
        db, record, with_approval=False
    )


def _detail_stock_in(db: Session, doc_id: int, *, with_items: bool) -> dict:
    record = stock_in_service.get_stock_in_or_404(db, doc_id)
    return stock_in_service.stock_in_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_stock_out(db: Session, doc_id: int, *, with_items: bool) -> dict:
    record = stock_out_service.get_stock_out_or_404(db, doc_id)
    return stock_out_service.stock_out_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_transfer(db: Session, doc_id: int, *, with_items: bool) -> dict:
    record = transfer_service.get_transfer_or_404(db, doc_id)
    return transfer_service.transfer_dict(
        db,
        record,
        with_items=with_items,
        with_docs=False,
        with_approval=False,
    )


def _detail_transfer_out(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = transfer_service.get_transfer_out_or_404(db, doc_id)
    return transfer_service.transfer_out_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_transfer_in(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = transfer_service.get_transfer_in_or_404(db, doc_id)
    return transfer_service.transfer_in_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_stock_check(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = stock_check_service.get_stock_check_or_404(db, doc_id)
    return stock_check_service.stock_check_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_location_adjust(
    db: Session, doc_id: int, *, with_items: bool
) -> dict:
    record = stock_query_service.get_location_adjust_or_404(db, doc_id)
    return stock_query_service.location_adjust_dict(
        db, record, with_items=with_items, with_approval=False
    )


def _detail_order(db: Session, doc_id: int, *, with_items: bool) -> dict:
    from app.core.data_scope import document_visible, require_scope_policy
    from app.core.query_access import current_actor_access

    policy = require_scope_policy(db)
    _, roles, _ = current_actor_access(db, policy.staff_id)
    if not roles.intersection({"ADMIN", "FINANCE", "WAREHOUSE"}) and not document_visible(db, None, "order", doc_id):
        raise BizException(ErrorCode.NOT_FOUND, "审批单据不存在")
    record = order_service.get_order_or_404(
        db, doc_id, company_id=policy.company_id,
    )
    return order_service.order_dict(
        db, record, with_items=with_items, with_approval=False,
        company_id=policy.company_id,
    )


_DETAIL_HANDLERS: dict[str, Callable[..., dict]] = {
    "purchase_plan": _detail_purchase_plan,
    "purchase_order": _detail_purchase_order,
    "purchase_prepay": _detail_prepay,
    "purchase_return": _detail_purchase_return,
    "purchase_invoice": _detail_purchase_invoice,
    "stock_in": _detail_stock_in,
    "stock_out": _detail_stock_out,
    "transfer": _detail_transfer,
    "transfer_out": _detail_transfer_out,
    "transfer_in": _detail_transfer_in,
    "stock_check": _detail_stock_check,
    "location_adjust": _detail_location_adjust,
    "order": _detail_order,
}


def _validate_doc_type(doc_type: str) -> str:
    normalized = (doc_type or "").strip()
    if normalized not in DOC_TYPE_LABELS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的单据类型：{doc_type}"
        )
    return normalized


def _staff_brief(db: Session, staff_id: int | None) -> dict | None:
    if staff_id is None:
        return None
    staff = get_staff_by_id(db, staff_id)
    if staff is None:
        return {"id": staff_id, "name": None}
    return {
        "id": staff.id,
        "name": staff.name,
        "staff_no": staff.staff_no,
        "department_id": staff.department_id,
        "position_id": staff.position_id,
        "is_active": staff.is_active,
    }


def _step_dicts(db: Session, flow: ApprovalFlow) -> list[dict]:
    steps = approval_service.steps_of(flow)
    result: list[dict] = []
    for step in steps:
        approvers = [
            item
            for item in (_staff_brief(db, approver_id) for approver_id in step.approver_ids)
            if item is not None
        ]
        result.append(
            {
                "step": step.step,
                "name": step.name,
                "approver_ids": list(step.approver_ids),
                "approvers": approvers,
            }
        )
    return result


def _flow_manage_dict(db: Session, flow: ApprovalFlow) -> dict:
    data = approval_service.flow_dict(flow)
    steps = _step_dicts(db, flow)
    parts: list[str] = []
    for step in steps:
        names = [item["name"] or f"员工{item['id']}" for item in step["approvers"]]
        label = "、".join(names)
        if step["name"]:
            label = f"{step['name']}：{label}"
        parts.append(label)

    data["doc_type_label"] = DOC_TYPE_LABELS.get(flow.doc_type, flow.doc_type)
    data["created_by_name"] = (db.get(Staff, flow.created_by).name if flow.created_by else None)
    data["steps"] = steps
    data["flow_display"] = " → ".join(parts) if parts else None
    return data


def list_doc_types() -> list[dict]:
    return [
        {"code": doc_type, "name": label}
        for doc_type, label in DOC_TYPE_LABELS.items()
    ]


def list_manage_flows(db: Session) -> list[dict]:
    return [_flow_manage_dict(db, flow) for flow in list_flows(db)]


def create_manage_flow(
    db: Session,
    *,
    name: str,
    doc_type: str,
    approvers: list[Any],
    staff: Staff,
) -> dict:
    flow = approval_service.create_flow(
        db,
        name=name,
        doc_type=_validate_doc_type(doc_type),
        approvers=approvers,
        actor=staff,
    )
    return _flow_manage_dict(db, flow)


def update_manage_flow(
    db: Session,
    *,
    flow_id: int,
    name: str | None,
    doc_type: str | None,
    approvers: list[Any] | None,
    staff: Staff,
) -> dict:
    flow = approval_service.update_flow(
        db,
        flow_id=flow_id,
        name=name,
        doc_type=_validate_doc_type(doc_type) if doc_type is not None else None,
        approvers=approvers,
        actor=staff,
    )
    return _flow_manage_dict(db, flow)


def _pending_record(records: list[ApprovalRecord]) -> ApprovalRecord | None:
    pending = [
        record
        for record in records
        if record.action == approval_service.ApprovalAction.APPROVE
        and record.status == approval_service.ApprovalStatus.PENDING
    ]
    if not pending:
        return None
    return max(pending, key=lambda record: (record.step, record.id))


def _flow_for_records(
    db: Session, records: list[ApprovalRecord]
) -> ApprovalFlow | None:
    flow_id = next(
        (
            record.flow_id
            for record in sorted(records, key=lambda item: item.id, reverse=True)
            if record.flow_id is not None
        ),
        None,
    )
    return get_flow_by_id(db, flow_id) if flow_id is not None else None


def _step_of_flow(steps: list[approval_service.ApprovalStep], step: int) -> Any:
    return next((item for item in steps if item.step == step), None)


def _current_approval(
    db: Session, records: list[ApprovalRecord]
) -> tuple[ApprovalFlow | None, list[approval_service.ApprovalStep], ApprovalRecord | None, Any]:
    flow = _flow_for_records(db, records)
    steps = approval_service.steps_of(flow) if flow is not None else []
    pending = _pending_record(records)
    step_config = _step_of_flow(steps, pending.step) if pending is not None else None
    return flow, steps, pending, step_config


def _is_current_pending_approver(
    db: Session, records: list[ApprovalRecord], staff_id: int
) -> bool:
    _, _, pending, step_config = _current_approval(db, records)
    return bool(
        pending is not None
        and step_config is not None
        and staff_id in step_config.approver_ids
    )


def _record_dict(
    db: Session,
    record: ApprovalRecord,
    steps: list[approval_service.ApprovalStep],
) -> dict:
    step = _step_of_flow(steps, record.step)
    approver = _staff_brief(db, record.approver_id)
    return {
        "id": record.id,
        "step": record.step,
        "step_name": step.name if step is not None else None,
        "action": record.action,
        "status": record.status,
        "approver_id": record.approver_id,
        "approver_name": approver["name"] if approver else None,
        "comment": record.comment,
        "reason": record.reason,
        "audit_time": record.audit_time,
        "created_at": record.created_at,
    }


def _load_detail(
    db: Session,
    doc_type: str,
    doc_id: int,
    *,
    with_items: bool,
) -> dict:
    handler = _DETAIL_HANDLERS.get(doc_type)
    if handler is None:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的单据类型：{doc_type}"
        )
    return handler(db, doc_id, with_items=with_items)


def check_document_access(db: Session, doc_type: str, doc_id: int) -> None:
    """审批留痕本身不能授予跨公司或孤儿单据的访问权。"""
    _load_detail(db, _validate_doc_type(doc_type), doc_id, with_items=False)


def _doc_no(records: list[ApprovalRecord], detail: dict, doc_type: str) -> str | None:
    for record in records:
        if record.doc_no:
            return record.doc_no
    field = _DOC_NO_FIELDS.get(doc_type)
    return detail.get(field) if field else None


def _latest_record(records: list[ApprovalRecord]) -> ApprovalRecord:
    return max(
        records,
        key=lambda record: (
            record.audit_time or record.created_at or datetime.min,
            record.id,
        ),
    )


def _approval_state_or_empty(
    db: Session, doc_type: str, doc_id: int
) -> dict:
    try:
        return approval_service.get_state(db, doc_type=doc_type, doc_id=doc_id)
    except BizException:
        return {
            "doc_type": doc_type,
            "doc_id": doc_id,
            "flow_name": None,
            "state": {
                "status": "UNKNOWN",
                "current_step": None,
                "total_steps": 0,
                "approved_steps": [],
            },
            "records": [],
        }


def _build_list_item(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    records: list[ApprovalRecord],
    approval_state: dict | None = None,
    compact: bool = False,
) -> dict:
    detail = _load_detail(db, doc_type, doc_id, with_items=False)

    flow, steps, pending, step_config = _current_approval(db, records)
    approval = (
        {"state": approval_state}
        if approval_state is not None
        else _approval_state_or_empty(db, doc_type, doc_id)
    )
    submit_record = next(
        (
            record
            for record in records
            if record.action == approval_service.ApprovalAction.SUBMIT
        ),
        None,
    )
    latest = _latest_record(records)
    current_approvers = (
        [
            item
            for item in (
                _staff_brief(db, approver_id)
                for approver_id in step_config.approver_ids
            )
            if item is not None
        ]
        if step_config is not None and not compact
        else []
    )
    state = approval.get("state", {})
    return {
        "doc_type": doc_type,
        "doc_type_label": DOC_TYPE_LABELS.get(doc_type, doc_type),
        "doc_id": doc_id,
        "doc_no": _doc_no(records, detail, doc_type),
        "business_status": detail.get("status"),
        "audit_status": detail.get("audit_status"),
        "approval_status": state.get("status"),
        "current_step": state.get("current_step"),
        "total_steps": state.get("total_steps"),
        "flow_id": flow.id if flow is not None else None,
        "flow_name": flow.name if flow is not None else None,
        "submitted_by": (
            submit_record.approver_id
            if submit_record and not compact
            else None
        ),
        "submitted_by_name": (
            (_staff_brief(db, submit_record.approver_id) or {}).get("name")
            if submit_record and not compact
            else None
        ),
        "submitted_at": submit_record.audit_time if submit_record else None,
        "current_approvers": current_approvers,
        "latest_action": latest.action,
        "latest_action_status": latest.status,
        "latest_action_at": latest.audit_time,
        "_sort_id": latest.id,
    }


def _approval_document_batch(
    db: Session,
    *,
    scope: str,
    staff_id: int,
    doc_type: str | None,
    before_sort_id: int | None,
) -> list[tuple[str, int, int]]:
    conditions = []
    if scope == MyApprovalScope.PENDING:
        conditions.extend((
            ApprovalRecord.action == approval_service.ApprovalAction.APPROVE,
            ApprovalRecord.status == approval_service.ApprovalStatus.PENDING,
        ))
    elif scope == MyApprovalScope.SUBMITTED:
        conditions.extend((
            ApprovalRecord.approver_id == staff_id,
            ApprovalRecord.action == approval_service.ApprovalAction.SUBMIT,
        ))
    elif scope == MyApprovalScope.PROCESSED:
        conditions.extend((
            ApprovalRecord.approver_id == staff_id,
            ApprovalRecord.action.in_([
                approval_service.ApprovalAction.APPROVE,
                approval_service.ApprovalAction.REJECT,
                approval_service.ApprovalAction.UNAUDIT,
            ]),
        ))
    else:
        raise BizException(ErrorCode.BAD_REQUEST, "审批范围仅支持 pending/submitted/processed")
    if doc_type is not None:
        conditions.append(ApprovalRecord.doc_type == doc_type)

    candidates = (
        select(ApprovalRecord.doc_type, ApprovalRecord.doc_id)
        .where(*conditions)
        .group_by(ApprovalRecord.doc_type, ApprovalRecord.doc_id)
        .subquery("approval_document_candidates")
    )
    latest_record = ApprovalRecord.__table__.alias("latest_approval_record")
    latest_time = func.coalesce(
        latest_record.c.audit_time, latest_record.c.created_at,
    )
    sort_id = (
        select(latest_record.c.id)
        .where(
            latest_record.c.doc_type == candidates.c.doc_type,
            latest_record.c.doc_id == candidates.c.doc_id,
        )
        .order_by(
            case((latest_time.is_(None), 1), else_=0),
            latest_time.desc(),
            latest_record.c.id.desc(),
        )
        .limit(1)
        .correlate(candidates)
        .scalar_subquery()
    )
    statement = select(
        candidates.c.doc_type, candidates.c.doc_id, sort_id.label("sort_id"),
    ).select_from(candidates)
    if before_sort_id is not None:
        statement = statement.where(sort_id < before_sort_id)
    statement = statement.order_by(sort_id.desc()).limit(
        _APPROVAL_LIST_SCAN_BATCH_SIZE
    )
    return [
        (row.doc_type, int(row.doc_id), int(row.sort_id))
        for row in db.execute(statement)
    ]


def _legacy_approval_representative_id_query(
    doc_keys: list[tuple[str, int]],
):
    """构建不依赖窗口函数的每单据至多7个代表留痕ID查询。"""
    latest_time = func.coalesce(ApprovalRecord.audit_time, ApprovalRecord.created_at)
    orderings = (
        (
            case((latest_time.is_(None), 1), else_=0),
            latest_time.desc(), ApprovalRecord.id.desc(),
        ),
        (
            case((ApprovalRecord.flow_id.is_(None), 1), else_=0),
            ApprovalRecord.id.desc(),
        ),
        (
            case((ApprovalRecord.action == approval_service.ApprovalAction.SUBMIT, 0), else_=1),
            ApprovalRecord.step, ApprovalRecord.id,
        ),
        (
            case((or_(ApprovalRecord.doc_no.is_(None), ApprovalRecord.doc_no == ""), 1), else_=0),
            ApprovalRecord.step, ApprovalRecord.id,
        ),
        (
            case((ApprovalRecord.status == approval_service.ApprovalStatus.PENDING, 0), else_=1),
            ApprovalRecord.step.desc(), ApprovalRecord.id.desc(),
        ),
        (
            case((and_(
                ApprovalRecord.action == approval_service.ApprovalAction.APPROVE,
                ApprovalRecord.status == approval_service.ApprovalStatus.PENDING,
            ), 0), else_=1),
            ApprovalRecord.step.desc(), ApprovalRecord.id.desc(),
        ),
        (ApprovalRecord.step, ApprovalRecord.id),
    )
    branches = []
    branch_number = 0
    for doc_type, doc_id in doc_keys:
        for ordering in orderings:
            limited = (
                select(ApprovalRecord.id.label("record_id"))
                .where(
                    ApprovalRecord.doc_type == doc_type,
                    ApprovalRecord.doc_id == doc_id,
                )
                .order_by(*ordering)
                .limit(1)
                .subquery(f"approval_rep_{branch_number}")
            )
            branches.append(select(limited.c.record_id))
            branch_number += 1
    return union_all(*branches)


def _approval_list_representative_records(
    db: Session,
    doc_keys: list[tuple[str, int]],
    *,
    use_window_functions: bool | None = None,
) -> list[ApprovalRecord]:
    if use_window_functions is None:
        dialect = db.get_bind().dialect
        version = getattr(dialect, "server_version_info", None)
        use_window_functions = (
            dialect.name != "mysql"
            or (version is not None and version >= (8, 0))
        )

    if use_window_functions:
        partition = (ApprovalRecord.doc_type, ApprovalRecord.doc_id)
        latest_time = func.coalesce(ApprovalRecord.audit_time, ApprovalRecord.created_at)
        ranks = (
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((latest_time.is_(None), 1), else_=0),
                    latest_time.desc(), ApprovalRecord.id.desc(),
                ),
            ).label("latest_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((ApprovalRecord.flow_id.is_(None), 1), else_=0),
                    ApprovalRecord.id.desc(),
                ),
            ).label("flow_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((ApprovalRecord.action == approval_service.ApprovalAction.SUBMIT, 0), else_=1),
                    ApprovalRecord.step, ApprovalRecord.id,
                ),
            ).label("submit_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((or_(ApprovalRecord.doc_no.is_(None), ApprovalRecord.doc_no == ""), 1), else_=0),
                    ApprovalRecord.step, ApprovalRecord.id,
                ),
            ).label("doc_no_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((ApprovalRecord.status == approval_service.ApprovalStatus.PENDING, 0), else_=1),
                    ApprovalRecord.step.desc(), ApprovalRecord.id.desc(),
                ),
            ).label("pending_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(
                    case((and_(
                        ApprovalRecord.action == approval_service.ApprovalAction.APPROVE,
                        ApprovalRecord.status == approval_service.ApprovalStatus.PENDING,
                    ), 0), else_=1),
                    ApprovalRecord.step.desc(), ApprovalRecord.id.desc(),
                ),
            ).label("approver_pending_rank"),
            func.row_number().over(
                partition_by=partition,
                order_by=(ApprovalRecord.step, ApprovalRecord.id),
            ).label("first_rank"),
        )
        ranked = (
            select(ApprovalRecord.id, *ranks)
            .where(tuple_(*partition).in_(doc_keys))
            .subquery("approval_list_record_ranks")
        )
        representative_ids = select(ranked.c.id).where(or_(
            ranked.c.latest_rank == 1,
            ranked.c.flow_rank == 1,
            ranked.c.submit_rank == 1,
            ranked.c.doc_no_rank == 1,
            ranked.c.pending_rank == 1,
            ranked.c.approver_pending_rank == 1,
            ranked.c.first_rank == 1,
        ))
        return db.scalars(
            select(ApprovalRecord)
            .where(ApprovalRecord.id.in_(representative_ids))
            .order_by(
                ApprovalRecord.doc_type, ApprovalRecord.doc_id,
                ApprovalRecord.step, ApprovalRecord.id,
            )
        ).all()

    # MySQL 5.7 has no window functions: seven indexed LIMIT 1 branches per
    # document are unioned in small batches, keeping app memory and SQL size bounded.
    selected: list[ApprovalRecord] = []
    query_batch_size = 25
    for start in range(0, len(doc_keys), query_batch_size):
        representative_ids = _legacy_approval_representative_id_query(
            doc_keys[start:start + query_batch_size],
        ).subquery("approval_list_legacy_record_ids")
        selected.extend(db.scalars(
            select(ApprovalRecord)
            .where(ApprovalRecord.id.in_(select(representative_ids.c.record_id)))
            .order_by(
                ApprovalRecord.doc_type, ApprovalRecord.doc_id,
                ApprovalRecord.step, ApprovalRecord.id,
            )
        ).all())
    return selected


def _approval_list_record_summaries(
    db: Session,
    candidates: list[tuple[str, int, int]],
) -> tuple[dict[tuple[str, int], list[ApprovalRecord]], dict[tuple[str, int], dict]]:
    """每个候选单据只取最多7条代表性留痕，状态统计在SQL聚合。"""
    if not candidates:
        return {}, {}

    doc_keys = [(doc_type, doc_id) for doc_type, doc_id, _ in candidates]
    partition = (ApprovalRecord.doc_type, ApprovalRecord.doc_id)
    representative_records = _approval_list_representative_records(db, doc_keys)

    records_by_doc: dict[tuple[str, int], list[ApprovalRecord]] = {}
    for record in representative_records:
        records_by_doc.setdefault((record.doc_type, record.doc_id), []).append(record)

    approved_count = func.sum(case((and_(
        ApprovalRecord.status == approval_service.ApprovalStatus.APPROVED,
        ApprovalRecord.status != approval_service.ApprovalStatus.INVALID,
        ApprovalRecord.step > 0,
    ), 1), else_=0))
    state_rows = db.execute(
        select(
            ApprovalRecord.doc_type,
            ApprovalRecord.doc_id,
            func.sum(case((
                ApprovalRecord.status != approval_service.ApprovalStatus.INVALID,
                1,
            ), else_=0)).label("effective_count"),
            approved_count.label("approved_count"),
            func.max(case((
                and_(
                    ApprovalRecord.status == approval_service.ApprovalStatus.REJECTED,
                    ApprovalRecord.status != approval_service.ApprovalStatus.INVALID,
                ),
                1,
            ), else_=0)).label("has_rejection"),
        )
        .where(tuple_(*partition).in_(doc_keys))
        .group_by(ApprovalRecord.doc_type, ApprovalRecord.doc_id)
    ).all()
    state_counts = {
        (row.doc_type, int(row.doc_id)): row
        for row in state_rows
    }

    doc_types = {doc_type for doc_type, _ in doc_keys}
    first_flow_ids = db.execute(
        select(ApprovalFlow.doc_type, func.min(ApprovalFlow.id))
        .where(ApprovalFlow.doc_type.in_(doc_types))
        .group_by(ApprovalFlow.doc_type)
    ).all()
    first_flows = {
        doc_type: get_flow_by_id(db, int(flow_id))
        for doc_type, flow_id in first_flow_ids
        if flow_id is not None
    }
    states_by_doc: dict[tuple[str, int], dict] = {}
    for key in doc_keys:
        doc_type, doc_id = key
        records = records_by_doc.get(key, [])
        counts = state_counts.get(key)
        if counts is None:
            continue

        state_flow = first_flows.get(doc_type)
        if state_flow is None:
            first_record = records[0] if records else None
            if first_record is not None and first_record.flow_id is not None:
                state_flow = get_flow_by_id(db, first_record.flow_id)
        try:
            total_steps = len(approval_service.steps_of(state_flow)) if state_flow else 0
        except BizException:
            states_by_doc[key] = {
                "status": "UNKNOWN", "current_step": None,
                "total_steps": 0, "approved_steps": [],
            }
            continue

        pending_record = max((
            record for record in records
            if record.status == approval_service.ApprovalStatus.PENDING
        ), key=lambda record: (record.step, record.id), default=None)
        effective_count = int(counts.effective_count or 0)
        if effective_count == 0:
            state = {
                "status": approval_service.DocApprovalState.NOT_SUBMITTED,
                "current_step": None,
                "total_steps": total_steps,
                "approved_steps": [],
            }
        elif pending_record is not None:
            state = {
                "status": approval_service.DocApprovalState.PENDING,
                "current_step": pending_record.step,
                "total_steps": total_steps,
                "approved_steps": [],
            }
        elif counts.has_rejection:
            state = {
                "status": approval_service.DocApprovalState.REJECTED,
                "current_step": None,
                "total_steps": total_steps,
                "approved_steps": [],
            }
        elif total_steps > 0 and int(counts.approved_count or 0) >= total_steps:
            state = {
                "status": approval_service.DocApprovalState.APPROVED,
                "current_step": None,
                "total_steps": total_steps,
                "approved_steps": [],
            }
        else:
            state = {
                "status": approval_service.DocApprovalState.PENDING,
                "current_step": None,
                "total_steps": total_steps,
                "approved_steps": [],
            }
        states_by_doc[key] = state

    return records_by_doc, states_by_doc


def list_my_approvals(
    db: Session,
    *,
    staff: Staff,
    scope: str = MyApprovalScope.PENDING,
    doc_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    compact: bool = False,
) -> dict:
    """按当前员工聚合我的审批。

    待审批按审批流当前环节过滤；已提交和已处理按审批留痕中的操作人过滤。
    """
    if scope not in MyApprovalScope.ALL:
        raise BizException(
            ErrorCode.BAD_REQUEST, "审批范围仅支持 pending/submitted/processed"
        )
    if doc_type is not None:
        doc_type = _validate_doc_type(doc_type)

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    start = (page - 1) * page_size
    end = start + page_size
    total = 0
    items: list[dict] = []
    before_sort_id = None
    keyword_text = keyword.strip().lower() if keyword else None

    while True:
        batch = _approval_document_batch(
            db, scope=scope, staff_id=staff.id, doc_type=doc_type,
            before_sort_id=before_sort_id,
        )
        if not batch:
            break
        records_by_doc, states_by_doc = _approval_list_record_summaries(db, batch)
        for item_doc_type, item_doc_id, sort_id in batch:
            before_sort_id = sort_id
            doc_key = (item_doc_type, item_doc_id)
            records = records_by_doc.get(doc_key, [])
            if not records or doc_key not in states_by_doc:
                continue
            if scope == MyApprovalScope.PENDING and not _is_current_pending_approver(
                db, records, staff.id,
            ):
                continue
            try:
                item = _build_list_item(
                    db, doc_type=item_doc_type, doc_id=item_doc_id,
                    records=records, approval_state=states_by_doc.get(doc_key),
                    compact=compact,
                )
            except BizException as exc:
                if exc.code != ErrorCode.NOT_FOUND:
                    raise
                # 不返回已删除、跨公司或无归属单据的编号、审批意见、total。
                continue
            if keyword_text:
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        item["doc_no"],
                        item["doc_type_label"],
                        item["flow_name"],
                        item["submitted_by_name"],
                    )
                ).lower()
                if keyword_text not in haystack:
                    continue
            if start <= total < end:
                items.append(item)
            total += 1
        if len(batch) < _APPROVAL_LIST_SCAN_BATCH_SIZE:
            break

    for item in items:
        item.pop("_sort_id", None)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "scope": scope,
        "items": items,
    }


def _can_view(
    db: Session, records: list[ApprovalRecord], staff_id: int
) -> bool:
    if any(record.approver_id == staff_id for record in records):
        return True
    _, _, _, step_config = _current_approval(db, records)
    return step_config is not None and staff_id in step_config.approver_ids


def _get_my_approval_detail_compact(
    db: Session, *, doc_type: str, doc_id: int, staff: Staff,
) -> dict:
    """MCP审批详情只取固定摘要和101条留痕哨兵行，不载入意见/原因列。"""
    record_rows = db.execute(
        select(
            ApprovalRecord.id, ApprovalRecord.step, ApprovalRecord.action,
            ApprovalRecord.status, ApprovalRecord.audit_time,
            ApprovalRecord.created_at,
        )
        .where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
        )
        .order_by(ApprovalRecord.step, ApprovalRecord.id)
        .limit(101)
    ).all()
    if not record_rows:
        raise BizException(ErrorCode.NOT_FOUND, "单据尚未提交审批")

    participated = db.scalar(
        select(ApprovalRecord.id).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.approver_id == staff.id,
        ).limit(1)
    ) is not None
    flow_id = db.scalar(
        select(ApprovalRecord.flow_id).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.flow_id.is_not(None),
        ).order_by(ApprovalRecord.id.desc()).limit(1)
    )
    flow = get_flow_by_id(db, flow_id) if flow_id is not None else None
    steps = approval_service.steps_of(flow) if flow is not None else []
    pending = db.execute(
        select(ApprovalRecord.step).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.action == approval_service.ApprovalAction.APPROVE,
            ApprovalRecord.status == approval_service.ApprovalStatus.PENDING,
        ).order_by(ApprovalRecord.step.desc(), ApprovalRecord.id.desc()).limit(1)
    ).first()
    step_config = _step_of_flow(steps, pending.step) if pending is not None else None
    can_approve = bool(
        pending is not None
        and step_config is not None
        and staff.id in step_config.approver_ids
    )
    if not participated and not can_approve:
        raise BizException(ErrorCode.FORBIDDEN, "无权查看该审批单据")

    detail = _load_detail(db, doc_type, doc_id, with_items=False)
    aggregate = db.execute(
        select(
            func.sum(case((
                ApprovalRecord.status != approval_service.ApprovalStatus.INVALID,
                1,
            ), else_=0)).label("effective_count"),
            func.sum(case((and_(
                ApprovalRecord.status == approval_service.ApprovalStatus.APPROVED,
                ApprovalRecord.step > 0,
            ), 1), else_=0)).label("approved_count"),
            func.max(case((and_(
                ApprovalRecord.status == approval_service.ApprovalStatus.REJECTED,
                ApprovalRecord.status != approval_service.ApprovalStatus.INVALID,
            ), 1), else_=0)).label("has_rejection"),
        ).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
        )
    ).one()

    state_flow_id = db.scalar(
        select(ApprovalFlow.id)
        .where(ApprovalFlow.doc_type == doc_type)
        .order_by(ApprovalFlow.id)
        .limit(1)
    )
    if state_flow_id is None:
        state_flow_id = db.scalar(
            select(ApprovalRecord.flow_id).where(
                ApprovalRecord.doc_type == doc_type,
                ApprovalRecord.doc_id == doc_id,
            ).order_by(ApprovalRecord.step, ApprovalRecord.id).limit(1)
        )
    state_flow = get_flow_by_id(db, state_flow_id) if state_flow_id is not None else None
    total_steps = len(approval_service.steps_of(state_flow)) if state_flow else 0
    state_pending = db.execute(
        select(ApprovalRecord.step).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.status == approval_service.ApprovalStatus.PENDING,
        ).order_by(ApprovalRecord.step.desc(), ApprovalRecord.id.desc()).limit(1)
    ).first()
    approved_steps = db.scalars(
        select(ApprovalRecord.step).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.status == approval_service.ApprovalStatus.APPROVED,
            ApprovalRecord.step > 0,
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
        ).order_by(ApprovalRecord.step, ApprovalRecord.id).limit(101)
    ).all()
    effective_count = int(aggregate.effective_count or 0)
    if effective_count == 0:
        state_status = approval_service.DocApprovalState.NOT_SUBMITTED
        current_step = None
    elif state_pending is not None:
        state_status = approval_service.DocApprovalState.PENDING
        current_step = state_pending.step
    elif aggregate.has_rejection:
        state_status = approval_service.DocApprovalState.REJECTED
        current_step = None
    elif total_steps > 0 and int(aggregate.approved_count or 0) >= total_steps:
        state_status = approval_service.DocApprovalState.APPROVED
        current_step = None
    else:
        state_status = approval_service.DocApprovalState.PENDING
        current_step = None
    state = {
        "status": state_status,
        "current_step": current_step,
        "total_steps": total_steps,
        "approved_steps": list(approved_steps[:100]),
        "approved_steps_truncated": len(approved_steps) > 100,
    }

    unaudit_allowed = False
    if state_status == approval_service.DocApprovalState.APPROVED and doc_type in _UNAUDIT_HANDLERS:
        unaudit_allowed = db.scalar(
            select(ApprovalRecord.id).where(
                ApprovalRecord.doc_type == doc_type,
                ApprovalRecord.doc_id == doc_id,
                ApprovalRecord.approver_id == staff.id,
                or_(
                    ApprovalRecord.action == approval_service.ApprovalAction.SUBMIT,
                    and_(
                        ApprovalRecord.action == approval_service.ApprovalAction.APPROVE,
                        ApprovalRecord.status == approval_service.ApprovalStatus.APPROVED,
                    ),
                ),
            ).limit(1)
        ) is not None

    doc_no = db.scalar(
        select(ApprovalRecord.doc_no).where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.doc_no.is_not(None),
            ApprovalRecord.doc_no != "",
        ).order_by(ApprovalRecord.step, ApprovalRecord.id).limit(1)
    )
    if not doc_no:
        doc_no = _doc_no([], detail, doc_type)

    records = [
        {
            "id": row.id,
            "step": row.step,
            "step_name": (
                _step_of_flow(steps, row.step).name
                if _step_of_flow(steps, row.step) is not None
                else None
            ),
            "action": row.action,
            "status": row.status,
            "audit_time": row.audit_time,
            "created_at": row.created_at,
        }
        for row in record_rows
    ]
    return {
        "doc_type": doc_type,
        "doc_type_label": DOC_TYPE_LABELS.get(doc_type, doc_type),
        "doc_id": doc_id,
        "doc_no": doc_no,
        "approval": {
            "flow_name": state_flow.name if state_flow is not None else None,
            "state": state,
        },
        "flow": (
            {
                "id": flow.id,
                "name": flow.name,
                "doc_type": flow.doc_type,
                "doc_type_label": DOC_TYPE_LABELS.get(flow.doc_type, flow.doc_type),
            }
            if flow is not None
            else None
        ),
        "records": records,
        "unaudit_reasons": [],
        "can_approve": can_approve,
        "can_reject": can_approve,
        "can_unaudit": unaudit_allowed,
        "detail": detail,
    }


def get_my_approval_detail(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    compact: bool = False,
) -> dict:
    doc_type = _validate_doc_type(doc_type)
    if compact:
        return _get_my_approval_detail_compact(
            db, doc_type=doc_type, doc_id=doc_id, staff=staff,
        )
    records = list_records(db, doc_type, doc_id)
    if not records:
        raise BizException(ErrorCode.NOT_FOUND, "单据尚未提交审批")
    if not _can_view(db, records, staff.id):
        raise BizException(ErrorCode.FORBIDDEN, "无权查看该审批单据")

    detail = _load_detail(db, doc_type, doc_id, with_items=not compact)
    flow, steps, pending, step_config = _current_approval(db, records)
    approval = approval_service.get_state(db, doc_type=doc_type, doc_id=doc_id)
    state = approval["state"]
    can_approve = (
        pending is not None
        and step_config is not None
        and staff.id in step_config.approver_ids
    )
    can_unaudit = (
        state["status"] == approval_service.DocApprovalState.APPROVED
        and doc_type in _UNAUDIT_HANDLERS
        and staff.id in _unaudit_allowed_ids(records)
    )
    return {
        "doc_type": doc_type,
        "doc_type_label": DOC_TYPE_LABELS.get(doc_type, doc_type),
        "doc_id": doc_id,
        "doc_no": _doc_no(records, detail, doc_type),
        "approval": state,
        "flow": (
            {
                "id": flow.id,
                "name": flow.name,
                "doc_type": flow.doc_type,
                "doc_type_label": DOC_TYPE_LABELS.get(flow.doc_type, flow.doc_type),
            }
            if flow is not None and compact
            else _flow_manage_dict(db, flow) if flow is not None else None
        ),
        "records": [
            ({
                "id": record.id,
                "step": record.step,
                "step_name": (_step_of_flow(steps, record.step).name
                              if _step_of_flow(steps, record.step) is not None else None),
                "action": record.action,
                "status": record.status,
                "audit_time": record.audit_time,
                "created_at": record.created_at,
            } if compact else _record_dict(db, record, steps))
            for record in records
        ],
        "unaudit_reasons": [
            {
                "step": record.step,
                "approver_id": record.approver_id,
                "approver_name": (
                    _staff_brief(db, record.approver_id) or {}
                ).get("name"),
                "reason": record.reason,
                "audit_time": record.audit_time,
            }
            for record in records
            if not compact
            and record.action == approval_service.ApprovalAction.UNAUDIT
        ],
        "can_approve": can_approve,
        "can_reject": can_approve,
        "can_unaudit": can_unaudit,
        "detail": detail,
    }


def _unaudit_allowed_ids(records: list[ApprovalRecord]) -> set[int]:
    allowed: set[int] = set()
    for record in records:
        if record.action == approval_service.ApprovalAction.SUBMIT:
            if record.approver_id is not None:
                allowed.add(record.approver_id)
        elif (
            record.action == approval_service.ApprovalAction.APPROVE
            and record.status == approval_service.ApprovalStatus.APPROVED
            and record.approver_id is not None
        ):
            allowed.add(record.approver_id)
    return allowed


def _dispatch_audit(
    db: Session,
    *,
    handlers: dict[str, tuple[str, Callable[..., Any]]],
    doc_type: str,
    doc_id: int,
    staff: Staff,
    comment: str | None = None,
    reason: str | None = None,
    step: int | None = None,
) -> None:
    item = handlers.get(doc_type)
    if item is None:
        raise BizException(
            ErrorCode.CONFLICT,
            f"单据类型「{DOC_TYPE_LABELS.get(doc_type, doc_type)}」暂不支持该操作",
        )
    id_field, handler = item
    kwargs: dict[str, Any] = {id_field: doc_id, "staff": staff}
    if comment is not None:
        kwargs["comment"] = comment
    if reason is not None:
        kwargs["reason"] = reason
    if step is not None:
        kwargs["step"] = step
    handler(db, **kwargs)


def approve_my_approval(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    comment: str | None = None,
) -> dict:
    doc_type = _validate_doc_type(doc_type)
    _dispatch_audit(
        db,
        handlers=_APPROVE_HANDLERS,
        doc_type=doc_type,
        doc_id=doc_id,
        staff=staff,
        comment=comment,
    )
    return get_my_approval_detail(
        db, doc_type=doc_type, doc_id=doc_id, staff=staff
    )


def reject_my_approval(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    comment: str | None = None,
) -> dict:
    doc_type = _validate_doc_type(doc_type)
    _dispatch_audit(
        db,
        handlers=_REJECT_HANDLERS,
        doc_type=doc_type,
        doc_id=doc_id,
        staff=staff,
        comment=comment,
    )
    return get_my_approval_detail(
        db, doc_type=doc_type, doc_id=doc_id, staff=staff
    )


def unaudit_my_approval(
    db: Session,
    *,
    doc_type: str,
    doc_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
) -> dict:
    if not reason or not reason.strip():
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")
    doc_type = _validate_doc_type(doc_type)
    _dispatch_audit(
        db,
        handlers=_UNAUDIT_HANDLERS,
        doc_type=doc_type,
        doc_id=doc_id,
        staff=staff,
        reason=reason,
        step=step,
    )
    return get_my_approval_detail(
        db, doc_type=doc_type, doc_id=doc_id, staff=staff
    )
