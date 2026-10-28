"""为旧业务表补充公司归属并按已验证关系回填。"""

from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


# organization/RBAC 表不重复存租户键；这些是核验过实际存在的业务表。
TENANT_TABLES = (
    "account", "account_subject", "approval_flow", "approval_record", "area_rule",
    "audit_log", "brand", "category", "combo_product", "combo_product_item",
    "customer", "delivery_list", "exchange_order", "exchange_order_item", "expense",
    "freight_template", "income", "invoice", "load_no", "location",
    "location_adjust", "location_adjust_item", "logistics", "notification_delivery_attempt",
    "order", "order_item", "order_shipment", "order_shipment_item", "payment",
    "pick_list", "platform", "print_template", "product", "product_sku",
    "project_category", "purchase_invoice", "purchase_order", "purchase_order_item",
    "purchase_plan", "purchase_plan_item", "purchase_prepay", "purchase_return",
    "purchase_return_item", "putaway", "receipt", "return_order", "return_order_item",
    "return_register", "settlement", "shop", "shop_api", "shop_stock", "spec_type",
    "spec_value", "stock_check", "stock_check_item", "stock_cost",
    "stock_cost_rewrite_log", "stock_flow", "stock_in", "stock_in_item",
    "stock_location", "stock_out", "stock_out_item", "stock_settlement", "stock_warning",
    "supplier", "supplier_price", "transfer", "transfer_in", "transfer_in_item",
    "transfer_item", "transfer_out", "transfer_out_item", "unit", "unit_group",
    "unit_group_item", "voucher", "voucher_item", "warehouse", "wave", "wave_order",
    "wave_strategy", "waybill", "zone",
)


