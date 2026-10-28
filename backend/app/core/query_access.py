"""共享读取边界：功能、实时角色、数据策略和可选 MCP 令牌必须同时允许。

此模块只授权已登记的能力，不接受客户端传入的 SQL、员工、公司或权限。
业务行过滤仍由服务完成；登记能力不代表它已作为 MCP 工具发布。
"""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.data_scope import (
    QUERY_COMPANY_SCOPE_KEY,
    ScopePolicy,
    require_scope_policy,
    resolve_policy,
)
from app.core.errors import BizException, ErrorCode
from app.core.permissions import permissions_for_roles
from app.db.base import CompanyOwnedBase
from app.models.org import Staff
from app.models.rbac import Role, StaffRole

MCP_GRANT_KEY = "mcp_query_grant"


@event.listens_for(Session, "do_orm_execute")
def _apply_query_company_scope(execute_state) -> None:
    """对已授权查询会话中的所有公司归属模型统一注入公司谓词。"""
    company_id = execute_state.session.info.get(QUERY_COMPANY_SCOPE_KEY)
    if (
        company_id is None
        or not execute_state.is_select
        or execute_state.is_column_load
        or execute_state.is_relationship_load
    ):
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            CompanyOwnedBase,
            lambda model: model.company_id == company_id,
            include_aliases=True,
        )
    )


@dataclass(frozen=True)
class QueryGrant:
    """仅由 MCP 凭据认证器设置，不能由工具参数构造。"""

    permissions: frozenset[str]
    tools: frozenset[str]


@dataclass(frozen=True)
class Capability:
    permission: str | frozenset[str]
    roles: frozenset[str] = frozenset()
    shared_legacy: bool = False
    tool: str | frozenset[str] | None = None


