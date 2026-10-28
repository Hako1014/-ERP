"""本人、公司、会话三重授权的私有附件；保存不等于发往模型。"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.models.assistant_attachment import AssistantAttachment as Attachment
from app.models.assistant_conversation import AssistantMessage
from app.models.org import Staff
from app.repositories import assistant_conversation_repo as repo
from app.services import assistant_conversations as chats
from app.services.assistant_attachment_files import PrivateAttachmentStorage, policy, validate


def contract(row):
    return {"id": row.id, "conversation_id": row.conversation_id, "message_id": row.message_id,
            "display_name": row.display_name, "media_type": row.media_type, "size": row.size,
            "status": row.status, "purpose": row.purpose, "created_at": row.created_at,
            "expires_at": row.purge_after if row.message_id is None else None,
            "model_sent": False}


def owned(db, staff, conversation_id, attachment_id, *, lock=False):
    actor = chats.actor_context(db, staff.id, staff.company_id)
    conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=lock)
    stmt = select(Attachment).where(
        Attachment.id == attachment_id, Attachment.company_id == actor.company_id,
        Attachment.staff_id == actor.staff_id, Attachment.conversation_id == conversation.id,
        Attachment.purpose == "CHAT", Attachment.status == "READY",
        or_(Attachment.purge_after.is_(None), Attachment.purge_after > chats.utcnow()),
    ).execution_options(populate_existing=True)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "附件不存在或不可见")
    if row.message_id:
        message = db.scalar(repo.messages(conversation).where(AssistantMessage.id == row.message_id))
        if message is None or message.role != "user":
            raise BizException(ErrorCode.NOT_FOUND, "附件不存在或不可见")
    return row, conversation


def upload(db, staff, conversation_id, data, name):
    # 非本人会话在内容解析前即拒绝；解析期间不持有写锁，落盘前仍重新验证。
    actor = chats.actor_context(db, staff.id, staff.company_id)
    repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id)
    data, name, media_type = validate(data, name)
    actor = chats.actor_context(db, staff.id, staff.company_id)
    # 先锁员工再锁会话；同一员工跨会话并发上传也不能绕过容量配额。
    db.scalar(select(Staff).where(Staff.id == actor.staff_id, Staff.company_id == actor.company_id).with_for_update())
    conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    if conversation.status != "ACTIVE":
        raise BizException(ErrorCode.CONFLICT, "归档会话须恢复后才能上传")
    limits = policy()
    used = db.scalar(select(func.coalesce(func.sum(Attachment.size), 0)).where(
        Attachment.company_id == actor.company_id, Attachment.staff_id == actor.staff_id,
        Attachment.status != "PURGED"))
    if used + len(data) > limits["owner_quota_bytes"]:
        raise BizException(ErrorCode.CONFLICT, "附件容量配额不足，请清理附件并等待回收")
    pending = list(db.scalars(select(Attachment).where(
        Attachment.company_id == actor.company_id, Attachment.staff_id == actor.staff_id,
        Attachment.conversation_id == conversation.id, Attachment.message_id.is_(None),
        Attachment.status.in_(("READY", "SCANNING")), Attachment.purge_after > chats.utcnow())))
    if len(pending) >= limits["max_files"] or sum(item.size for item in pending) + len(data) > limits["max_total_bytes"]:
        raise BizException(ErrorCode.VALIDATION_ERROR, "待保存附件数量或总大小超限，请先移除或保存当前附件")
    storage = PrivateAttachmentStorage()
    key = uuid4().hex + uuid4().hex
    now = chats.utcnow()
    row = Attachment(id=str(uuid4()), company_id=actor.company_id, staff_id=actor.staff_id,
        conversation_id=conversation.id, purpose="CHAT", display_name=name, media_type=media_type,
        size=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_key=key, status="SCANNING",
        created_at=now, updated_at=now,
        purge_after=now + timedelta(hours=get_settings().assistant_orphan_hours))
    db.add(row)
    chats._audit(db, actor, conversation, "file.reserve")
    # 先登记再写文件，崩溃时也有可清理的孤立文件记录。
    chats._commit(db)
    try:
        storage.save(data, key=key)
        # 写磁盘期间会话可能被清空/删除，必须重新锁定并检查预约状态。
        owned_conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation.id, lock=True)
        db.refresh(row, with_for_update=True)
        if row.status != "SCANNING" or owned_conversation.status != "ACTIVE":
            raise BizException(ErrorCode.CONFLICT, "会话状态已变化，请重新上传")
        row.status = "READY"
        row.updated_at = chats.utcnow()
        chats._commit(db)
    except Exception:
        db.rollback()
        storage.remove(key)
        db.execute(update(Attachment).where(Attachment.id == row.id).values(status="DELETED", purge_after=chats.utcnow(), updated_at=chats.utcnow()))
        chats._commit(db)
        raise
    return contract(row)


def list_pending(db, staff, conversation_id):
    actor = chats.actor_context(db, staff.id, staff.company_id)
    repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id)
    return [contract(row) for row in db.scalars(select(Attachment).where(
        Attachment.company_id == actor.company_id, Attachment.staff_id == actor.staff_id,
        Attachment.conversation_id == conversation_id, Attachment.purpose == "CHAT",
        Attachment.status == "READY", Attachment.message_id.is_(None), Attachment.purge_after > chats.utcnow(),
    ).order_by(Attachment.created_at, Attachment.id))]


def message_attachments(db, actor, message_id):
    return [contract(row) for row in db.scalars(select(Attachment).where(
        Attachment.company_id == actor.company_id, Attachment.staff_id == actor.staff_id,
        Attachment.message_id == message_id, Attachment.purpose == "CHAT", Attachment.status == "READY",
        Attachment.purge_after.is_(None),
    ).order_by(Attachment.created_at, Attachment.id))]


def remove(db, staff, conversation_id, attachment_id):
    row, conversation = owned(db, staff, conversation_id, attachment_id, lock=True)
    if row.message_id:
        raise BizException(ErrorCode.CONFLICT, "已保存到消息的附件不能单独移除，请清空或删除所属会话")
    row.status, row.purge_after = "DELETED", chats.utcnow()
    row.updated_at = chats.utcnow()
    chats._audit(db, chats.actor_context(db, staff.id, staff.company_id), conversation, "file.delete")
    chats._commit(db)
    # 已软删除立即不可访问；磁盘清理由周期任务重试，接口不误报回收成功。
    return {"id": row.id, "status": row.status}


def save_message(db, staff, conversation_id, *, version, client_message_id, request_id, attachment_ids, content):
    actor = chats.actor_context(db, staff.id, staff.company_id)
    conversation = repo.owned_conversation(db, actor.company_id, actor.staff_id, conversation_id, lock=True)
    body_digest = chats.digest({"kind": "attachments_only", "ids": sorted(attachment_ids), "content": content})
    existing = db.scalar(select(AssistantMessage).where(
        AssistantMessage.company_id == actor.company_id, AssistantMessage.staff_id == actor.staff_id,
        AssistantMessage.conversation_id == conversation.id, AssistantMessage.client_message_id == client_message_id))
    if existing:
        if existing.payload_digest != body_digest or existing.purge_after or existing.seq <= conversation.cleared_through_seq:
            raise BizException(ErrorCode.CONFLICT, "幂等键已使用或消息已清空")
        return {"message": chats.message_contract(db, actor, existing), "version": conversation.version}
    chats._check_version(conversation, version)
    if conversation.status != "ACTIVE":
        raise BizException(ErrorCode.CONFLICT, "归档会话不能保存新附件")
    if db.scalar(repo.messages(conversation).where(AssistantMessage.status.in_(chats.ACTIVE_STATES)).limit(1)):
        raise BizException(ErrorCode.CONFLICT, "请先等待或停止当前回答")
    limits = policy()
    if not attachment_ids or len(set(attachment_ids)) != len(attachment_ids) or len(attachment_ids) > limits["max_files"]:
        raise BizException(ErrorCode.VALIDATION_ERROR, "附件数量无效或重复")
    rows = [owned(db, staff, conversation_id, item, lock=True)[0] for item in sorted(attachment_ids)]
    if any(row.message_id for row in rows) or sum(row.size for row in rows) > limits["max_total_bytes"]:
        raise BizException(ErrorCode.CONFLICT, "附件已关联其他消息或总大小超限")
    now = chats.utcnow()
    conversation.version += 1
    conversation.latest_message_seq += 1
    conversation.updated_at = now
    message = AssistantMessage(id=str(uuid4()), conversation_id=conversation.id, company_id=actor.company_id,
        staff_id=actor.staff_id, seq=conversation.latest_message_seq, role="user", content=content,
        status="COMPLETED", client_message_id=client_message_id, request_id=request_id, payload_digest=body_digest,
        run_version=conversation.version, session_id=str(uuid4()), session_version=1,
        login_fingerprint="", run_until=now, sources=[], created_at=now, updated_at=now)
    db.add(message)
    db.flush()
    for row in rows:
        row.message_id, row.purge_after = message.id, None
    chats._audit(db, actor, conversation, "file.save", request_id)
    chats._commit(db)
    return {"message": chats.message_contract(db, actor, message), "version": conversation.version}


def retire_conversation(db, conversation_id, purge_at):
    db.execute(update(Attachment).where(Attachment.conversation_id == conversation_id,
        Attachment.purpose == "CHAT", Attachment.status.in_(("READY", "SCANNING"))).values(status="DELETED", purge_after=purge_at, updated_at=chats.utcnow()))
    from app.models.assistant_extraction import AssistantExtraction
    db.execute(update(AssistantExtraction).where(
        AssistantExtraction.conversation_id == conversation_id,
        AssistantExtraction.status.in_(("RUNNING", "COMPLETED", "FAILED", "CANCELLED")),
    ).values(status="DELETED", result=None, error_category="conversation_cleared", purge_after=purge_at, updated_at=chats.utcnow()))


def cleanup_expired(db: Session, *, now=None, limit=1000, company_id=None):
    now = now or chats.utcnow()
    # 正式业务附件不在 CHAT 生命周期内；仅处理已登记且到期的随机键，绝不遍历公共目录。
    stmt = select(Attachment).where(Attachment.purpose == "CHAT",
        Attachment.status != "PURGED", Attachment.purge_after <= now,
    )
    if company_id is not None:
        stmt = stmt.where(Attachment.company_id == company_id)
    rows = list(db.scalars(stmt.order_by(Attachment.purge_after).limit(limit).with_for_update()))
    storage, purged, failed = PrivateAttachmentStorage(), 0, 0
    for row in rows:
        try:
            storage.remove(row.storage_key)
        except (OSError, BizException):
            failed += 1
            continue
        row.status, row.display_name, row.sha256 = "PURGED", "已清理附件", ""
        row.updated_at = now
        purged += 1
    chats._commit(db)
    return {"attachments_purged": purged, "attachments_retry": failed}