# 每项是 (归属来源表, 当前表中的外键列)，按业务主关系优先；不在此表的记录归公司 1。
OWNERSHIP_SOURCES = {
    "account": (("supplier", "supplier_id"), ("customer", "customer_id")),
    "approval_flow": (("staff", "created_by"),),
    "approval_record": (("approval_flow", "flow_id"), ("staff", "approver_id")),
    "area_rule": (("freight_template", "freight_template_id"), ("logistics", "logistics_id")),
    "combo_product": (("staff", "created_by"), ("product", "main_product_id")),
    "combo_product_item": (("combo_product", "combo_id"),),
    "delivery_list": (("wave", "wave_id"),),
    "exchange_order": (("order", "order_id"), ("shop", "shop_id")),
    "exchange_order_item": (("exchange_order", "exchange_order_id"),),
    "expense": (("staff", "created_by"), ("customer", "customer_id")),
    "freight_template": (("logistics", "logistics_id"),),
    "income": (("staff", "created_by"), ("customer", "customer_id"), ("supplier", "supplier_id")),
    "load_no": (("wave", "wave_id"),),
    "location": (("warehouse", "warehouse_id"), ("zone", "zone_id")),
    "location_adjust": (("staff", "created_by"), ("warehouse", "warehouse_id")),
    "location_adjust_item": (("location_adjust", "adjust_id"),),
    "order": (("shop", "shop_id"),),
    "order_item": (("order", "order_id"),),
    "order_shipment": (("order", "order_id"), ("staff", "shipped_by")),
    "order_shipment_item": (("order_shipment", "shipment_id"),),
    "payment": (
        ("staff", "created_by"), ("stock_in", "stock_in_id"),
        ("purchase_invoice", "invoice_id"), ("purchase_prepay", "prepay_id"),
        ("supplier", "supplier_id"),
    ),
    "pick_list": (("wave", "wave_id"),),
    "print_template": (("logistics", "logistics_id"),),
    "product": (
        ("supplier", "default_supplier_id"), ("brand", "brand_id"),
        ("category", "category_id"), ("unit_group", "unit_group_id"),
        ("unit", "main_unit_id"),
    ),
    "product_sku": (("product", "product_id"),),
    "purchase_invoice": (("staff", "created_by"), ("supplier", "supplier_id")),
    "purchase_order": (
        ("purchase_plan", "plan_id"), ("staff", "purchaser_id"),
        ("staff", "created_by"), ("warehouse", "warehouse_id"),
        ("supplier", "supplier_id"),
    ),
    "purchase_order_item": (("purchase_order", "order_id"),),
    "purchase_plan": (("staff", "created_by"), ("supplier", "supplier_id")),
    "purchase_plan_item": (("purchase_plan", "plan_id"),),
    "purchase_prepay": (("staff", "created_by"), ("purchase_order", "order_id"), ("supplier", "supplier_id")),
    "purchase_return": (("staff", "created_by"), ("purchase_order", "order_id"), ("warehouse", "warehouse_id")),
    "purchase_return_item": (("purchase_return", "return_id"),),
    "putaway": (("staff", "operator_id"), ("location", "location_id"), ("warehouse", "warehouse_id")),
    "receipt": (
        ("staff", "created_by"), ("order", "order_id"), ("expense", "expense_id"),
        ("invoice", "invoice_id"), ("customer", "customer_id"),
    ),
    "return_order": (("order", "order_id"), ("shop", "shop_id"), ("stock_in", "stock_in_id")),
    "return_order_item": (("return_order", "return_order_id"),),
    "return_register": (("exchange_order", "exchange_order_id"), ("return_order", "return_order_id")),
    "settlement": (("staff", "created_by"), ("shop", "shop_id"), ("invoice", "receivable_id")),
    "shop": (("platform", "platform_id"), ("supplier", "supplier_id")),
    "shop_api": (("shop", "shop_id"),),
    "shop_stock": (("staff", "created_by"), ("shop", "shop_id"), ("warehouse", "warehouse_id")),
    "spec_type": (("category", "category_id"),),
    "spec_value": (("spec_type", "spec_type_id"),),
    "stock_check": (("staff", "created_by"), ("warehouse", "warehouse_id")),
    "stock_check_item": (("stock_check", "check_id"),),
    "stock_cost": (("product", "product_id"),),
    "stock_cost_rewrite_log": (("staff", "created_by"), ("product", "product_id")),
    "stock_flow": (("warehouse", "warehouse_id"), ("product", "product_id")),
    "stock_in": (("purchase_order", "purchase_order_id"), ("staff", "created_by"), ("warehouse", "warehouse_id")),
    "stock_in_item": (("stock_in", "stock_in_id"),),
    "stock_location": (("location", "location_id"),),
    "stock_out": (("purchase_return", "purchase_return_id"), ("staff", "created_by"), ("warehouse", "warehouse_id")),
    "stock_out_item": (("stock_out", "stock_out_id"),),
    "stock_settlement": (("product", "product_id"),),
    "stock_warning": (("product", "product_id"),),
    "supplier_price": (("supplier", "supplier_id"), ("product", "product_id")),
    "transfer": (("staff", "created_by"), ("warehouse", "from_warehouse_id"), ("warehouse", "to_warehouse_id")),
    "transfer_in": (("transfer", "transfer_id"), ("staff", "created_by"), ("warehouse", "warehouse_id")),
    "transfer_in_item": (("transfer_in", "transfer_in_id"),),
    "transfer_item": (("transfer", "transfer_id"),),
    "transfer_out": (("transfer", "transfer_id"), ("staff", "created_by"), ("warehouse", "warehouse_id")),
    "transfer_out_item": (("transfer_out", "transfer_out_id"),),
    "unit_group": (("unit", "main_unit_id"),),
    "unit_group_item": (("unit_group", "unit_group_id"),),
    "voucher": (("staff", "created_by"), ("staff", "auditor_id")),
    "voucher_item": (("voucher", "voucher_id"),),
    "wave": (("staff", "created_by"), ("wave_strategy", "strategy_id")),
    "wave_order": (("wave", "wave_id"),),
    "wave_strategy": (("warehouse", "warehouse_id"), ("logistics", "logistics_id")),
    "waybill": (("order", "order_id"),),
    "zone": (("warehouse", "warehouse_id"),),
    "notification_delivery_attempt": (("notification_inbox", "inbox_id"),),
}