# ADMIN 不受子功能角色限制，但仍受公司、数据范围和令牌限制。
CAPABILITIES = {
    "access": Capability("", tool="erp_get_my_access"),
    "dashboard": Capability(
        "module:dashboard", shared_legacy=True,
        tool="erp_dashboard_get_dashboard_overview",
    ),
    "purchase_orders": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}),
        tool=frozenset({
            "erp_search_purchase_orders", "erp_get_purchase_order",
            "erp_list_purchase_order_items",
        }),
    ),
    "sales_orders": Capability(
        "module:order", shared_legacy=True,
        tool=frozenset({
            "erp_order_list_orders", "erp_order_get_order",
            "erp_order_list_order_items", "erp_order_list_order_logs",
            "erp_ship_list_shipments", "erp_ship_list_order_shipments",
            "erp_ship_get_shipment", "erp_ship_list_shipment_items",
        }),
    ),
    "shop_catalog": Capability(
        frozenset({"module:settings", "module:order"}), shared_legacy=True,
        tool=frozenset({
            "erp_shop_list_platforms", "erp_shop_get_platform",
            "erp_shop_list_shops", "erp_shop_get_shop", "erp_shop_get_api_status",
        }),
    ),
    "purchase_returns": Capability(
        frozenset({"module:purchase", "module:stock", "module:finance"}),
        frozenset({"PURCHASER", "WAREHOUSE", "FINANCE", "QUALITY"}),
        True,
        frozenset({
            "erp_search_purchase_returns", "erp_get_purchase_return",
            "erp_list_purchase_return_items", "erp_get_purchase_return_stock_out",
            "erp_get_purchase_return_refund",
        }),
    ),
    "purchase_plans": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}), True,
        frozenset({
            "erp_search_purchase_plans", "erp_get_purchase_plan",
            "erp_list_purchase_plan_items", "erp_get_purchase_plan_creation_request",
        }),
    ),
    "purchase_creation_requests": Capability(
        "module:purchase", frozenset({"PURCHASER"}), True,
        "erp_get_purchase_plan_creation_request",
    ),
    "purchase_needs": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}), True,
        "erp_preview_purchase_needs",
    ),
    "purchase_prepays": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}),
        tool=frozenset({
            "erp_search_purchase_prepays", "erp_get_purchase_prepay",
            "erp_get_purchase_prepay_voucher",
        }),
    ),
    "purchase_invoices": Capability(
        "module:finance", frozenset({"FINANCE"}),
        tool=frozenset({
            "erp_search_purchase_invoices", "erp_get_purchase_invoice",
            "erp_get_purchase_invoice_cost_detail",
        }),
    ),
    "invoice_management": Capability(
        "module:finance", frozenset({"FINANCE"}), True,
        frozenset({"erp_invoice_list_invoices", "erp_invoice_get_invoice"}),
    ),
    "supplier_balances": Capability(
        "module:finance", frozenset({"FINANCE"}), True,
        frozenset({
            "erp_invoice_list_supplier_account_balances",
            "erp_invoice_get_supplier_account_balance",
        }),
    ),
    "logistics_catalog": Capability(
        frozenset({"module:settings", "module:order"}), shared_legacy=True,
        tool=frozenset({"erp_logistics_list_logistics", "erp_logistics_get_logistics"}),
    ),
    "freight_catalog": Capability(
        frozenset({"module:settings", "module:order"}), shared_legacy=True,
        tool=frozenset({
            "erp_logistics_list_freight_templates",
            "erp_logistics_get_freight_template",
            "erp_logistics_calculate_freight",
        }),
    ),
    "area_rule_catalog": Capability(
        frozenset({"module:settings", "module:order"}), shared_legacy=True,
        tool=frozenset({
            "erp_logistics_match_area_rule", "erp_logistics_list_area_rules",
            "erp_logistics_get_area_rule",
        }),
    ),
    "print_template_catalog": Capability(
        frozenset({"module:settings", "module:order"}), shared_legacy=True,
        tool=frozenset({
            "erp_logistics_list_print_templates",
            "erp_logistics_get_print_template",
        }),
    ),
    "order_flag_dictionary": Capability(
        "module:order", tool="erp_order_list_order_flags",
    ),
    "finance_expenses": Capability(
        "module:finance", frozenset({"FINANCE"}),
        tool=frozenset({
            "erp_search_expenses", "erp_finance_doc_get_expense",
            "erp_finance_doc_get_expense_voucher",
        }),
    ),
    "finance_incomes": Capability(
        "module:finance", frozenset({"FINANCE"}),
        tool=frozenset({
            "erp_search_incomes", "erp_finance_doc_get_income",
            "erp_finance_doc_get_income_voucher",
        }),
    ),
    "finance_payments": Capability(
        "module:finance", frozenset({"FINANCE"}), tool=frozenset({
            "erp_search_payments", "erp_finance_doc_get_payment",
            "erp_finance_doc_get_payment_voucher",
        }),
    ),
    "finance_receipts": Capability(
        "module:finance", frozenset({"FINANCE"}), tool=frozenset({
            "erp_search_receipts", "erp_finance_doc_get_receipt",
            "erp_finance_doc_get_receipt_voucher",
        }),
    ),
    "finance_settlements": Capability(
        "module:finance", frozenset({"FINANCE"}),
        tool=frozenset({
            "erp_search_settlements", "erp_finance_doc_get_settlement",
            "erp_finance_doc_get_settlement_voucher",
        }),
    ),
    "finance_accounting": Capability(
        "module:finance", frozenset({"FINANCE"}), True,
        frozenset({
            "erp_account_subject_get_subject_meta",
            "erp_account_subject_list_subjects",
            "erp_account_subject_get_subject",
            "erp_account_subject_list_project_categories",
            "erp_account_subject_get_project_category",
        }),
    ),
    "product_catalog": Capability(
        "module:product", frozenset({"PURCHASER"}),
        tool=frozenset({
            "erp_search_products", "erp_get_product", "erp_list_product_skus",
            "erp_search_combos", "erp_get_combo", "erp_list_combo_items",
            "erp_product_expand_combo",
        }),
    ),
    "product_settings": Capability(
        frozenset({"module:product", "module:settings"}),
        tool=frozenset({
            "erp_product_list_categories", "erp_product_list_brands",
            "erp_product_list_units", "erp_product_list_unit_groups",
            "erp_product_list_unit_group_items", "erp_product_list_spec_types",
            "erp_product_list_spec_values",
        }),
    ),
    "company_directory": Capability(
        "module:settings",
        tool=frozenset({
            "erp_company_get_company", "erp_company_list_departments",
            "erp_company_get_department", "erp_company_list_positions",
            "erp_company_get_position", "erp_company_list_staff",
            "erp_company_get_staff", "erp_company_get_staff_data_scope",
        }),
    ),
    "company_ip_rules": Capability(
        "module:settings", frozenset({"ADMIN"}),
        tool=frozenset({"erp_company_list_ip_rules", "erp_company_get_ip_rule"}),
    ),
    "assistant_admin": Capability(
        "settings:manage", frozenset({"ADMIN"}),
        tool=frozenset({
            "erp_assistant_get_admin_config", "erp_assistant_get_admin_usage",
        }),
    ),
    "assistant_usage": Capability(
        "", tool="erp_assistant_get_my_usage",
    ),
    "assistant_capabilities": Capability(
        "", tool="erp_assistant_capabilities",
    ),
    "supplier_catalog": Capability(
        "module:purchase", frozenset({"PURCHASER"}), True,
        frozenset({"erp_search_suppliers", "erp_get_supplier"}),
    ),
    "customer_catalog": Capability(
        "module:order", frozenset({"AFTER_SALE"}), True,
        frozenset({"erp_search_customers", "erp_get_customer"}),
    ),
    "party_accounts": Capability(
        frozenset({"module:settings", "module:purchase", "module:order"}),
        frozenset({"PURCHASER", "FINANCE", "AFTER_SALE"}), True,
        frozenset({"erp_list_supplier_accounts", "erp_list_customer_accounts"}),
    ),
    "supplier_prices": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}), True,
        "erp_list_supplier_prices",
    ),
    "after_sale": Capability(
        "module:order", frozenset({"AFTER_SALE"}), True,
        frozenset({
            "erp_search_after_sale_returns", "erp_get_after_sale_return",
            "erp_list_after_sale_return_items", "erp_search_return_registers",
            "erp_search_after_sale_exchanges", "erp_get_after_sale_exchange",
            "erp_list_after_sale_exchange_items",
        }),
    ),
    "stock_flows": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        "erp_search_stock_flows",
    ),
    "stock_checks": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_stock_check_list_stock_checks", "erp_stock_check_get_stock_check",
            "erp_stock_check_list_items",
        }),
    ),
    "stock_documents": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_stock_in_list_pending_putaways", "erp_stock_in_list_putaways",
            "erp_stock_in_list_stock_ins", "erp_stock_in_get_stock_in",
            "erp_stock_in_list_stock_in_items", "erp_stock_out_list_stock_outs",
            "erp_stock_out_get_stock_out", "erp_stock_out_list_stock_out_items",
        }),
    ),
    "stock_inventory": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_stock_query_list_stock_summary",
            "erp_stock_query_list_stock_locations",
            "erp_stock_query_list_stock_warnings",
            "erp_stock_query_list_stock_costs",
            "erp_stock_query_list_location_adjusts",
            "erp_stock_query_get_location_adjust",
            "erp_stock_query_list_location_adjust_items",
        }),
    ),
    "my_notifications": Capability(
        "", tool=frozenset({
            "erp_list_my_notifications", "erp_notification_count_unread",
            "erp_get_my_notification",
        }),
    ),
    "purchase_report": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}),
        tool=frozenset({
            "erp_report_purchase_orders", "erp_report_purchase_inbounds",
        }),
    ),
    "purchase_invoice_report": Capability(
        "module:purchase", frozenset({"FINANCE"}),
        tool="erp_report_purchase_invoices",
    ),
    "inventory_report": Capability(
        "module:purchase", frozenset({"PURCHASER", "FINANCE"}), True,
        "erp_report_inventory_in_transit",
    ),
    "stock_settings": Capability(
        "module:stock", frozenset({"WAREHOUSE"}),
        tool="erp_get_stock_settings",
    ),
    "shop_stocks": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({"erp_stock_settlement_list_shop_stocks", "erp_stock_settlement_get_shop_stock"}),
    ),
    "stock_settlements": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_stock_settlement_list_periods",
            "erp_stock_settlement_list_settlements",
            "erp_stock_settlement_list_cost_rewrite_logs",
        }),
    ),
    "transfer_queries": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_transfer_list_transfers", "erp_transfer_get_transfer",
            "erp_transfer_list_transfer_items", "erp_transfer_list_transfer_outs",
            "erp_transfer_get_transfer_out", "erp_transfer_list_transfer_out_items",
            "erp_transfer_list_transfer_ins", "erp_transfer_get_transfer_in",
            "erp_transfer_list_transfer_in_items",
        }),
    ),
    "finance_vouchers": Capability(
        "module:finance", frozenset({"FINANCE"}),
        tool=frozenset({
            "erp_voucher_list_vouchers", "erp_voucher_get_voucher",
            "erp_voucher_list_voucher_items",
        }),
    ),
    "location_capacity": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        "erp_warehouse_get_location_capacity",
    ),
    "wave_queries": Capability(
        "module:order", shared_legacy=True,
        tool=frozenset({
            "erp_wave_list_wave_strategies", "erp_wave_get_wave_strategy",
            "erp_wave_get_wave_strategy_pending_count", "erp_wave_list_waves",
            "erp_wave_get_wave", "erp_wave_list_wave_orders",
            "erp_wave_list_load_numbers",
        }),
    ),
    "warehouse_catalog": Capability(
        "module:stock", frozenset({"WAREHOUSE"}), True,
        frozenset({
            "erp_search_warehouses", "erp_get_warehouse",
            "erp_list_warehouse_zones", "erp_get_warehouse_zone",
            "erp_search_locations", "erp_get_location",
        }),
    ),
    "approval_business": Capability(
        "module:approval", shared_legacy=True,
        tool=frozenset({
            "erp_list_approval_flows", "erp_get_approval_records",
            "erp_list_approval_doc_types", "erp_list_my_approvals",
            "erp_get_my_approval_detail",
        }),
    ),
    "approval_configuration": Capability(
        "approval:configure", frozenset({"ADMIN"}), True,
        tool="erp_list_approval_flow_configs",
    ),
    "print_batch": Capability(
        "module:order", frozenset({"WAREHOUSE", "FINANCE"}), True,
        "erp_print_get_print_batch",
    ),
    "assistant_order": Capability("module:order", shared_legacy=True),
    "assistant_purchase": Capability("module:purchase", frozenset({"PURCHASER", "FINANCE"}), True),
    "assistant_inventory": Capability("module:stock", frozenset({"WAREHOUSE"}), True),
    "assistant_approval": Capability("module:approval", shared_legacy=True),
}


