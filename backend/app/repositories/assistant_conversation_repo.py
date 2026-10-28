"""所有私人资源查询同时约束公司和本人；不接受管理员绕过。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizException, ErrorCode
from app.models.assistant_conversation import AssistantConversation, AssistantMessage


def conversations(company_id: int, staff_id: int):
    return select(AssistantConversation).where(
        AssistantConversation.company_id == company_id,
        AssistantConversation.staff_id == staff_id,
        AssistantConversation.deleted_at.is_(None),
        AssistantConversation.status != "DELETED",
    )


def owned_conversation(db: Session, company_id: int, staff_id: int, conversation_id: str, *, lock: bool = False):
    stmt = conversations(company_id, staff_id).where(AssistantConversation.id == conversation_id)
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt.execution_options(populate_existing=True))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "会话不存在或不可见")
    return row


def messages(conversation: AssistantConversation):
    return select(AssistantMessage).where(
        AssistantMessage.company_id == conversation.company_id,
        AssistantMessage.staff_id == conversation.staff_id,
        AssistantMessage.conversation_id == conversation.id,
        AssistantMessage.seq > conversation.cleared_through_seq,
        AssistantMessage.purge_after.is_(None),
    )
