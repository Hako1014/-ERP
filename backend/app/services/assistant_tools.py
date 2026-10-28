"""TASK-079：AI 助手固定注册、逐次鉴权的只读业务查询工具。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, require_scope_policy
from app.core.errors import BizException
from app.core.query_access import authorize_query
from app.integrations.assistant_provider import ProviderToolCall
from app.models.org import Company, Staff
from app.models.order import Order, OrderItem
from app.models.purchase import PurchaseOrder
from app.services import (
    approval_manage_service,
    metrics_service,
    order_service,
    purchase_order_service,
    stock_query_service,
)

MAX_TOOL_ARGUMENT_CHARS = 8_000
MAX_TOOL_CALLS_PER_TURN = 3
MAX_TOOL_TURNS = 3
MAX_RESULT_ROWS = 8
MAX_DETAIL_ITEMS = 10


def single_company_draft_supported(db: Session) -> bool:
    """快捷录入仍依赖未完全改造为公司归属过滤的主数据查询。"""
    return int(db.scalar(select(func.count(Company.id))) or 0) == 1

logger = logging.getLogger(__name__)


class SelectedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    doc_type: Literal["order", "purchase_order"]
    doc_id: int = Field(gt=0)


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _EmptyArguments(_ToolArguments):
    pass


class _ApprovalArguments(_ToolArguments):
    scope: Literal["pending", "submitted", "processed"] = "pending"


class _MetricArguments(_ToolArguments):
    metric: Literal["today_order_count", "stock_warning_count"]


_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "order_lookup": {
        "module": "order",
        "title": "订单查询",
        "arguments": _EmptyArguments,
        "schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "purchase_order_lookup": {
        "module": "purchase",
        "title": "采购订单查询",
        "arguments": _EmptyArguments,
        "schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "inventory_lookup": {
        "module": "stock",
        "title": "库存与库位查询",
        "arguments": _EmptyArguments,
        "schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "my_approval_tasks": {
        "module": "approval",
        "title": "本人审批记录",
        "arguments": _ApprovalArguments,
        "schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["pending", "submitted", "processed"]}
            },
            "additionalProperties": False,
        },
    },
    "authorized_metric": {
        "module": "dashboard",
        "title": "授权看板指标",
        "arguments": _MetricArguments,
        "schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["today_order_count", "stock_warning_count"],
                }
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
}


class ToolRejected(Exception):
    """查询必须拒绝时的内部信号；不携带敏感参数或数据库详情。"""


def tool_definitions(
    permissions: list[str],
    selected_document: SelectedDocument | None = None,
) -> list[dict[str, Any]]:
    allowed = set(permissions)
    has_all = "*" in allowed
    result = []
    for name, spec in _TOOL_SPECS.items():
        if name == "order_lookup" and (
            selected_document is None or selected_document.doc_type != "order"
        ):
            continue
        if name == "purchase_order_lookup" and (
            selected_document is None or selected_document.doc_type != "purchase_order"
        ):
            continue
        if has_all or f"module:{spec['module']}" in allowed:
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": _tool_description(name),
                        "parameters": spec["schema"],
                    },
                }
            )
    return result


def _tool_description(name: str) -> str:
    descriptions = {
        "order_lookup": "查询当前页面所选订单的状态及最多 10 条商品数量；不返回单号、商品/客户身份、联系方式、地址或金额。工具不接收任何单据标识，后端只在本地使用所选记录并重新鉴权。",
        "purchase_order_lookup": "查询当前页面所选采购订单的状态和累计到货数量；必须遵守本人/部门/公司范围，不返回单号、商品/供应商身份、订购数量、价格、金额、联系方式或银行信息。工具不接收任何单据标识，后端只在本地使用所选记录并重新鉴权。",
        "inventory_lookup": "读取当前公司库存数量汇总及库存预警数量；不返回商品、仓库身份、成本或金额。",
        "my_approval_tasks": "只查询当前登录员工参与的审批记录，最多 8 条；不返回审批意见、金额或完整单据内容。",
        "authorized_metric": "读取当前账号可访问的首页确定性非财务指标；仅支持今日订单数或库存预警 SKU 数。",
    }
    return descriptions[name]


def _parse_arguments(raw: str, argument_model: type[_ToolArguments]) -> _ToolArguments:
    if not isinstance(raw, str) or len(raw) > MAX_TOOL_ARGUMENT_CHARS:
        raise ToolRejected

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw, object_pairs_hook=no_duplicate_keys)
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be an object")
        return argument_model.model_validate(parsed)
    except (json.JSONDecodeError, ValueError, ValidationError):
        raise ToolRejected from None


def sanitized_tool_call_for_history(call: ProviderToolCall) -> dict[str, str]:
    """仅把已知工具的规范化参数放回模型上下文，丢弃拒绝参数及未知工具名。"""
    spec = _TOOL_SPECS.get(call.name)
    if spec is None:
        return {"name": "unregistered_tool", "arguments": "{}"}
    try:
        args = _parse_arguments(call.arguments, spec["arguments"])
    except ToolRejected:
        return {"name": call.name, "arguments": "{}"}
    return {
        "name": call.name,
        "arguments": json.dumps(
            args.model_dump(exclude_unset=True),
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def _audit_tool(
    db: Session,
    *,
    staff: Staff,
    name: str,
    allowed: bool,
    scope: str = "未执行查询",
) -> None:
    write_audit(
        db,
        action="READ" if allowed else "READ_DENIED",
        target_type="assistant_tool",
        target_id=name if name in _TOOL_SPECS else "unregistered",
        summary="助手只读查询" if allowed else "助手只读查询拒绝",
        detail={"tool": name if name in _TOOL_SPECS else "unregistered", "scope": scope},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.commit()


def _authorize(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    module: str,
) -> tuple[Staff, str]:
    capability = {
        "order": "assistant_order", "purchase": "assistant_purchase",
        "stock": "assistant_inventory", "approval": "assistant_approval",
        "dashboard": "dashboard",
    }.get(module)
    try:
        require_scope_policy(db, staff_id=staff_id, company_id=company_id)
        access = authorize_query(db, capability)
    except (BizException, RuntimeError):
        raise ToolRejected from None
    staff, policy = access.staff, access.policy
    scope_label = {
        "SELF": "本人制单范围",
        "DEPARTMENT": "本部门制单范围",
        "COMPANY": "本公司制单范围",
    }.get(policy.scope_type)
    if scope_label is None:
        raise ToolRejected
    if module == "purchase":
        return staff, scope_label
    if module == "approval":
        return staff, "仅当前员工参与的审批记录"
    return staff, "本公司共享业务数据"


def _selected_id(
    *,
    name: str,
    selected_document: SelectedDocument | None,
) -> int:
    expected_type = "order" if name == "order_lookup" else "purchase_order"
    if (
        selected_document is None
        or selected_document.doc_type != expected_type
    ):
        raise ToolRejected
    return selected_document.doc_id


def _source(name: str, scope: str, filters: dict[str, Any] | None = None) -> dict[str, str]:
    titles = {
        "order_lookup": "ERP 订单",
        "purchase_order_lookup": "ERP 采购订单",
        "inventory_lookup": "ERP 库存查询",
        "my_approval_tasks": "ERP 我的审批",
        "authorized_metric": "ERP 首页指标",
    }
    detail = f"；筛选范围：{json.dumps(filters, ensure_ascii=False, sort_keys=True)}" if filters else ""
    return {
        "type": "data",
        "title": titles[name],
        "scope": f"{scope}{detail}",
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def _order_summary(db: Session, order: Order) -> dict[str, Any]:
    rows = db.scalars(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
        .limit(MAX_DETAIL_ITEMS + 1)
    ).all()
    quantities = [row.qty for row in rows[:MAX_DETAIL_ITEMS]]
    return {
        "status": order.status,
        "audit_status": order.audit_status,
        "item_quantities": quantities,
        "items_truncated": len(rows) > MAX_DETAIL_ITEMS,
    }


def _lookup_order(db: Session, order_id: int) -> dict[str, Any]:
    policy = require_scope_policy(db)
    try:
        order = order_service.get_order_or_404(
            db, order_id, company_id=policy.company_id,
        )
    except BizException:
        # 缺少记录和不可见记录统一失败，不把业务服务的探测线索送回模型。
        raise ToolRejected from None
    if policy.scope_type != "COMPANY" and not document_visible(
        db,
        owner_id=None,
        doc_type="order",
        doc_id=order.id,
    ):
        # 订单旧模型没有 created_by；SELF/DEPARTMENT 只允许本人参与的审批单据。
        raise ToolRejected
    return _order_summary(db, order)


def _purchase_summary(order: PurchaseOrder) -> dict[str, Any]:
    return {
        "status": order.status,
        "audit_status": order.audit_status,
        "arrived_quantity": order.arrived_qty,
    }


def _lookup_purchase_order(db: Session, order_id: int) -> dict[str, Any]:
    policy = require_scope_policy(db)
    try:
        order = purchase_order_service.get_order_or_404(
            db, order_id, company_id=policy.company_id,
        )
    except BizException:
        raise ToolRejected from None
    return _purchase_summary(order)


def _inventory_summary(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
) -> dict[str, Any]:
    totals = stock_query_service.assistant_inventory_totals(
        db,
        staff_id=staff_id,
        company_id=company_id,
    )
    return {
        **totals,
        "warning_sku_count": metrics_service.inventory_warning_count(db),
    }


def _approval_summary(db: Session, staff: Staff, args: _ApprovalArguments) -> dict[str, Any]:
    result = approval_manage_service.list_my_approvals(
        db,
        staff=staff,
        scope=args.scope,
        page=1,
        page_size=MAX_RESULT_ROWS,
    )
    statuses = [row["approval_status"] for row in result["items"]]
    return {
        "scope": args.scope,
        "total_matches": result["total"],
        "approval_statuses": statuses,
    }


def _metric_summary(db: Session, args: _MetricArguments) -> dict[str, Any]:
    if args.metric == "stock_warning_count":
        value = metrics_service.inventory_warning_count(db)
        label = "库存预警 SKU 数"
        as_of = datetime.now(timezone.utc).date().isoformat()
    else:
        overview = metrics_service.dashboard_overview(db, trend_period="day")
        metric = next(
            (item for item in overview["metrics"] if item["key"] == args.metric),
            None,
        )
        if metric is None:
            raise ToolRejected
        value = metric["value"]
        label = metric["label"]
        as_of = overview["as_of"].isoformat()
    return {"metric": args.metric, "label": label, "value": value, "as_of": as_of}


def dispatch_tool_call(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    call: ProviderToolCall,
    selected_document: SelectedDocument | None = None,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """逐次读取当前身份/权限/策略，在授权后调用一个固定只读适配器。"""
    name = call.name if isinstance(call.name, str) else ""
    spec = _TOOL_SPECS.get(name)
    audit_staff = db.get(Staff, staff_id)
    if spec is None:
        db.rollback()
        if audit_staff is not None:
            _audit_tool(db, staff=audit_staff, name=name, allowed=False)
        return {"ok": False, "message": "该查询工具未开放。"}, None

    try:
        args = _parse_arguments(call.arguments, spec["arguments"])
        selected_id = None
        if name in {"order_lookup", "purchase_order_lookup"}:
            selected_id = _selected_id(
                name=name,
                selected_document=selected_document,
            )
        staff, scope = _authorize(
            db,
            staff_id=staff_id,
            company_id=company_id,
            module=spec["module"],
        )

        if name == "order_lookup":
            data = _lookup_order(db, selected_id)
        elif name == "purchase_order_lookup":
            data = _lookup_purchase_order(db, selected_id)
        elif name == "inventory_lookup":
            data = _inventory_summary(
                db,
                staff_id=staff_id,
                company_id=company_id,
            )
        elif name == "my_approval_tasks":
            data = _approval_summary(db, staff, args)
        else:
            data = _metric_summary(db, args)

        # 释放首轮读取事务后重新取当前身份与角色，撤权、停用或范围变化时丢弃结果。
        db.rollback()
        db.expire_all()
        current_staff, current_scope = _authorize(
            db,
            staff_id=staff_id,
            company_id=company_id,
            module=spec["module"],
        )
        if current_staff.id != staff.id or current_scope != scope:
            raise ToolRejected
        staff = current_staff
        scope = current_scope

        filters = args.model_dump(exclude_none=True)
        source = _source(name, scope, filters)
        _audit_tool(db, staff=staff, name=name, allowed=True, scope=scope)
        return {"ok": True, "data": data}, source
    except (ToolRejected, ValidationError):
        db.rollback()
        current_staff = db.get(Staff, staff_id)
        if current_staff is not None:
            _audit_tool(db, staff=current_staff, name=name, allowed=False)
        return {"ok": False, "message": "记录不存在或当前账号无权查看，或查询参数无效。"}, None
    except Exception as exc:
        # DB 层拒绝、审批数据损坏等情况不透出底层内容；异常仍按失败关闭处理。
        # 仅记录异常类型用于排障，避免 SQL 参数、业务数据或密钥进入日志。
        logger.warning("Assistant read-only tool failed: %s", type(exc).__name__)
        db.rollback()
        current_staff = db.get(Staff, staff_id)
        if current_staff is not None:
            _audit_tool(db, staff=current_staff, name=name, allowed=False)
        return {"ok": False, "message": "查询暂时无法完成，请使用 ERP 页面核对。"}, None