@dataclass(frozen=True)
class QueryAccess:
    staff: Staff
    roles: frozenset[str]
    permissions: frozenset[str]
    policy: ScopePolicy
    capability: str

    def cache_fingerprint(self) -> str:
        value = {
            "version": 1, "capability": self.capability,
            "staff": self.policy.staff_id, "company": self.policy.company_id,
            "department": self.policy.department_id, "scope": self.policy.scope_type,
            "roles": sorted(self.roles), "permissions": sorted(self.permissions),
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def current_actor_access(db: Session, staff_id: int) -> tuple[Staff, frozenset[str], frozenset[str]]:
    # populate_existing 防止同一 Session 的 identity map 保留已停用或调离的员工。
    staff = db.scalar(select(Staff).where(Staff.id == staff_id).execution_options(populate_existing=True))
    if staff is None or not staff.is_active:
        raise BizException(ErrorCode.UNAUTHORIZED, "账号不可用")
    rows = db.execute(select(Role.code, Role.company_id).join(
        StaffRole, StaffRole.role_id == Role.id,
    ).where(StaffRole.staff_id == staff.id)).all()
    if any(company_id != staff.company_id for _, company_id in rows):
        raise BizException(ErrorCode.FORBIDDEN, "角色公司归属无效")
    roles = frozenset(code for code, _ in rows)
    return staff, roles, frozenset(permissions_for_roles(roles))


def enable_company_query_scope(db: Session, company_id: int) -> None:
    """绑定已认证公司的 ORM 查询隔离；同一会话禁止切换公司。"""
    policy = require_scope_policy(db, company_id=company_id)
    existing = db.info.get(QUERY_COMPANY_SCOPE_KEY)
    if existing not in (None, policy.company_id):
        raise BizException(ErrorCode.FORBIDDEN, "查询公司范围与当前身份不一致")
    db.info[QUERY_COMPANY_SCOPE_KEY] = policy.company_id


def authorize_query(db: Session, capability: str) -> QueryAccess:
    spec = CAPABILITIES.get(capability)
    if spec is None:
        raise BizException(ErrorCode.FORBIDDEN, "查询能力未开放")
    policy = require_scope_policy(db)
    staff, roles, permissions = current_actor_access(db, policy.staff_id)
    if policy != resolve_policy(db, staff):
        raise BizException(ErrorCode.FORBIDDEN, "身份或数据范围已变化，请重新查询")
    required_permissions = (
        ({spec.permission} if spec.permission else set())
        if isinstance(spec.permission, str)
        else set(spec.permission)
    )
    if not permissions or (
        "*" not in permissions
        and required_permissions
        and not required_permissions.intersection(permissions)
    ):
        raise BizException(ErrorCode.FORBIDDEN, "无查询权限")
    if spec.roles and "ADMIN" not in roles and not roles.intersection(spec.roles):
        raise BizException(ErrorCode.FORBIDDEN, "无此子功能查询权限")
    grant = db.info.get(MCP_GRANT_KEY)
    if grant is not None:
        # 未给能力配置工具名时拒绝 MCP，不能因 API 已接入就自动对外开放。
        allowed_tools = ({spec.tool} if isinstance(spec.tool, str) else set(spec.tool or ()))
        if not isinstance(grant, QueryGrant) or not allowed_tools.intersection(grant.tools):
            raise BizException(ErrorCode.FORBIDDEN, "工具不在凭据范围内")
        grant_permissions = {
            {
                "approval:configure": "module:approval",
                "settings:manage": "module:settings",
            }.get(permission, permission)
            for permission in required_permissions if permission
        }
        if grant_permissions and not grant_permissions.intersection(grant.permissions):
            raise BizException(ErrorCode.FORBIDDEN, "功能不在凭据范围内")
        permissions = frozenset(grant.permissions if "*" in permissions else permissions & grant.permissions)
        if not permissions:
            raise BizException(ErrorCode.FORBIDDEN, "查询权限已撤销")
    # 旧表已补齐 company_id；通过 ORM 全局条件按当前公司隔离，覆盖关联、
    # 汇总和详情查询，不再因数据库中存在多个公司而拦截。
    enable_company_query_scope(db, policy.company_id)
    return QueryAccess(staff, roles, permissions, policy, capability)
