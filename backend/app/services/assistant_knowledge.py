"""由前端菜单、路由和用户手册生成的助手知识索引。"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import BizException, ErrorCode

_KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "assistant_knowledge.json"


@lru_cache(maxsize=1)
def load_knowledge() -> dict[str, Any]:
    with _KNOWLEDGE_PATH.open(encoding="utf-8") as source:
        return json.load(source)


def route_for_path(path: str) -> dict[str, Any] | None:
    return next(
        (route for route in load_knowledge()["routes"] if route["path"] == path),
        None,
    )


def can_access_route(route: dict[str, Any], permissions: list[str]) -> bool:
    allowed = set(permissions)
    return "*" in allowed or route["permission"] in allowed


def resolve_page_context(
    path: str | None,
    permissions: list[str],
) -> dict[str, Any] | None:
    if path is None:
        return None
    route = route_for_path(path)
    if route is None:
        raise BizException(ErrorCode.BAD_REQUEST, "页面上下文不在已登记路由中")
    if not can_access_route(route, permissions):
        raise BizException(ErrorCode.FORBIDDEN, "无权使用该页面上下文")
    return route


def page_sources(
    route: dict[str, Any] | None,
    permissions: list[str],
) -> list[dict[str, str]]:
    if route is None:
        return []
    knowledge = load_knowledge()
    version = knowledge["version"]
    sources: list[dict[str, str]] = []
    section = route.get("guide_section")
    if route.get("guide_status") == "available" and section:
        sources.append(
            {
                "type": "guide",
                "title": f"模块速查：{knowledge['module_guide_titles'][section]}",
                "href": route["path"],
                "scope": f"仅包含当前授权模块 {route.get('module')} 的操作说明",
                "knowledge_version": version,
            }
        )
    else:
        sources.append(
            {
                "type": "guide",
                "title": f"{route['title']}：暂无专属操作说明",
                "href": route["path"],
                "scope": "已登记路由；本手册暂无对应章节",
                "knowledge_version": version,
            }
        )

    if knowledge["sections"].get("常见问题"):
        sources.append(
            {
                "type": "guide",
                "title": "用户手册：通用常见问题",
                "href": route["path"],
                "scope": "已授权助手用户共享的登录、权限和筛选说明",
                "knowledge_version": version,
            }
        )

    related = [
        candidate
        for candidate in knowledge["routes"]
        if candidate.get("is_leaf")
        and candidate.get("module") == route.get("module")
        and candidate["path"] != route["path"]
        and can_access_route(candidate, permissions)
    ]
    for candidate in related[:5]:
        sources.append(
            {
                "type": "navigation",
                "title": f"前往：{candidate['title']}",
                "href": candidate["path"],
                "scope": "已登记且当前账号有权访问的页面",
                "knowledge_version": version,
            }
        )
    return sources
