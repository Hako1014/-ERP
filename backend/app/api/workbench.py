"""Role-scoped announcement, todo, and contextual help panels."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_staff
from app.core.errors import BizException, ErrorCode
from app.core.query_access import current_actor_access
from app.core.response import Result
from app.db.session import get_db
from app.models.announcement import Announcement
from app.models.org import Department, Staff
from app.services import approval_manage_service
from app.services.assistant_knowledge import (
    load_knowledge,
    page_sources,
    resolve_page_context,
    route_for_path,
)

router = APIRouter(tags=["工作台公告、待办与帮助"])


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _announcement_visible_query(
    db: Session,
    *,
    staff: Staff,
    role_codes: frozenset[str],
):
    now = _now_utc_naive()
    department_id = db.scalar(
        select(Department.id).where(
            Department.id == staff.department_id,
            Department.company_id == staff.company_id,
        )
    ) if staff.department_id else None

    def audience_match(column, values: list[str | int]):
        clauses = [column.is_(None), func.json_length(column) == 0]
        clauses.extend(
            func.json_contains(column, json.dumps(value, ensure_ascii=False)) == 1
            for value in values
        )
        return or_(*clauses)

    return select(Announcement).where(
        Announcement.company_id == staff.company_id,
        Announcement.status == "PUBLISHED",
        Announcement.published_at.is_not(None),
        Announcement.published_at <= now,
        or_(Announcement.starts_at.is_(None), Announcement.starts_at <= now),
        or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
        audience_match(Announcement.audience_roles, list(role_codes)),
        audience_match(
            Announcement.audience_department_ids,
            [department_id] if department_id is not None else [],
        ),
    )


def _announcement_summary(item: Announcement) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "preview": item.content[:240],
        "published_at": item.published_at,
        "expires_at": item.expires_at,
    }


@router.get("/announcements", response_model=Result[dict])
def list_announcements(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    _, role_codes, _ = current_actor_access(db, staff.id)
    visible = _announcement_visible_query(db, staff=staff, role_codes=role_codes)
    total = db.scalar(
        visible.with_only_columns(
            func.count(Announcement.id), maintain_column_froms=True
        ).order_by(None)
    ) or 0
    items = db.scalars(
        visible.order_by(Announcement.published_at.desc(), Announcement.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Result.ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_announcement_summary(item) for item in items],
    })


@router.get("/announcements/{announcement_id}", response_model=Result[dict])
def get_announcement(
    announcement_id: int,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    _, role_codes, _ = current_actor_access(db, staff.id)
    item = db.scalar(
        _announcement_visible_query(db, staff=staff, role_codes=role_codes).where(
            Announcement.id == announcement_id
        )
    )
    if item is None:
        raise BizException(ErrorCode.NOT_FOUND, "公告不存在或当前账号无权查看")
    return Result.ok({
        **_announcement_summary(item),
        "content": item.content,
        "starts_at": item.starts_at,
    })


@router.get("/workbench/todos", response_model=Result[dict])
def list_workbench_todos(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    _, _, permissions = current_actor_access(db, staff.id)
    if "*" not in permissions and not {
        "module:approval",
        "approval:approve",
    }.issubset(permissions):
        return Result.ok({
            "available": False,
            "total": 0,
            "page": page,
            "page_size": page_size,
            "items": [],
        })

    result = approval_manage_service.list_my_approvals(
        db,
        staff=staff,
        scope="pending",
        page=page,
        page_size=page_size,
        compact=True,
    )
    items = []
    for item in result["items"]:
        doc_type = str(item["doc_type"])
        doc_id = int(item["doc_id"])
        items.append({
            "doc_type": doc_type,
            "doc_type_label": item["doc_type_label"],
            "doc_id": doc_id,
            "doc_no": item["doc_no"],
            "title": " · ".join(
                part for part in (item["doc_type_label"], item["doc_no"]) if part
            ),
            "current_step": item["current_step"],
            "submitted_at": item["submitted_at"],
            "target": {
                "path": "/approval/mine",
                "query": {
                    "notificationDocType": doc_type,
                    "notificationDocId": str(doc_id),
                },
            },
        })
    return Result.ok({
        "available": True,
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "items": items,
    })


@router.get("/workbench/help", response_model=Result[dict])
def get_workbench_help(
    route_key: str | None = Query(default=None, max_length=160),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    _, _, permissions = current_actor_access(db, staff.id)
    permission_list = sorted(permissions)
    route = resolve_page_context(route_key, permission_list)
    knowledge = load_knowledge()
    assistant_route = route_for_path("/assistant")
    assistant_available = bool(
        assistant_route
        and ("*" in permissions or assistant_route["permission"] in permissions)
    )
    guidance = (
        knowledge.get("module_guides", {}).get(route.get("module"), "")
        if route is not None
        else ""
    )
    return Result.ok({
        "title": route["title"] if route else "帮助中心",
        "route_key": route["path"] if route else None,
        "guidance": guidance,
        "faq": knowledge.get("sections", {}).get("常见问题", ""),
        "sources": page_sources(route, permission_list),
        "assistant_available": assistant_available,
        "assistant_path": "/assistant" if assistant_available else None,
    })
