"""RBAC 权限定义，默认矩阵见 docs/权限矩阵.md。"""

MODULES = [
    "dashboard",
    "purchase",
    "product",
    "stock",
    "order",
    "approval",
    "finance",
    "settings",
]

PERMISSIONS: dict[str, str] = {
    # 模块可见
    "module:dashboard": "首页看板",
    "module:purchase": "采购管理",
    "module:product": "商品管理",
    "module:stock": "库存管理",
    "module:order": "订单管理",
    "module:approval": "协同办公",
    "module:finance": "财务管理",
    "module:settings": "基础设置",
    # 动作
    "purchase:create": "采购制单",
    "purchase:approve": "采购审核",
    "product:manage": "商品维护",
    "stock:manage": "库存操作",
    "order:audit": "订单审核",
    "order:ship": "订单发货",
    "after_sale:manage": "售后管理",
    "finance:manage": "财务操作",
    "approval:configure": "审批流配置",
    "approval:approve": "审批/审核",
    "notification:manage": "失败通知受控重投",
    "settings:manage": "基础设置与公司设置管理",
    "organization:read": "同公司组织人员只读目录",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "ADMIN": {"*", "organization:read"},
    "VP": {
        "module:dashboard",
        "module:purchase",
        "module:approval",
        "purchase:approve",
        "approval:approve",
        "organization:read",
    },
    "FINANCE": {
        "module:dashboard",
        "module:purchase",
        "module:order",
        "module:approval",
        "module:finance",
        "purchase:approve",
        "order:audit",
        "finance:manage",
        "approval:approve",
        "organization:read",
    },
    "PURCHASER": {
        "module:dashboard",
        "module:purchase",
        "module:product",
        "module:approval",
        "purchase:create",
        "organization:read",
    },
    "WAREHOUSE": {
        "module:dashboard",
        "module:stock",
        "module:order",
        "module:approval",
        "stock:manage",
        "order:ship",
        "approval:approve",
        "organization:read",
    },
    "QUALITY": {
        "module:dashboard",
        "module:purchase",
        "module:stock",
        "module:approval",
        "purchase:approve",
        "approval:approve",
        "organization:read",
    },
    "AFTER_SALE": {"module:dashboard", "module:order", "after_sale:manage", "organization:read"},
}


def permissions_for_roles(roles: list[str]) -> set[str]:
    """合并多个角色的权限集合。"""
    result: set[str] = set()
    for role in roles:
        perms = ROLE_PERMISSIONS.get(role, set())
        result |= perms
    return result


def visible_modules(codes: set[str]) -> list[str]:
    if "*" in codes:
        return list(MODULES)
    return sorted(p.removeprefix("module:") for p in codes if p.startswith("module:"))
