"""固定外发字段白名单；不接受客户端传入字段集合，不透传任意嵌套对象。

页面内部结构可以更丰富。备注、附件、审批意见、银行账号和秘密没有默认出口。
这里的形状只是 TASK-099 适配器的基础，并不自动注册任何 MCP 工具。
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.errors import BizException, ErrorCode
from app.core.query_access import QueryAccess

_SCALAR_TYPES = (str, int, bool, Decimal, date, datetime)
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PURCHASE_FIELDS = frozenset({
    "id", "order_no", "status", "audit_status", "arrival_status", "total_qty",
    "arrived_qty", "difference_qty", "created_at", "updated_at",
})
_ITEM_FIELDS = frozenset({
    "id", "product_id", "product_name", "product_code", "sku_id", "spec_name",
    "spec_code", "qty", "arrived_qty", "difference_qty", "in_transit_qty",
})
_PLAN_FIELDS = frozenset({
    "id", "plan_no", "supplier_id", "supplier_name", "plan_date", "status",
    "audit_status", "total_qty", "created_at", "updated_at",
})
_PLAN_ITEM_FIELDS = frozenset({
    "id", "product_id", "product_name", "product_code", "sku_id",
    "spec_name", "spec_code", "planned_qty", "purchased_qty", "remaining_qty",
})
_PRODUCT_FIELDS = frozenset({
    "id", "name", "code", "art_no", "category_id", "category_name",
    "brand_id", "brand_name", "measure_type", "unit_group_id", "main_unit_id",
    "unit_name", "barcode", "is_gift", "is_disabled", "listed", "listed_at",
})
_STOCK_FLOW_FIELDS = frozenset({
    "id", "warehouse_id", "warehouse_name", "product_id", "product_code",
    "product_name", "sku_id", "spec_code", "spec_name", "location_id",
    "location_code", "unit_id", "unit_name", "direction", "biz_type",
    "qty", "source_no", "order_no", "flow_time", "created_at",
})
_PREPAY_FIELDS = frozenset({
    "id", "prepay_no", "supplier_id", "supplier_name", "order_id", "order_no",
    "status", "audit_status", "created_at", "updated_at",
})
_PURCHASE_INVOICE_FIELDS = frozenset({
    "id", "invoice_no", "supplier_id", "supplier_name", "invoice_type",
    "invoice_type_name", "tax_rate", "amount", "tax_amount", "net_amount",
    "write_off_amount", "write_off_status", "business_type", "status",
    "audit_status", "cost_accounted_at", "created_at", "updated_at",
})
_FINANCE_EXPENSE_FIELDS = frozenset({
    "id", "expense_no", "customer_id", "customer_name", "expense_type",
    "amount", "status", "audit_status", "receipt_id", "receipt_no",
    "created_at", "updated_at",
})
_FINANCE_INCOME_FIELDS = frozenset({
    "id", "income_no", "supplier_id", "supplier_name", "income_type", "amount",
    "write_off_amount", "remaining_amount", "write_off_status", "status",
    "audit_status", "created_at", "updated_at",
})
_FINANCE_PAYMENT_FIELDS = frozenset({
    "id", "payment_no", "supplier_id", "supplier_name", "stock_in_id",
    "stock_in_no", "invoice_id", "invoice_no", "income_id", "income_no",
    "prepay_id", "prepay_no", "payment_type", "pay_method", "amount",
    "status", "audit_status", "has_warning", "created_at", "updated_at",
})
_FINANCE_RECEIPT_FIELDS = frozenset({
    "id", "receipt_no", "customer_id", "customer_name", "order_id", "order_no",
    "invoice_id", "invoice_no", "expense_id", "expense_no", "sale_type",
    "settle_type", "amount", "status", "audit_status", "has_warning",
    "created_at", "updated_at",
})
_FINANCE_SETTLEMENT_FIELDS = frozenset({
    "id", "settlement_no", "platform_no", "settlement_date", "shop_id",
    "shop_name", "platform_name", "amount", "status", "receivable_id",
    "receivable_no", "created_at", "updated_at",
})
_STOCK_SETTING_FIELDS = frozenset({
    "id", "sync_stock_default", "shop_stock_enabled", "floating_cost_enabled",
    "off_shelf_below_warning", "updated_at",
})
_AFTER_SALE_RETURN_FIELDS = frozenset({
    "id", "return_no", "order_id", "order_no", "order_status", "shop_id",
    "shop_name", "warehouse_id", "warehouse_name", "status", "status_label",
    "return_qty", "logistics_company", "is_arrived", "is_rejected_sign",
    "goods_status", "need_return_goods", "stock_in_id", "stock_in_no",
    "stock_in_status", "audit_time", "created_at", "updated_at",
})
_AFTER_SALE_EXCHANGE_FIELDS = frozenset({
    "id", "exchange_no", "order_id", "order_no", "shop_id", "shop_name",
    "status", "status_label", "exchange_qty", "is_cod", "is_return",
    "compensate_order_no", "compensate_direction", "created_at", "updated_at",
})
_APPROVAL_DETAIL_FIELDS = {
    "purchase_plan": frozenset({
        "id", "plan_no", "supplier_id", "supplier_name", "plan_date",
        "status", "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "purchase_order": frozenset({
        "id", "order_no", "supplier_id", "supplier_name", "warehouse_id",
        "warehouse_name", "plan_id", "plan_no", "status", "audit_status",
        "arrival_status", "total_qty", "arrived_qty", "difference_qty",
        "created_at", "updated_at",
    }),
    "purchase_prepay": frozenset({
        "id", "prepay_no", "supplier_id", "supplier_name", "order_id",
        "order_no", "status", "audit_status", "created_at", "updated_at",
    }),
    "purchase_return": frozenset({
        "id", "return_no", "supplier_id", "supplier_name", "warehouse_id",
        "warehouse_name", "status", "audit_status", "total_qty",
        "returned_qty", "created_at", "updated_at",
    }),
    "purchase_invoice": frozenset({
        "id", "invoice_no", "supplier_id", "supplier_name", "invoice_type",
        "status", "audit_status", "write_off_status", "created_at", "updated_at",
    }),
    "stock_in": frozenset({
        "id", "stock_in_no", "supplier_name", "warehouse_id", "warehouse_name",
        "status", "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "stock_out": frozenset({
        "id", "stock_out_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "transfer": frozenset({
        "id", "transfer_no", "from_warehouse_id", "from_warehouse_name",
        "to_warehouse_id", "to_warehouse_name", "status", "audit_status",
        "total_qty", "created_at", "updated_at",
    }),
    "transfer_out": frozenset({
        "id", "transfer_out_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "transfer_in": frozenset({
        "id", "transfer_in_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "stock_check": frozenset({
        "id", "check_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "total_qty", "created_at", "updated_at",
    }),
    "location_adjust": frozenset({
        "id", "adjust_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "created_at", "updated_at",
    }),
    "order": frozenset({
        "id", "order_no", "shop_id", "shop_name", "warehouse_id",
        "warehouse_name", "status", "audit_status", "pay_status",
        "created_at", "updated_at",
    }),
}
_APPROVAL_DETAIL_MONEY_FIELDS = {
    "purchase_plan": frozenset({"total_amount"}),
    "purchase_order": frozenset({"total_amount", "prepay_amount"}),
    "purchase_prepay": frozenset({"prepay_amount", "write_off_amount", "unwritten_amount"}),
    "purchase_return": frozenset({"refund_amount"}),
    "purchase_invoice": frozenset({"amount", "tax_amount", "net_amount"}),
    "order": frozenset({"order_amount", "paid_amount", "freight"}),
}


def _pick(data: dict, fields: frozenset[str]) -> dict:
    if not isinstance(data, dict):
        raise BizException(ErrorCode.BAD_REQUEST, "外发数据结构无效")
    result = {}
    for key in fields:
        if key not in data:
            continue
        value = data[key]
        if value is not None and not isinstance(value, _SCALAR_TYPES):
            raise BizException(ErrorCode.BAD_REQUEST, "外发字段结构无效")
        result[key] = value
    return result


def mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(char for char in str(value) if char.isascii() and char.isdigit())
    return "****" + digits[-4:] if len(digits) > 4 else "已脱敏"


def mask_address(value: str | None) -> str | None:
    # 不从自由文本地址猜省市区，避免把门牌号误当行政区。
    return "已脱敏" if value else None


def purchase_order(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_orders":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    money = bool(access.roles & {"ADMIN", "PURCHASER", "FINANCE"}) and (
        "*" in access.permissions or "module:purchase" in access.permissions
    )
    result = _pick(data, _PURCHASE_FIELDS | (frozenset({"total_amount", "prepay_amount"}) if money else frozenset()))
    if "items" in data:
        if not isinstance(data["items"], list):
            raise BizException(ErrorCode.BAD_REQUEST, "外发明细结构无效")
        result["items"] = [
            _pick(row, _ITEM_FIELDS | (frozenset({"purchase_price", "amount"}) if money else frozenset()))
            for row in data["items"][:100]
        ]
        result["items_truncated"] = len(data["items"]) > 100
    return result


def purchase_plan(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_plans":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    money = bool(access.roles & {"ADMIN", "PURCHASER", "FINANCE"}) and (
        "*" in access.permissions or "module:purchase" in access.permissions
    )
    result = _pick(
        data,
        _PLAN_FIELDS | (frozenset({"total_amount"}) if money else frozenset()),
    )
    if "items" in data:
        if not isinstance(data["items"], list):
            raise BizException(ErrorCode.BAD_REQUEST, "外发明细结构无效")
        fields = _PLAN_ITEM_FIELDS | (
            frozenset({"purchase_price", "amount"}) if money else frozenset()
        )
        result["items"] = [_pick(row, fields) for row in data["items"]]
    return result


def purchase_plan_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_plans":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    money = bool(access.roles & {"ADMIN", "PURCHASER", "FINANCE"}) and (
        "*" in access.permissions or "module:purchase" in access.permissions
    )
    fields = _PLAN_ITEM_FIELDS | (
        frozenset({"purchase_price", "amount"}) if money else frozenset()
    )
    return _pick(data, fields)


def purchase_need(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_needs":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "product_id", "product_name", "product_code", "sku_id", "spec_name",
        "spec_code", "supplier_id", "sales_20d", "current_stock",
        "in_transit_stock", "in_progress_qty", "suggested_qty",
    })
    if "*" in access.permissions or "module:purchase" in access.permissions:
        fields |= frozenset({"purchase_price", "estimated_amount"})
    return _pick(data, fields)


def _purchase_return_can_see_money(access: QueryAccess) -> bool:
    return "*" in access.permissions or bool(
        access.permissions & {"module:purchase", "module:finance"}
    )


def purchase_return(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_returns":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "id", "return_no", "supplier_id", "supplier_name", "order_id",
        "order_no", "warehouse_id", "warehouse_name", "auto_out",
        "total_qty", "returned_qty", "refund_status", "status",
        "audit_status", "created_at", "updated_at",
    })
    if _purchase_return_can_see_money(access):
        fields |= frozenset({"refund_amount"})
    return _pick(data, fields)


def purchase_return_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_returns":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "id", "product_id", "product_name", "product_code", "art_no",
        "sku_id", "spec_name", "spec_code", "unit_id", "unit_name",
        "return_qty",
    })
    if _purchase_return_can_see_money(access):
        fields |= frozenset({"unit_price", "amount"})
    return _pick(data, fields)


def purchase_return_stock_out(data: dict | None, access: QueryAccess) -> dict | None:
    if access.capability != "purchase_returns":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if data is None:
        return None
    return _pick(data, frozenset({
        "id", "stock_out_no", "warehouse_id", "stock_out_type", "status",
        "audit_status", "off_shelf_status", "total_qty", "audit_time",
        "created_at",
    }))


def purchase_return_refund(data: dict | None, access: QueryAccess) -> dict | None:
    if access.capability != "purchase_returns":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if data is None:
        return None
    fields = frozenset({
        "id", "income_no", "supplier_id", "income_type", "status",
        "audit_status", "created_at",
    })
    if _purchase_return_can_see_money(access):
        fields |= frozenset({"amount"})
    return _pick(data, fields)


def product(data: dict, access: QueryAccess) -> dict:
    if access.capability != "product_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 商品 Service 的字典还包含售价、成本、税率、描述和图片地址；MCP只外发固定基础字段。
    result = _pick(data, _PRODUCT_FIELDS)
    if "skus" in data:
        if not isinstance(data["skus"], list):
            raise BizException(ErrorCode.BAD_REQUEST, "商品规格结构无效")
        result["skus"] = [product_sku(row, access) for row in data["skus"]]
    return result


def product_sku(data: dict, access: QueryAccess) -> dict:
    if access.capability != "product_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "product_id", "spec_name", "spec_code", "barcode", "is_active",
    }))


def combo(data: dict, access: QueryAccess) -> dict:
    if access.capability != "product_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "combo_no", "name", "main_product_id", "main_product_name",
        "main_sku_id", "main_sku_spec_code", "is_enabled",
    }))


def combo_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "product_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "combo_id", "product_id", "product_name", "sku_id",
        "sku_spec_code", "qty", "sort_order",
    }))


def combo_expansion_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "product_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "combo_id", "kind", "product_id", "product_name", "sku_id",
        "sku_spec_code", "qty", "sort_order",
    }))


def company(data: dict, access: QueryAccess) -> dict:
    if access.capability != "company_directory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({"id", "full_name", "short_name"}))


def department(data: dict, access: QueryAccess) -> dict:
    if access.capability != "company_directory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({"id", "name", "parent_id", "sort_order"}))


def position(data: dict, access: QueryAccess) -> dict:
    if access.capability != "company_directory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({"id", "name"}))


def staff_directory_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "company_directory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "name", "staff_no", "department_id", "department_name",
        "position_id", "position_name", "is_active",
    }))


def assistant_capabilities(data: dict, access: QueryAccess) -> dict:
    if access.capability != "assistant_capabilities":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    safe_reasons = {
        "company_scope_unavailable", "provider_tools_unavailable",
        "module_not_authorized", "purchase_create_not_authorized",
        "structured_provider_unavailable", "planned_v2",
    }
    features = []
    for feature in data.get("features", []):
        if not isinstance(feature, dict) or feature.get("code") not in {
            "guide", "read_query", "draft_entry",
        } or not isinstance(feature.get("enabled"), bool):
            continue
        item = {"code": feature["code"], "enabled": feature["enabled"]}
        if feature.get("reason") in safe_reasons:
            item["reason"] = feature["reason"]
        if feature["code"] == "draft_entry":
            item["document_types"] = [
                kind for kind in feature.get("document_types", [])
                if kind == "purchase_plan"
            ]
        features.append(item)
    modules = {
        "dashboard", "purchase", "product", "stock", "order",
        "approval", "finance", "settings",
    }
    return {
        "service_status": data.get("service_status")
        if data.get("service_status") in {"ready", "unconfigured", "unavailable"}
        else "unavailable",
        "provider_mode": data.get("provider_mode")
        if data.get("provider_mode") in {"deepseek", "mock", "unavailable"}
        else "unavailable",
        "modules": [module for module in data.get("modules", []) if module in modules],
        "features": features,
    }


def supplier(data: dict, access: QueryAccess) -> dict:
    if access.capability != "supplier_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 公司归属由统一查询scope限制；联系人、邮箱、备注、账户均不外发。
    result = _pick(data, frozenset({"id", "code", "name", "is_active"}))
    result["phone"] = mask_phone(data.get("phone"))
    result["address"] = mask_address(data.get("address"))
    return result


def customer(data: dict, access: QueryAccess) -> dict:
    if access.capability != "customer_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({"id", "code", "name", "is_active"}))
    result["phone"] = mask_phone(data.get("phone"))
    result["address"] = mask_address(data.get("address"))
    return result


def supplier_price(data: dict, access: QueryAccess) -> dict:
    if access.capability != "supplier_prices":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "id", "supplier_id", "product_id", "product_name", "product_code",
        "product_status", "sku_id", "sku_spec_code", "status",
    })
    if "*" in access.permissions or "module:purchase" in access.permissions:
        fields |= frozenset({"price"})
    return _pick(data, fields)


def account_subject_meta(data: dict, access: QueryAccess) -> dict:
    if access.capability != "finance_accounting":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({"seed_version", "seed_note"}))
    for field in ("subject_categories", "balance_directions"):
        rows = data.get(field)
        if not isinstance(rows, list) or len(rows) > 100:
            raise BizException(ErrorCode.BAD_REQUEST, "会计科目元数据结构无效")
        result[field] = [_pick(row, frozenset({"code", "name"})) for row in rows]
    return result


def account_subject(data: dict, access: QueryAccess) -> dict:
    if access.capability != "finance_accounting":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "code", "name", "category", "category_name",
        "balance_direction", "balance_direction_name", "is_auxiliary",
        "is_qty_amount", "measure_unit", "is_cash", "is_bank", "is_journal",
        "is_cash_equivalent", "project_category_id", "project_category_code",
        "project_category_name", "is_enabled", "created_at", "updated_at",
    }))


def project_category(data: dict, access: QueryAccess) -> dict:
    if access.capability != "finance_accounting":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "code", "name", "created_at", "updated_at",
    }))


def _mask_tracking(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    return f"{text[:2]}****{text[-2:]}" if len(text) > 4 else "已脱敏"


def after_sale_return(data: dict, access: QueryAccess) -> dict:
    if access.capability != "after_sale":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = _AFTER_SALE_RETURN_FIELDS
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields |= frozenset({"refund_amount", "freight", "freight_refunded"})
    result = _pick(data, fields)
    result["buyer_phone"] = mask_phone(data.get("buyer_phone"))
    result["tracking_no"] = _mask_tracking(data.get("tracking_no"))
    return result


def after_sale_return_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "after_sale":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "qty",
    })
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields |= frozenset({"refund_amount"})
    return _pick(data, fields)


def after_sale_exchange(data: dict, access: QueryAccess) -> dict:
    if access.capability != "after_sale":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = _AFTER_SALE_EXCHANGE_FIELDS
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields |= frozenset({"compensate_amount"})
    return _pick(data, fields)


def after_sale_exchange_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "after_sale":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "qty", "exchange_qty",
    }))


def return_register(data: dict, access: QueryAccess) -> dict:
    if access.capability != "after_sale":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({
        "id", "return_order_id", "exchange_order_id", "target_type",
        "target_id", "target_no", "logistics_company", "created_at", "updated_at",
    }))
    result["tracking_no"] = _mask_tracking(data.get("tracking_no"))
    result["sender_phone"] = mask_phone(data.get("sender_phone"))
    return result


def warehouse(data: dict, access: QueryAccess) -> dict:
    if access.capability != "warehouse_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({
        "id", "code", "name", "region", "warehouse_type", "is_enabled", "is_default",
    }))
    result["address"] = mask_address(data.get("address"))
    return result


def location(data: dict, access: QueryAccess) -> dict:
    if access.capability != "warehouse_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "warehouse_id", "zone_id", "code", "is_virtual", "is_enabled",
    }))


def stock_flow(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_flows":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 库存流水 Service 内含成本、图片与备注；这些字段不属于仓库查询外发结构。
    return _pick(data, _STOCK_FLOW_FIELDS)


def stock_check(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_checks":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "check_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "item_count", "result_entered_count",
        "current_total_qty", "actual_total_qty", "gain_total_qty",
        "loss_total_qty", "has_imported_result", "created_at", "updated_at",
    }))


def stock_check_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_checks":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "location_id", "location_code",
        "current_qty", "actual_qty", "difference_qty",
    }))


def stock_document(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_documents":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "stock_in_no", "stock_out_no", "warehouse_id", "warehouse_name",
        "stock_in_type", "status", "audit_status", "total_qty",
        "putaway_qty", "pending_putaway_qty", "item_count", "created_at",
        "updated_at", "stock_out_no", "stock_out_type", "off_shelf_status",
    }))


def stock_document_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_documents":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "location_id", "location_code", "qty",
        "putaway_qty", "pending_putaway_qty",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({"cost_price", "amount"})
    return _pick(data, frozenset(fields))


def putaway(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_documents":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "source_type", "source_id", "source_no", "warehouse_id",
        "warehouse_name", "location_id", "location_code", "product_id",
        "product_name", "product_code", "sku_id", "spec_name", "spec_code",
        "qty", "created_at",
    }))


def pending_putaway(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_documents":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "warehouse_id", "warehouse_name", "product_id", "product_name",
        "product_code", "sku_id", "spec_name", "spec_code", "received_qty",
        "putaway_qty", "pending_putaway_qty",
    }))


def stock_summary(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "warehouse_id", "warehouse_name", "product_id", "product_code",
        "product_name", "sku_id", "spec_code", "spec_name", "normal_qty",
        "defective_qty", "total_qty", "occupied_qty", "available_qty",
        "shortage_qty", "warn_qty", "is_below_warning",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({"cost_price", "stock_cost"})
    return _pick(data, frozenset(fields))


def stock_location(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "stock_location_id", "warehouse_id", "warehouse_name", "location_id",
        "location_code", "product_id", "product_code", "product_name",
        "sku_id", "spec_code", "spec_name", "status", "qty",
    }))


def stock_warning(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "product_id", "product_code", "product_name", "sku_id", "spec_code",
        "spec_name", "current_qty", "warn_qty", "is_below_warning",
    }))


def stock_cost(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {"product_id", "product_code", "product_name", "sku_id", "spec_code", "spec_name", "qty"}
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({"cost_price", "total_cost"})
    return _pick(data, frozenset(fields))


def location_adjust(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "adjust_no", "warehouse_id", "warehouse_name", "status",
        "audit_status", "item_count", "created_at", "updated_at",
    }))


def location_adjust_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_inventory":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "from_location_id", "from_location_code",
        "to_location_id", "to_location_code", "qty",
    }))


def stock_settings(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_settings":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, _STOCK_SETTING_FIELDS)


def shop_stock(data: dict, access: QueryAccess) -> dict:
    if access.capability != "shop_stocks":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "shop_id", "shop_name", "platform_id", "platform_name",
        "warehouse_id", "warehouse_name", "product_id", "product_code",
        "product_name", "sku_id", "spec_code", "spec_name",
        "exclusive_qty", "stock_qty", "occupied_qty", "available_qty",
        "shop_available_qty", "is_sync", "is_enabled", "is_effective",
        "effective_start", "effective_end",
    }))


def stock_settlement_period(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_settlements":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {"settlement_date", "item_count", "total_qty"}
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.add("total_cost")
    return _pick(data, frozenset(fields))


def stock_settlement(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_settlements":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "id", "settlement_date", "product_id", "product_code", "product_name",
        "sku_id", "spec_code", "spec_name", "qty",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({"cost_price", "total_cost"})
    return _pick(data, frozenset(fields))


def stock_cost_rewrite_log(data: dict, access: QueryAccess) -> dict:
    if access.capability != "stock_settlements":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "id", "batch_no", "period_start", "period_end", "target_type",
        "target_id", "warehouse_id", "warehouse_name", "product_id",
        "product_code", "product_name", "sku_id", "spec_code", "spec_name",
        "qty", "cost_source", "created_at",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({
            "old_cost_price", "new_cost_price", "old_total_cost", "new_total_cost",
        })
    return _pick(data, frozenset(fields))


def transfer_document(data: dict, access: QueryAccess) -> dict:
    if access.capability != "transfer_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "transfer_no", "transfer_out_no", "transfer_in_no",
        "transfer_id", "from_warehouse_id", "from_warehouse_name",
        "to_warehouse_id", "to_warehouse_name", "warehouse_id",
        "warehouse_name", "sync_stock", "status", "audit_status",
        "total_qty", "item_count", "created_at", "updated_at",
    }))


def transfer_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "transfer_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "product_id", "product_code", "product_name", "sku_id",
        "spec_code", "spec_name", "location_id", "location_code", "qty",
    }))


def voucher_document(data: dict, access: QueryAccess) -> dict:
    if access.capability != "finance_vouchers":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "voucher_word", "voucher_no", "business_date", "record_date",
        "total_debit", "total_credit", "status", "item_count", "created_at",
        "updated_at",
    }))


def voucher_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "finance_vouchers":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "subject_id", "subject_code", "subject_name", "debit", "credit",
    }))


def linked_voucher(data: dict, access: QueryAccess) -> dict:
    if access.capability not in {
        "finance_expenses", "finance_incomes", "finance_payments",
        "finance_receipts", "finance_settlements", "purchase_prepays",
    }:
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "voucher_word", "voucher_no", "business_date", "record_date",
        "total_debit", "total_credit", "status", "item_count", "created_at",
        "updated_at",
    }))


def purchase_invoice_cost(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_invoices":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "stock_in_id", "stock_in_no", "stock_in_item_id", "product_id",
        "sku_id", "qty", "original_amount", "original_cost_price",
        "allocated_amount", "new_amount", "new_cost_price",
    }))


def ip_rule(data: dict, access: QueryAccess) -> dict:
    if access.capability != "company_ip_rules":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "rule_type", "ip_address", "audit_time",
    }))


def assistant_admin_config(data: dict, access: QueryAccess) -> dict:
    if access.capability != "assistant_admin":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "enabled", "api_key_configured", "requests_per_minute",
        "concurrent_requests_per_user", "daily_requests_per_user",
        "monthly_budget",
    }))


def assistant_usage(data: dict, access: QueryAccess) -> dict:
    if access.capability not in {"assistant_admin", "assistant_usage"}:
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "request_id", "status", "prompt_tokens", "completion_tokens",
        "total_tokens", "latency_ms", "cost_status", "estimated_cost",
        "started_at", "completed_at",
    }
    if access.capability == "assistant_admin":
        fields.update({"staff_id", "staff_name"})
    return _pick(data, frozenset(fields))


def dashboard_snapshot(data: dict, access: QueryAccess) -> dict:
    if access.capability != "dashboard":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    metrics = [
        _pick(item, frozenset({"key", "label", "value", "previous_value", "unit", "change_rate"}))
        for item in data.get("metrics", [])
    ]
    trend = data.get("sales_trend") or {}
    return {
        "as_of": data.get("as_of"),
        "metrics": metrics,
        "sales_trend": {
            "period": trend.get("period"),
            "start_date": trend.get("start_date"),
            "end_date": trend.get("end_date"),
            "items": [
                _pick(item, frozenset({"period", "start_date", "end_date", "amount", "order_count"}))
                for item in trend.get("items", [])
            ],
        },
    }


def location_capacity(data: dict, access: QueryAccess) -> dict:
    if access.capability != "location_capacity":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "location_id", "capacity", "used_qty", "remaining_qty", "unlimited",
    }))


def wave_strategy(data: dict, access: QueryAccess) -> dict:
    if access.capability != "wave_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "name", "logistics_id", "logistics_name", "warehouse_id",
        "warehouse_name", "wave_order_count", "load_no_type", "sort_type",
        "is_enabled", "created_at", "updated_at",
    }))


def wave_document(data: dict, access: QueryAccess) -> dict:
    if access.capability != "wave_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "wave_no", "strategy_id", "name", "express_type",
        "total_order_count", "order_count", "load_number_count",
        "created_at", "updated_at",
    }))


def wave_order(data: dict, access: QueryAccess) -> dict:
    if access.capability != "wave_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "order_no", "warehouse_id", "warehouse_name", "logistics_id",
        "logistics_name", "status", "audit_status", "order_time", "load_no",
        "sequence",
    }))


def wave_load_number(data: dict, access: QueryAccess) -> dict:
    if access.capability != "wave_queries":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({"id", "wave_id", "load_no", "express_type"}))


def approval_records(data: dict, access: QueryAccess) -> dict:
    if access.capability != "approval_business":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise BizException(ErrorCode.BAD_REQUEST, "审批记录结构无效")
    if len(data["records"]) > 100:
        raise BizException(ErrorCode.BAD_REQUEST, "审批记录分页超出上限")
    result = _pick(data, frozenset({"doc_type", "doc_id"}))
    result["records"] = []
    for row in data["records"]:
        record = _pick(
            row,
            frozenset({"id", "step", "action", "status", "audit_time", "created_at"}),
        )
        for field in ("audit_time", "created_at"):
            value = record.get(field)
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                record[field] = value.astimezone(_BUSINESS_TIMEZONE)
        result["records"].append(record)
    return result


def approval_list_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "approval_business":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "doc_type", "doc_type_label", "doc_id", "doc_no", "business_status",
        "audit_status", "approval_status", "current_step", "total_steps",
        "flow_id", "flow_name", "submitted_at", "latest_action",
        "latest_action_status", "latest_action_at",
    }))


def approval_flow_config(data: dict, access: QueryAccess) -> dict:
    if access.capability != "approval_configuration":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({
        "id", "name", "doc_type", "doc_type_label", "created_at", "updated_at",
    }))
    steps = data.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 100:
        raise BizException(ErrorCode.BAD_REQUEST, "审批流步骤结构无效")
    result["steps"] = []
    for step in steps:
        if not isinstance(step, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "审批流步骤结构无效")
        approvers = step.get("approvers")
        approver_ids = step.get("approver_ids")
        count = len(approvers) if isinstance(approvers, list) else (
            len(approver_ids) if isinstance(approver_ids, list) else 0
        )
        result["steps"].append({
            "step": step.get("step"), "name": step.get("name"),
            "approver_count": count,
        })
    return result


def my_approval_detail(data: dict, access: QueryAccess) -> dict:
    if access.capability != "approval_business":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if not isinstance(data, dict):
        raise BizException(ErrorCode.BAD_REQUEST, "审批详情结构无效")
    doc_type = data.get("doc_type")
    detail_fields = _APPROVAL_DETAIL_FIELDS.get(doc_type)
    if detail_fields is None:
        raise BizException(ErrorCode.BAD_REQUEST, "审批单据类型不支持外发")
    result = _pick(data, frozenset({
        "doc_type", "doc_type_label", "doc_id", "doc_no", "can_approve",
        "can_reject", "can_unaudit",
    }))
    detail = data.get("detail")
    if not isinstance(detail, dict):
        raise BizException(ErrorCode.BAD_REQUEST, "审批单据结构无效")
    fields = detail_fields
    if access.permissions & frozenset({"*", "module:finance", "module:purchase"}):
        fields |= _APPROVAL_DETAIL_MONEY_FIELDS.get(doc_type, frozenset())
    result["detail"] = _pick(detail, fields)

    approval = data.get("approval")
    if not isinstance(approval, dict):
        raise BizException(ErrorCode.BAD_REQUEST, "审批状态结构无效")
    state = approval.get("state")
    state = state if isinstance(state, dict) else {}
    approved_steps = state.get("approved_steps", [])
    if not isinstance(approved_steps, list):
        raise BizException(ErrorCode.BAD_REQUEST, "已审批环节结构无效")
    result["approval"] = {
        "flow_name": approval.get("flow_name"),
        "status": state.get("status"),
        "current_step": state.get("current_step"),
        "total_steps": state.get("total_steps"),
        "approved_steps": approved_steps[:100],
        "approved_steps_truncated": (
            bool(state.get("approved_steps_truncated"))
            or len(approved_steps) > 100
        ),
    }
    flow = data.get("flow")
    if flow is not None:
        if not isinstance(flow, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "审批流结构无效")
        result["flow"] = _pick(flow, frozenset({"id", "name", "doc_type", "doc_type_label"}))
    records = data.get("records", [])
    if not isinstance(records, list):
        raise BizException(ErrorCode.BAD_REQUEST, "审批记录结构无效")
    result["records"] = [
        _pick(row, frozenset({
            "id", "step", "step_name", "action", "status", "audit_time", "created_at",
        }))
        for row in records[:100]
        if isinstance(row, dict)
    ]
    result["records_truncated"] = len(records) > 100
    return result


def _finance_pick(data: dict, access: QueryAccess, capability: str,
                  fields: frozenset[str]) -> dict:
    if access.capability != capability:
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if "*" not in access.permissions and "module:finance" not in access.permissions:
        raise BizException(ErrorCode.FORBIDDEN, "金额字段权限不足")
    return _pick(data, fields)


def purchase_prepay(data: dict, access: QueryAccess) -> dict:
    if access.capability != "purchase_prepays":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = _PREPAY_FIELDS | frozenset({
        "order_amount", "prepay_amount", "write_off_amount", "unwritten_amount",
    })
    return _pick(data, fields)


def purchase_invoice(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "purchase_invoices", _PURCHASE_INVOICE_FIELDS)


def invoice_management(data: dict, access: QueryAccess) -> dict:
    if access.capability != "invoice_management":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 纳税人识别号、申请人个人信息不进入第三方助手；仅保留业务开票摘要。
    return _pick(data, frozenset({
        "id", "invoice_no", "document_type", "invoice_title", "invoice_type",
        "invoice_type_name", "amount", "received_amount", "unreceived_amount",
        "is_received", "company", "status", "saved_at", "created_at",
        "updated_at",
    }))


def supplier_balance(data: dict, access: QueryAccess) -> dict:
    if access.capability != "supplier_balances":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = frozenset({
        "id", "supplier_id", "supplier_code", "supplier_name", "bank",
        "branch", "bank_no", "account_no", "is_default", "balance",
        "kingdee_sync_status", "balance_synced_at", "created_at", "updated_at",
    })
    result = _pick(data, fields - {"bank_no", "account_no"})
    for field in ("bank_no", "account_no"):
        value = data.get(field)
        if not value:
            result[field] = None
        else:
            normalized = "".join(character for character in str(value) if character.isalnum())
            result[field] = f"****{normalized[-4:]}" if len(normalized) > 4 else "已脱敏"
    return result


def logistics_carrier(data: dict, access: QueryAccess) -> dict:
    if access.capability != "logistics_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 联系人、手机号和地址属于个人/联系信息，不对外部助手暴露。
    return _pick(data, frozenset({
        "id", "name", "company", "is_default", "is_enabled",
    }))


def freight_template(data: dict, access: QueryAccess) -> dict:
    if access.capability != "freight_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _freight_template_projection(data, access)


def freight_calculation(data: dict, access: QueryAccess) -> dict:
    if access.capability != "freight_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "template_id", "template_name", "charge_type", "quantity_label",
        "quantity", "charge_weight", "additional_units",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({
            "base_fee", "additional_fee", "freight_fee", "operation_fee",
            "consumable_fee", "ewaybill_fee", "tax_point", "percentage_tax",
            "fixed_tax_fee", "tax_amount", "total_fee",
        })
    return _pick(data, frozenset(fields))


def _freight_template_projection(data: dict, access: QueryAccess) -> dict:
    fields = {
        "id", "name", "logistics_id", "logistics_name", "charge_type",
        "first_count", "continue_count", "first_weight", "continue_weight",
        "deduct_weight",
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        fields.update({
            "base_fee", "continue_fee", "operation_fee", "consumable_fee",
            "ewaybill_fee", "tax_fee", "tax_point",
        })
    return _pick(data, frozenset(fields))


def area_rule(data: dict, access: QueryAccess) -> dict:
    if access.capability != "area_rule_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    return _pick(data, frozenset({
        "id", "logistics_id", "logistics_name", "province", "city",
        "match_weight_min", "match_weight_max", "freight_template_id",
        "freight_template_name",
    }))


def area_rule_match(data: dict, access: QueryAccess) -> dict:
    if access.capability != "area_rule_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({
        "matched", "logistics_id", "province", "city", "weight",
    }))
    rule = data.get("area_rule")
    result["area_rule"] = area_rule(rule, access) if rule is not None else None
    template = data.get("freight_template")
    result["freight_template"] = (
        _freight_template_projection(template, access) if template is not None else None
    )
    return result


def print_template(data: dict, access: QueryAccess) -> dict:
    if access.capability != "print_template_catalog":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    # 打印模板 content 可能含脚本、收件人字段映射或内嵌敏感配置，永不对外助手暴露。
    return _pick(data, frozenset({
        "id", "name", "template_type", "logistics_id", "logistics_name",
        "paper_width", "paper_height", "is_default",
    }))


def sales_order(data: dict, access: QueryAccess) -> dict:
    if access.capability != "sales_orders":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "id", "order_no", "shop_id", "shop_name", "platform_name",
        "warehouse_id", "warehouse_name", "logistics_id", "logistics_name",
        "status", "status_label", "audit_status", "pay_status", "flags",
        "flags_label", "weight", "total_qty", "order_time", "pay_time",
        "ship_time",
    }
    if "*" in access.permissions or "module:order" in access.permissions or "module:finance" in access.permissions:
        fields.update({
            "order_amount", "paid_amount", "freight", "platform_discount",
            "seller_discount",
        })
    # 不外发买家/收货人信息、地址、运单号、买卖双方备注或自由文本。
    result = _pick(data, frozenset(fields - {"flags", "flags_label"}))
    for field in ("flags", "flags_label"):
        values = data.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise BizException(ErrorCode.BAD_REQUEST, "外发订单标识结构无效")
        result[field] = values[:16]
    return result


def sales_order_item(data: dict, access: QueryAccess) -> dict:
    if access.capability != "sales_orders":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    fields = {
        "id", "order_id", "product_id", "product_code", "product_name",
        "sku_id", "sku_name", "qty", "is_gift",
    }
    if "*" in access.permissions or "module:order" in access.permissions or "module:finance" in access.permissions:
        fields.update({"price", "amount"})
    return _pick(data, frozenset(fields))


def sales_order_log(data: dict, access: QueryAccess) -> dict:
    if access.capability != "sales_orders":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    if data.get("source") not in {"AUDIT", "APPROVAL"}:
        raise BizException(ErrorCode.BAD_REQUEST, "订单日志来源无效")
    safe_actions = {
        "CREATE", "UPDATE", "DELETE", "SUBMIT", "APPROVE", "REJECT",
        "UNAUDIT", "CANCEL", "CLOSE", "SHIP", "REFUND", "RETURN",
        "EXCHANGE",
    }
    safe_statuses = {"PENDING", "APPROVED", "REJECTED", "DONE"}
    action = data.get("action")
    status = data.get("status")
    result = {
        "event_id": data.get("event_id"),
        "source": data["source"],
        "action": action if action in safe_actions else "OTHER",
        "step": data.get("step") if data["source"] == "APPROVAL" else None,
        "status": (
            status if status in safe_statuses else "OTHER"
        ) if data["source"] == "APPROVAL" else None,
        "created_at": data.get("created_at"),
    }
    return _pick(result, frozenset(result))


def finance_expense(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "finance_expenses", _FINANCE_EXPENSE_FIELDS)


def finance_income(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "finance_incomes", _FINANCE_INCOME_FIELDS)


def finance_payment(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "finance_payments", _FINANCE_PAYMENT_FIELDS)


def finance_receipt(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "finance_receipts", _FINANCE_RECEIPT_FIELDS)


def finance_settlement(data: dict, access: QueryAccess) -> dict:
    return _finance_pick(data, access, "finance_settlements", _FINANCE_SETTLEMENT_FIELDS)


def print_batch(data: dict, access: QueryAccess) -> dict:
    if access.capability != "print_batch":
        raise BizException(ErrorCode.FORBIDDEN, "字段策略不匹配")
    result = _pick(data, frozenset({"batch_no", "print_type", "order_count", "created_at"}))
    rows = data.get("orders", [])
    if not isinstance(rows, list):
        raise BizException(ErrorCode.BAD_REQUEST, "外发明细结构无效")
    result["orders"] = []
    for row in rows[:100]:
        item = _pick(row, frozenset({"id", "order_no"}))
        item["receiver_phone"] = mask_phone(row.get("receiver_phone"))
        item["receiver_address"] = mask_address(row.get("receiver_address"))
        result["orders"].append(item)
    result["orders_truncated"] = len(rows) > 100
    return result
