"""持久私人会话、幂等写入与历史权限复核；正文不进入审计或日志。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.data_scope import clear_scope_policy, document_visible, initialize_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.query_access import authorize_query, current_actor_access
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.models.assistant_attachment import AssistantAttachment
from app.models.org import Staff
from app.models.order import Order
from app.models.purchase import PurchaseOrder
from app.repositories import assistant_conversation_repo as repo
from app.services.assistant_knowledge import resolve_page_context

ACTIVE_STATES = ("PENDING", "STREAMING")
HIDDEN_TEXT = "权限已变化，此内容暂不可查看"
MAX_ANSWER_CHARS = 64_000
MAX_CONTEXT_DOCUMENTS = 64


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Actor:
    staff_id: int
    company_id: int
    permissions: frozenset[str]
    fingerprint: str


@dataclass(frozen=True)
class Run:
    conversation_id: str
    company_id: int
    staff_id: int
    user_id: str
    assistant_id: str
    user_seq: int
    assistant_seq: int
    version: int
    request_id: str
    guard: dict


def actor_context(db: Session, staff_id: int, company_id: int) -> Actor:
    # 请求/流式事件可能复用已结束的依赖 Session，重新绑定实时身份，不能沿用旧策略。
    staff, roles, permissions = current_actor_access(db, staff_id)
    if staff.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "会话不存在或不可见")
    clear_scope_policy(db)
    policy = initialize_scope_policy(db, staff)
    members = []
    if policy.scope_type == "DEPARTMENT" and policy.department_id is not None:
        members = list(db.scalars(select(Staff.id).where(
            Staff.company_id == company_id, Staff.department_id == policy.department_id,
        ).order_by(Staff.id)))
    fingerprint = digest({
        "v": 1, "staff": staff_id, "company": company_id,
        "roles": sorted(roles), "permissions": sorted(permissions),
        "department": policy.department_id, "scope": policy.scope_type, "members": members,
    })
    return Actor(staff_id, company_id, permissions, fingerprint)


def _approval_acl_stamp(db: Session, actor: Actor) -> str:
    # 聚合审批结果没有逐行公开 ID，故保存整个授权关系摘要；改变时整段隐藏。
    value = hashlib.sha256()
    for stmt in (
        select(ApprovalFlow.id, ApprovalFlow.approvers).where(ApprovalFlow.company_id == actor.company_id).order_by(ApprovalFlow.id),
        select(ApprovalRecord.id, ApprovalRecord.flow_id, ApprovalRecord.approver_id,
               ApprovalRecord.step, ApprovalRecord.status, ApprovalRecord.action,
               ApprovalRecord.doc_type, ApprovalRecord.doc_id).where(
                   ApprovalRecord.company_id == actor.company_id,
               ).order_by(ApprovalRecord.id),
    ):
        for row in db.execute(stmt).yield_per(500):
            value.update(json.dumps(tuple(row), default=str, ensure_ascii=False).encode())
    return value.hexdigest()


def guard_visible(db: Session, actor: Actor, guard: dict | None) -> bool:
    if not isinstance(guard, dict) or guard.get("version") != 1 or guard.get("access") != actor.fingerprint:
        return False
    try:
        for path in guard.get("pages", []):
            resolve_page_context(path, list(actor.permissions))
        if guard.get("used_approval") and guard.get("approval_acl") != _approval_acl_stamp(db, actor):
            return False
        for document in guard.get("documents", []):
            kind, doc_id = document["doc_type"], document["doc_id"]
            if kind == "order":
                access = authorize_query(db, "assistant_order")
                row = db.scalar(select(Order).where(Order.id == doc_id, Order.company_id == actor.company_id))
                if row is None or (access.policy.scope_type != "COMPANY" and not document_visible(db, None, "order", doc_id)):
                    return False
            elif kind == "purchase_order":
                authorize_query(db, "assistant_purchase")
                row = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == doc_id, PurchaseOrder.company_id == actor.company_id).execution_options(populate_existing=True))
                if row is None or not document_visible(db, row.created_by, "purchase_order", doc_id):
                    return False
            else:
                return False
        return True
    except (BizException, KeyError, TypeError, ValueError):
        return False


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BizException(ErrorCode.CONFLICT, "消息或请求已提交，请刷新会话") from None
    except SQLAlchemyError:
        db.rollback()
        raise BizException(ErrorCode.INTERNAL_ERROR, "会话暂时无法保存，请稍后重试") from None


def _audit(db: Session, actor: Actor, conversation: AssistantConversation, action: str, request_id: str | None = None):
    write_audit(db, action=f"assistant.chat.{action}", target_type="assistant_conversation",
                target_id=conversation.id, actor_id=actor.staff_id, company_id=actor.company_id,
                detail={"version": conversation.version, "request_id": request_id})


def _check_version(conversation: AssistantConversation, version: int):
    if conversation.version != version:
        raise BizException(ErrorCode.CONFLICT, "会话已更新，请刷新后重试", detail={"version": conversation.version})


def _interrupt(db: Session, conversation: AssistantConversation, *, expired_only: bool = False):
    stmt = repo.messages(conversation).where(AssistantMessage.status.in_(ACTIVE_STATES))
    if expired_only:
        stmt = stmt.where(AssistantMessage.run_until <= utcnow())
    rows = list(db.scalars(stmt.with_for_update()))
    for row in rows:
        row.status = "INTERRUPTED"
        row.updated_at = utcnow()
    if rows:
        conversation.version += 1
        conversation.updated_at = utcnow()


def _recover(db: Session, actor: Actor):
    ids = list(db.scalars(select(AssistantMessage.conversation_id).where(
        AssistantMessage.company_id == actor.company_id, AssistantMessage.staff_id == actor.staff_id,
        AssistantMessage.status.in_(ACTIVE_STATES), AssistantMessage.run_until <= utcnow(),
        AssistantMessage.purge_after.is_(None),
    ).distinct().limit(100)))
    for conversation_id in ids:
        conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
        _interrupt(db, conversation, expired_only=True)
    if ids:
        _commit(db)


def message_contract(db: Session, actor: Actor, row: AssistantMessage, *, visible: bool | None = None) -> dict:
    from app.services.assistant_attachments import message_attachments
    visible = (row.role == "user" or guard_visible(db, actor, row.visibility_guard)) if visible is None else visible
    return {
        "id": row.id, "seq": row.seq, "role": row.role,
        "content": row.content if visible else HIDDEN_TEXT,
        "status": row.status, "hidden": not visible,
        "client_message_id": row.client_message_id,
        "request_id": row.request_id,
        "sources": row.sources if visible and row.role == "assistant" else [],
        "page_context": row.page_context if visible and row.role == "assistant" else None,
        "error_code": row.error_code if visible else None,
        "attachments": message_attachments(db, actor, row.id) if visible and row.role == "user" else [],
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _conversation_contract(db: Session, actor: Actor, row: AssistantConversation, *, q: str = "") -> dict | None:
    # 先权限复核再生成标题、搜索命中、摘要与计数；不得先算 SQL 总数再过滤搜索。
    cache: dict[str, bool] = {}
    hidden = False
    visible_ids = set()
    user_count = int(db.scalar(select(func.count()).select_from(repo.messages(row).where(AssistantMessage.role == "user").subquery())) or 0)
    for message_id, guard in db.execute(repo.messages(row).where(AssistantMessage.role == "assistant").with_only_columns(AssistantMessage.id, AssistantMessage.visibility_guard)):
        key = digest(guard)
        if key not in cache:
            cache[key] = guard_visible(db, actor, guard)
        if cache[key]:
            visible_ids.add(message_id)
        else:
            hidden = True
    title = "权限已变化的会话" if hidden else row.title
    matched_message_seq = None
    if q:
        matches = db.scalars(repo.messages(row).where(
            AssistantMessage.content.icontains(q, autoescape=True),
        ).order_by(AssistantMessage.seq.desc()))
        for item in matches:
            if item.role == "user" or item.id in visible_ids:
                matched_message_seq = item.seq
                break
        if q.casefold() not in title.casefold() and matched_message_seq is None:
            return None
    summary = ""
    for item in db.scalars(repo.messages(row).order_by(AssistantMessage.seq.desc()).limit(8)):
        if (item.role == "user" or item.id in visible_ids) and item.content:
            summary = item.content[:160]
            break
    return {
        "id": row.id, "title": title, "summary": summary,
        "is_pinned": row.is_pinned, "status": row.status, "version": row.version,
        "latest_message_seq": row.latest_message_seq, "message_count": user_count + len(visible_ids),
        "created_at": row.created_at, "updated_at": row.updated_at,
        "retention_days": get_settings().assistant_conversation_retention_days,
        "matched_message_seq": matched_message_seq,
    }


def create_conversation(db: Session, staff: Staff, title: str = "新会话", request_id: str | None = None):
    actor = actor_context(db, staff.id, staff.company_id)
    row = AssistantConversation(id=str(uuid4()), company_id=actor.company_id, staff_id=actor.staff_id,
                                title=title, created_at=utcnow(), updated_at=utcnow())
    db.add(row)
    # Python column defaults are assigned on INSERT; initialize before auditing/responding.
    row.version, row.latest_message_seq, row.cleared_through_seq = 1, 0, 0
    row.status, row.is_pinned = "ACTIVE", False
    _audit(db, actor, row, "create", request_id)
    _commit(db)
    return _conversation_contract(db, actor, row)


def list_conversations(db: Session, staff: Staff, *, page: int, page_size: int, q: str = "", status: str = "ACTIVE"):
    actor = actor_context(db, staff.id, staff.company_id)
    _recover(db, actor)
    stmt = repo.conversations(actor.company_id, actor.staff_id)
    if status != "ALL":
        stmt = stmt.where(AssistantConversation.status == status)
    stmt = stmt.order_by(AssistantConversation.is_pinned.desc(), AssistantConversation.updated_at.desc(), AssistantConversation.id)
    if not q:
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        rows = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
        items = [_conversation_contract(db, actor, row) for row in rows]
    else:
        # 查询正文不会暴露 SQL 命中数；逐会话权限裁剪后再计算分页和 total。
        total, items = 0, []
        for row in db.scalars(stmt):
            item = _conversation_contract(db, actor, row, q=q)
            if item is not None:
                if (page - 1) * page_size <= total < page * page_size:
                    items.append(item)
                total += 1
    return {"page": page, "page_size": page_size, "total": total, "items": items}


def get_conversation(db: Session, staff: Staff, conversation_id: str):
    actor = actor_context(db, staff.id, staff.company_id)
    row = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    _interrupt(db, row, expired_only=True)
    _commit(db)
    return _conversation_contract(db, actor, row)


def update_conversation(db: Session, staff: Staff, conversation_id: str, *, version: int, changes: dict, request_id: str):
    actor = actor_context(db, staff.id, staff.company_id)
    row = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    _check_version(row, version)
    _interrupt(db, row)
    for field, value in changes.items():
        if field not in {"title", "is_pinned", "status"} or value is None:
            raise BizException(ErrorCode.VALIDATION_ERROR, "会话修改字段无效")
        setattr(row, field, value)
    row.version += 1
    row.updated_at = utcnow()
    _audit(db, actor, row, "update", request_id)
    _commit(db)
    return _conversation_contract(db, actor, row)


def clear_or_delete(db: Session, staff: Staff, conversation_id: str, *, version: int, request_id: str, deleting: bool = False):
    actor = actor_context(db, staff.id, staff.company_id)
    row = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    _check_version(row, version)
    _interrupt(db, row)
    purge_at = utcnow() + timedelta(days=get_settings().assistant_deleted_retention_days)
    from app.services.assistant_attachments import retire_conversation
    retire_conversation(db, row.id, purge_at)
    db.execute(update(AssistantMessage).where(
        AssistantMessage.company_id == actor.company_id, AssistantMessage.staff_id == actor.staff_id,
        AssistantMessage.conversation_id == row.id, AssistantMessage.purge_after.is_(None),
    ).values(purge_after=purge_at))
    row.cleared_through_seq = row.latest_message_seq
    row.version += 1
    row.updated_at = utcnow()
    if deleting:
        row.status, row.deleted_at, row.is_pinned = "DELETED", utcnow(), False
    _audit(db, actor, row, "delete" if deleting else "clear", request_id)
    _commit(db)
    return {"id": row.id, "version": row.version, "status": row.status}


def list_messages(db: Session, staff: Staff, conversation_id: str, *, before_seq: int | None = None, limit: int = 20):
    actor = actor_context(db, staff.id, staff.company_id)
    row = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    _interrupt(db, row, expired_only=True)
    _commit(db)
    stmt = repo.messages(row)
    if before_seq is not None:
        stmt = stmt.where(AssistantMessage.seq < before_seq)
    rows = list(db.scalars(stmt.order_by(AssistantMessage.seq.desc()).limit(limit + 1)))
    has_more, rows = len(rows) > limit, rows[:limit]
    return {"items": [message_contract(db, actor, item) for item in reversed(rows)],
            "next_before_seq": rows[-1].seq if has_more else None, "version": row.version}


def begin_message(db: Session, staff: Staff, conversation_id: str, *, version: int, client_message_id: str,
                  request_id: str, content: str, page_context: dict | None, session_id: str,
                  session_version: int, login_fingerprint: str, run_seconds: int) -> tuple[Run, list[dict], bool]:
    actor = actor_context(db, staff.id, staff.company_id)
    row = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    body_digest = digest({"content": content, "page_context": page_context})
    existing = db.scalar(select(AssistantMessage).where(
        AssistantMessage.conversation_id == row.id, AssistantMessage.company_id == actor.company_id,
        AssistantMessage.staff_id == actor.staff_id, AssistantMessage.client_message_id == client_message_id,
    ).with_for_update())
    if existing is not None:
        if existing.payload_digest != body_digest or existing.seq <= row.cleared_through_seq or existing.purge_after:
            raise BizException(ErrorCode.CONFLICT, "幂等键已用于其他消息或已清空的消息")
        reply = db.scalar(repo.messages(row).where(AssistantMessage.reply_to_id == existing.id).with_for_update())
        if reply is None:
            raise BizException(ErrorCode.CONFLICT, "消息记录不可重放，请刷新")
        run = Run(row.id, actor.company_id, actor.staff_id, existing.id, reply.id, existing.seq, reply.seq,
                  reply.run_version, reply.request_id, reply.visibility_guard or {})
        db.rollback()
        return run, [], True
    _check_version(row, version)
    if row.status != "ACTIVE":
        raise BizException(ErrorCode.CONFLICT, "归档会话须恢复后才能发送消息")
    _interrupt(db, row, expired_only=True)
    if db.scalar(repo.messages(row).where(AssistantMessage.status.in_(ACTIVE_STATES)).limit(1)) is not None:
        raise BizException(ErrorCode.CONFLICT, "该会话仍在生成回答，请先停止或等待")
    pages, documents, used_approval = set(), {}, False
    if page_context:
        resolve_page_context(page_context["path"], list(actor.permissions))
        pages.add(page_context["path"])
        selected = page_context.get("selected_document")
        if selected:
            documents[digest(selected)] = selected
    history = []
    for item in reversed(list(db.scalars(repo.messages(row).order_by(AssistantMessage.seq.desc()).limit(get_settings().assistant_history_max_messages)))):
        if item.role == "user":
            # 仅保存的附件消息不属于模型上下文；TASK-107 显式外发确认后再引入。
            has_files = db.scalar(select(AssistantAttachment.id).where(AssistantAttachment.message_id == item.id).limit(1))
            if item.content and not has_files:
                history.append({"role": "user", "content": item.content[-4000:]})
        elif item.status in ("COMPLETED", "INTERRUPTED") and item.content and guard_visible(db, actor, item.visibility_guard):
            inherited = {digest(document): document for document in item.visibility_guard.get("documents", [])}
            if len(set(documents) | set(inherited)) > MAX_CONTEXT_DOCUMENTS:
                # 无法带齐授权依赖就不带该历史回答，不能先丢依赖再复用其业务事实。
                continue
            history.append({"role": "assistant", "content": item.content[-4000:]})
            pages.update(item.visibility_guard.get("pages", []))
            documents.update(inherited)
            used_approval |= bool(item.visibility_guard.get("used_approval"))
    guard = {"version": 1, "access": actor.fingerprint, "pages": sorted(pages),
             "documents": list(documents.values()), "used_approval": used_approval,
             "approval_acl": _approval_acl_stamp(db, actor) if "*" in actor.permissions or "module:approval" in actor.permissions else None}
    row.version += 1
    row.updated_at = utcnow()
    now = utcnow()
    common = dict(conversation_id=row.id, company_id=actor.company_id, staff_id=actor.staff_id,
                  request_id=request_id, payload_digest=body_digest, run_version=row.version,
                  session_id=session_id, session_version=session_version, login_fingerprint=login_fingerprint,
                  run_until=now + timedelta(seconds=run_seconds), created_at=now, updated_at=now,
                  page_context=page_context, sources=[], status="PENDING")
    user = AssistantMessage(id=str(uuid4()), seq=row.latest_message_seq + 1, role="user", content=content,
                            client_message_id=client_message_id, **common)
    reply = AssistantMessage(id=str(uuid4()), seq=row.latest_message_seq + 2, role="assistant", content="",
                             reply_to_id=user.id, visibility_guard=guard, **common)
    row.latest_message_seq += 2
    db.add_all([user, reply])
    _audit(db, actor, row, "message.accept", request_id)
    _commit(db)
    return Run(row.id, actor.company_id, actor.staff_id, user.id, reply.id, user.seq, reply.seq,
               row.version, request_id, guard), history, False


def assert_run(db: Session, run: Run):
    actor = actor_context(db, run.staff_id, run.company_id)
    conversation = repo.owned_conversation(db, run.company_id, run.staff_id, run.conversation_id)
    _check_version(conversation, run.version)
    reply = db.scalar(repo.messages(conversation).where(AssistantMessage.id == run.assistant_id))
    if reply is None or reply.status not in ACTIVE_STATES or reply.run_until <= utcnow():
        raise BizException(ErrorCode.CONFLICT, "该回答已停止或失效")
    if not guard_visible(db, actor, reply.visibility_guard):
        raise BizException(ErrorCode.FORBIDDEN, "权限已变化，回答已停止")
    return actor, conversation, reply


def persist_event(db: Session, run: Run, event: str, data: dict) -> dict:
    # 统一锁顺序：conversation -> message；清空/删除与迟到事件不能交错复活数据。
    repo.owned_conversation(db, run.company_id, run.staff_id, run.conversation_id, lock=True)
    actor, conversation, reply = assert_run(db, run)
    if event == "delta":
        text = data.get("text", "")
        if not isinstance(text, str) or len(reply.content) + len(text) > MAX_ANSWER_CHARS:
            raise BizException(ErrorCode.AI_PROVIDER_ERROR, "回答超过单条消息保存上限")
        reply.content += text
        reply.status = "STREAMING"
    elif event == "source":
        sources = data.get("items", [])
        if not isinstance(sources, list):
            raise BizException(ErrorCode.AI_PROVIDER_ERROR)
        reply.sources = (reply.sources + sources)[:100]
        guard = dict(reply.visibility_guard)
        pages = set(guard.get("pages", []))
        for source in sources:
            if source.get("href"):
                resolve_page_context(source["href"], list(actor.permissions))
                pages.add(source["href"])
        guard["pages"] = sorted(pages)
        reply.visibility_guard = guard
        if any(item.get("type") == "data" and item.get("title") == "ERP 我的审批" for item in sources):
            guard["used_approval"] = True
            reply.visibility_guard = dict(guard)
            if not guard_visible(db, actor, guard):
                raise BizException(ErrorCode.FORBIDDEN, "审批授权关系已变化，回答已停止")
    elif event in ("done", "error"):
        reply.status = "COMPLETED" if event == "done" else "FAILED"
        reply.error_code = data.get("code") if event == "error" else None
        user = db.scalar(repo.messages(conversation).where(AssistantMessage.id == run.user_id))
        user.status = "COMPLETED"
        user.updated_at = utcnow()
        conversation.version += 1
    reply.updated_at = conversation.updated_at = utcnow()
    _commit(db)
    if event in ("done", "error"):
        data = {**data, "assistant_message_id": reply.id, "seq": reply.seq,
                "status": reply.status, "conversation_version": conversation.version}
    return data


def interrupt_run(db: Session, run: Run, *, status: str = "INTERRUPTED", error_code: str | None = None):
    # 退出/过期后仍可封存已经收到的部分；只按不可变 run 身份定位，不扩大读取权限。
    conversation = db.scalar(repo.conversations(run.company_id, run.staff_id).where(
        AssistantConversation.id == run.conversation_id,
    ).with_for_update().execution_options(populate_existing=True))
    if conversation is None or conversation.version != run.version:
        db.rollback()
        return
    rows = list(db.scalars(repo.messages(conversation).where(
        AssistantMessage.id.in_([run.user_id, run.assistant_id]), AssistantMessage.status.in_(ACTIVE_STATES),
    )))
    for row in rows:
        row.status = "COMPLETED" if row.role == "user" and status == "FAILED" else status
        row.error_code = error_code if row.role == "assistant" else None
        row.updated_at = utcnow()
    if rows:
        conversation.version += 1
        conversation.updated_at = utcnow()
    _commit(db)


def cancel_message(db: Session, staff: Staff, conversation_id: str, message_id: str, *, request_id: str):
    actor = actor_context(db, staff.id, staff.company_id)
    conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    row = db.scalar(repo.messages(conversation).where(AssistantMessage.id == message_id, AssistantMessage.role == "assistant"))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND)
    if row.status in ACTIVE_STATES:
        _interrupt(db, conversation)
        _audit(db, actor, conversation, "message.cancel", request_id)
        _commit(db)
    return {"id": row.id, "status": row.status, "version": conversation.version}


def replay_message(db: Session, run: Run):
    actor = actor_context(db, run.staff_id, run.company_id)
    conversation = repo.owned_conversation(db, run.company_id, run.staff_id, run.conversation_id, lock=True)
    _interrupt(db, conversation, expired_only=True)
    _commit(db)
    row = db.scalar(repo.messages(conversation).where(AssistantMessage.id == run.assistant_id))
    if row is None:
        raise BizException(ErrorCode.CONFLICT, "消息已清空")
    return message_contract(db, actor, row), conversation.version


def cleanup_expired(db: Session, *, now: datetime | None = None, limit: int = 100) -> dict:
    """周期清理独占消息正文；保留无正文幂等墓碑，防止旧请求复活。"""
    now = now or utcnow()
    retention = get_settings().assistant_conversation_retention_days
    if retention:
        rows = list(db.scalars(select(AssistantConversation).where(
            AssistantConversation.deleted_at.is_(None),
            AssistantConversation.updated_at <= now - timedelta(days=retention),
        ).order_by(AssistantConversation.id).limit(limit).with_for_update()))
        for row in rows:
            _interrupt(db, row)
            row.status, row.deleted_at, row.is_pinned = "DELETED", now, False
            row.cleared_through_seq = row.latest_message_seq
            row.version += 1
            from app.services.assistant_attachments import retire_conversation
            retire_conversation(db, row.id, now + timedelta(days=get_settings().assistant_deleted_retention_days))
            db.execute(update(AssistantMessage).where(
                AssistantMessage.conversation_id == row.id, AssistantMessage.company_id == row.company_id,
                AssistantMessage.staff_id == row.staff_id, AssistantMessage.purge_after.is_(None),
            ).values(purge_after=now + timedelta(days=get_settings().assistant_deleted_retention_days)))
    ids = list(db.scalars(select(AssistantMessage.id).where(AssistantMessage.purge_after <= now).order_by(AssistantMessage.purge_after).limit(limit).with_for_update()))
    if ids:
        db.execute(update(AssistantMessage).where(AssistantMessage.id.in_(ids)).values(
            content="", sources=[], page_context=None, visibility_guard=None,
            login_fingerprint="", purge_after=None, status="INTERRUPTED",
        ))
    deleted = list(db.scalars(select(AssistantConversation).where(
        AssistantConversation.status == "DELETED",
        AssistantConversation.deleted_at <= now - timedelta(days=get_settings().assistant_deleted_retention_days),
        AssistantConversation.title != "已删除会话",
    ).limit(limit).with_for_update()))
    for row in deleted:
        row.title = "已删除会话"
    _commit(db)
    return {"messages_purged": len(ids), "conversations_redacted": len(deleted)}
