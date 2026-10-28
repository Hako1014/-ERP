"""当前用户站内消息 API 与可恢复 SSE。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from itertools import islice

import jwt
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_staff
from app.core.data_scope import clear_scope_policy, initialize_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.security import decode_token
from app.core.response import Result
from app.db.redis import RedisCache, get_redis
from app.db.session import SessionLocal, get_db
from app.models.org import Staff
from app.services.notification_query_service import (
    decode_cursor,
    encode_cursor,
    category_for,
    get_visible_notification,
    iter_visible_notifications,
    scan_stream_batch,
    serialize_notification,
    stream_identity_snapshot,
    utc_now,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["站内消息"])
_STREAM_POLL_SECONDS = 5
_STREAM_HEARTBEAT_SECONDS = 15
_STREAM_AUTH_CHECK_SECONDS = 10
_STREAM_BATCH_SIZE = 200


def _load_stream_identity(staff_id: int):
    with SessionLocal() as auth_db:
        return stream_identity_snapshot(auth_db, staff_id)


def _load_stream_batch(staff_id: int, company_id: int, after_id: int):
    with SessionLocal() as stream_db:
        stream_staff = stream_db.get(Staff, staff_id)
        if (
            stream_staff is None
            or not stream_staff.is_active
            or stream_staff.company_id != company_id
        ):
            return None, after_id, False
        initialize_scope_policy(stream_db, stream_staff)
        try:
            return scan_stream_batch(
                stream_db,
                stream_staff,
                after_id=after_id,
                batch_size=_STREAM_BATCH_SIZE,
            )
        finally:
            clear_scope_policy(stream_db)


def _decode_or_bad_request(value: str | None, *, staff_id: int, company_id: int) -> int:
    try:
        return decode_cursor(value, staff_id=staff_id, company_id=company_id)
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, str(exc)) from None


@router.get("", response_model=Result[dict])
def list_notifications(
    cursor: str | None = None,
    category: str = Query(default="all", pattern="^(all|approval|purchase)$"),
    read_state: str | None = Query(default=None, pattern="^(UNREAD|READ)$"),
    page_size: int = Query(default=20, ge=1, le=100),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    before_id = _decode_or_bad_request(
        cursor, staff_id=staff.id, company_id=staff.company_id
    )
    notifications = list(
        islice(
            iter_visible_notifications(
                db,
                staff,
                direction="desc",
                cursor_id=before_id if before_id else None,
                category=category,
                read_state=read_state,
            ),
            page_size + 1,
        )
    )
    has_more = len(notifications) > page_size
    page = notifications[:page_size]
    next_cursor = (
        encode_cursor(
            staff_id=staff.id,
            company_id=staff.company_id,
            inbox_id=page[-1][0].id,
        )
        if has_more and page
        else None
    )
    return Result.ok(
        {
            "items": [
                serialize_notification(
                    inbox, staff_id=staff.id, company_id=staff.company_id
                )
                for inbox, _event in page
            ],
            "page_size": page_size,
            "next_cursor": next_cursor,
        }
    )


@router.get("/unread-count", response_model=Result[dict])
def unread_count(
    staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)
):
    count = sum(
        1
        for _ in iter_visible_notifications(
            db, staff, direction="desc", read_state="UNREAD"
        )
    )
    return Result.ok({"count": count})


@router.get("/events")
def notification_events(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
    redis: RedisCache = Depends(get_redis),
):
    if credentials is None:
        raise BizException(ErrorCode.UNAUTHORIZED, "未登录")
    try:
        claims = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise BizException(ErrorCode.UNAUTHORIZED, "登录已过期或无效") from None
    token_exp = claims.get("exp")
    if not isinstance(token_exp, (int, float)):
        raise BizException(ErrorCode.UNAUTHORIZED, "登录凭证缺少有效期")
    staff_id = claims.get("staff_id")
    if (
        isinstance(staff_id, bool)
        or not isinstance(staff_id, int)
        or staff_id < 1
        or staff_id != staff.id
    ):
        raise BizException(ErrorCode.UNAUTHORIZED, "无效的登录凭证")
    if not staff.is_active:
        raise BizException(ErrorCode.UNAUTHORIZED, "账号不存在或已停用")
    company_id = staff.company_id
    identity_snapshot = stream_identity_snapshot(db, staff_id)
    if identity_snapshot is None or identity_snapshot[0] != company_id:
        raise BizException(ErrorCode.UNAUTHORIZED, "账号已停用或所属公司已变更")
    after_id = _decode_or_bad_request(
        last_event_id, staff_id=staff_id, company_id=company_id
    )
    async def events():
        pubsub = None
        channel = f"erp:notifications:{company_id}:{staff_id}"
        cursor_id = after_id
        last_heartbeat = time.monotonic()
        last_auth_check = last_heartbeat
        next_poll = last_heartbeat
        try:
            try:
                pubsub = redis.pubsub()
                pubsub.subscribe(channel)
            except Exception:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                pubsub = None

            yield "retry: 5000\n\n"
            while True:
                now = time.monotonic()
                if utc_now().timestamp() >= float(token_exp):
                    yield 'event: auth_expired\ndata: {"code":"TOKEN_EXPIRED"}\n\n'
                    return

                if now - last_auth_check >= _STREAM_AUTH_CHECK_SECONDS:
                    current_snapshot = await asyncio.to_thread(
                        _load_stream_identity, staff_id
                    )
                    if current_snapshot != identity_snapshot:
                        yield 'event: auth_revoked\ndata: {"code":"IDENTITY_CHANGED"}\n\n'
                        return
                    last_auth_check = now

                signaled = False
                if pubsub is not None:
                    try:
                        signal = pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=0
                        )
                        signaled = signal is not None
                    except Exception:
                        try:
                            pubsub.close()
                        except Exception:
                            pass
                        pubsub = None

                if signaled or now >= next_poll:
                    while True:
                        visible, scanned_to, has_more = await asyncio.to_thread(
                            _load_stream_batch, staff_id, company_id, cursor_id
                        )
                        if visible is None:
                            yield 'event: auth_revoked\ndata: {"code":"IDENTITY_CHANGED"}\n\n'
                            return
                        cursor_id = scanned_to
                        for inbox, _event in visible:
                            if utc_now().timestamp() >= float(token_exp):
                                yield 'event: auth_expired\ndata: {"code":"TOKEN_EXPIRED"}\n\n'
                                return
                            event_cursor = encode_cursor(
                                staff_id=staff_id,
                                company_id=company_id,
                                inbox_id=inbox.id,
                            )
                            data = json.dumps(
                                {
                                    "inbox_id": inbox.id,
                                    "category": category_for(inbox),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            yield (
                                f"id: {event_cursor}\nevent: notification\n"
                                f"data: {data}\n\n"
                            )
                        if not has_more:
                            break
                    next_poll = time.monotonic() + _STREAM_POLL_SECONDS

                now = time.monotonic()
                if now - last_heartbeat >= _STREAM_HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("站内消息 SSE 流异常，当前连接结束")
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass

    # get_current_staff 的请求级 Session 不应被 SSE 长连接占用。
    clear_scope_policy(db)
    db.close()
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{inbox_id}", response_model=Result[dict])
def notification_detail(
    inbox_id: int,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = get_visible_notification(db, staff, inbox_id)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "消息不存在或已无权查看")
    return Result.ok(
        serialize_notification(
            row, staff_id=staff.id, company_id=staff.company_id
        )
    )


@router.post("/{inbox_id}/read", response_model=Result[dict])
def mark_notification_read(
    inbox_id: int,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    row = get_visible_notification(db, staff, inbox_id)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "消息不存在或已无权查看")
    if row.read_state != "READ":
        row.read_state = "READ"
        row.read_at = utc_now().replace(tzinfo=None)
        db.commit()
        db.refresh(row)
    return Result.ok({"id": row.id, "read_state": row.read_state, "read_at": row.read_at})


@router.post("/read-all", response_model=Result[dict])
def mark_all_notifications_read(
    staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)
):
    rows = [
        inbox
        for inbox, _event in iter_visible_notifications(
            db, staff, direction="desc", read_state="UNREAD"
        )
    ]
    read_at = utc_now().replace(tzinfo=None)
    for inbox in rows:
        inbox.read_state = "READ"
        inbox.read_at = read_at
    if rows:
        db.commit()
    return Result.ok({"updated_count": len(rows)})