def _ordered_tables() -> list[str]:
    pending = set(OWNERSHIP_SOURCES)
    parents = {
        parent
        for links in OWNERSHIP_SOURCES.values()
        for parent, _ in links
    }
    resolved = (set(TENANT_TABLES) | parents) - pending
    ordered: list[str] = []
    while pending:
        ready = sorted(
            table for table in pending
            if all(parent in resolved for parent, _ in OWNERSHIP_SOURCES[table])
        )
        if not ready:
            raise RuntimeError(f"公司归属关系存在循环依赖: {sorted(pending)}")
        for table in ready:
            ordered.append(table)
            resolved.add(table)
            pending.remove(table)
    return ordered


def _validate_schema(bind) -> None:
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    missing = sorted(set(TENANT_TABLES) - existing_tables)
    if missing:
        raise RuntimeError(f"公司归属迁移缺少目标表: {missing}")
    planned = set(TENANT_TABLES)
    for table, links in OWNERSHIP_SOURCES.items():
        columns = {column["name"] for column in inspector.get_columns(table)}
        foreign_keys = {
            (column, fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
            for column in fk["constrained_columns"]
        }
        for parent, column in links:
            parent_columns = {c["name"] for c in inspector.get_columns(parent)}
            if column not in columns or "id" not in parent_columns:
                raise RuntimeError(f"公司归属关系列不存在: {table}.{column} -> {parent}.id")
            if parent not in planned and "company_id" not in parent_columns:
                raise RuntimeError(f"公司归属来源表没有 company_id: {parent}")
            if (column, parent) not in foreign_keys:
                raise RuntimeError(f"公司归属来源不是已验证外键: {table}.{column} -> {parent}.id")
    _ordered_tables()


def _source_expression(table: str) -> str:
    lookups = [
        f"(SELECT owner_{index}.company_id FROM `{parent}` owner_{index} "
        f"WHERE owner_{index}.id = t.`{column}` LIMIT 1)"
        for index, (parent, column) in enumerate(OWNERSHIP_SOURCES[table])
    ]
    return f"COALESCE({', '.join(lookups)}, 1)"


def upgrade() -> None:
    bind = op.get_bind()
    _validate_schema(bind)
    inspector = sa.inspect(bind)
    before_counts = {
        table: bind.execute(sa.text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        for table in TENANT_TABLES
    }
    if any(before_counts.values()) and bind.execute(
        sa.text("SELECT id FROM company WHERE id = 1")
    ).first() is None:
        raise RuntimeError("存在旧业务记录但缺少公司 ID 1，不能按约定回填")
    for table in TENANT_TABLES:
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "company_id" not in columns:
            op.add_column(table, sa.Column("company_id", sa.BigInteger(), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        index_name = f"ix_{table}_company_id"
        if index_name not in indexes:
            op.create_index(index_name, table, ["company_id"])

    # 先应用兜底，再按经过 FK 校验的父子关系覆盖；不改动任何原业务字段。
    for table in TENANT_TABLES:
        bind.execute(sa.text(f"UPDATE `{table}` SET company_id = 1"))
    for table in _ordered_tables():
        bind.execute(sa.text(
            f"UPDATE `{table}` AS t SET company_id = {_source_expression(table)}"
        ))

    for table, expected_count in before_counts.items():
        count, unassigned = bind.execute(sa.text(
            f"SELECT COUNT(*), SUM(company_id IS NULL) FROM `{table}`"
        )).one()
        if count != expected_count or (unassigned or 0) != 0:
            raise RuntimeError(f"公司归属回填校验失败: {table}")
        invalid_company = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM `{table}` t LEFT JOIN company c "
            "ON c.id = t.company_id WHERE c.id IS NULL"
        )).scalar_one()
        if invalid_company:
            raise RuntimeError(f"公司归属引用不存在的公司: {table}")


def downgrade() -> None:
    """回退仅删除本迁移新增的归属列/索引，不改动原业务字段或记录。"""
    for table in reversed(TENANT_TABLES):
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
