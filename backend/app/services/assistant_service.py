"""角色 AI 助手能力判定。

本模块按实时角色矩阵计算入口与工具能力；只做能力展示，不执行业务查询。
对话流式、只读工具调用和单据快捷录入分别由对应任务卡实现。
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.permissions import permissions_for_roles, visible_modules
from app.models.org import Staff
from app.repositories.rbac_repo import list_staff_role_codes
from app.services.assistant_governance import AssistantRuntime, resolve_runtime

SERVICE_READY = "ready"
SERVICE_UNCONFIGURED = "unconfigured"
SERVICE_UNAVAILABLE = "unavailable"

# 首版能力项。
# `enabled` 表示「当前身份是否被授权使用该能力」，与模型是否已配置无关；
# 真实可用性还需叠加 service_status——未配置密钥时前端只做提示，不生成回答。
FEATURE_GUIDE = "guide"
FEATURE_READ_QUERY = "read_query"
FEATURE_DRAFT_ENTRY = "draft_entry"

_FEATURES: tuple[tuple[str, bool, str | None], ...] = (
    # 页面操作指引：授权即开放（内容由 TASK-078 提供）。
    (FEATURE_GUIDE, True, None),
    # 单据快捷录入：第 2 版能力，首版仅保留扩展位，前端默认隐藏。
    (FEATURE_DRAFT_ENTRY, False, "planned_v2"),
)


def resolve_service_status(runtime: AssistantRuntime | None = None) -> str:
    """助手服务状态。不含密钥，也不暴露未核验的模型标识。"""
    settings = runtime.settings if runtime else get_settings()
    if not settings.assistant_enabled:
        return SERVICE_UNAVAILABLE
    provider = settings.assistant_provider.strip().lower()
    if provider == "mock":
        return SERVICE_READY if settings.debug else SERVICE_UNAVAILABLE
    if provider != "deepseek":
        return SERVICE_UNAVAILABLE
    if not settings.assistant_api_key or not settings.assistant_model:
        return SERVICE_UNCONFIGURED
    return SERVICE_READY


def resolve_provider_mode(runtime: AssistantRuntime | None = None) -> str:
    """只返回非敏感的运行模式，不返回服务地址、密钥或模型标识。"""
    settings = runtime.settings if runtime else get_settings()
    provider = settings.assistant_provider.strip().lower()
    if provider == "deepseek" and settings.assistant_enabled:
        return "deepseek"
    if provider == "mock" and settings.debug and settings.assistant_enabled:
        return "mock"
    return "unavailable"


def _features(
    *,
    read_query_enabled: bool,
    read_query_reason: str | None,
    draft_entry_enabled: bool,
    draft_entry_reason: str | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for code, enabled, reason in _FEATURES:
        if code == FEATURE_DRAFT_ENTRY:
            enabled, reason = draft_entry_enabled, draft_entry_reason
        item: dict[str, Any] = {"code": code, "enabled": enabled}
        if reason:
            item["reason"] = reason
        if code == FEATURE_DRAFT_ENTRY:
            item["document_types"] = ["purchase_plan"] if enabled else []
        items.append(item)
    query_item: dict[str, Any] = {
        "code": FEATURE_READ_QUERY,
        "enabled": read_query_enabled,
    }
    if read_query_reason:
        query_item["reason"] = read_query_reason
    items.append(query_item)
    return items


def build_capabilities(
    db: Session, staff: Staff, runtime: AssistantRuntime | None = None
) -> dict[str, Any]:
    """按当前身份重新计算助手能力。

    权限每次都从数据库重新解析，客户端提交的角色或权限不参与判定。
    """
    runtime = runtime or resolve_runtime(db, staff.company_id, env_settings=get_settings())
    roles = list_staff_role_codes(db, staff.id)
    codes = permissions_for_roles(roles)
    modules = visible_modules(codes)
    if not modules:
        raise BizException(ErrorCode.FORBIDDEN, "当前账号未开通 AI 助手")
    if resolve_provider_mode(runtime) != "deepseek":
        query_enabled = False
        query_reason = "provider_tools_unavailable"
    else:
        query_enabled = any(
            "*" in codes or f"module:{module}" in codes
            for module in ("dashboard", "purchase", "stock", "order", "approval")
        )
        query_reason = None if query_enabled else "module_not_authorized"
    draft_entry_enabled = "*" in codes or "purchase:create" in codes
    if not draft_entry_enabled:
        draft_entry_reason = "purchase_create_not_authorized"
    elif resolve_provider_mode(runtime) != "deepseek":
        draft_entry_enabled = False
        draft_entry_reason = "structured_provider_unavailable"
    else:
        draft_entry_reason = None
    return {
        "staff_id": staff.id,
        "service_status": resolve_service_status(runtime),
        "provider_mode": resolve_provider_mode(runtime),
        "roles": roles,
        "permissions": sorted(codes),
        "modules": modules,
        "features": _features(
            read_query_enabled=query_enabled,
            read_query_reason=query_reason,
            draft_entry_enabled=draft_entry_enabled,
            draft_entry_reason=draft_entry_reason,
        ),
    }
