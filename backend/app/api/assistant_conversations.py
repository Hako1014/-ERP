"""私人工作台资源；旧短期助手接口继续兼容。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session, sessionmaker

from app.api import assistant as legacy
from app.api.deps import get_current_staff
from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.redis import RedisCache, get_redis
from app.db.session import get_db
from app.services import assistant_conversations as service
from app.services.assistant_conversation_stream import GuardedProvider, events

router = APIRouter(prefix="/assistant/conversations", tags=["AI 助手私人会话"])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="新会话", min_length=1, max_length=120)
    request_id: UUID

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("会话标题不能为空")
        return value.strip()


class ConversationVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    request_id: UUID


class ConversationUpdate(ConversationVersion):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    is_pinned: bool | None = Field(default=None, strict=True)
    status: Literal["ACTIVE", "ARCHIVED"] | None = None

    @model_validator(mode="after")
    def valid_changes(self):
        changed = self.model_fields_set - {"version", "request_id"}
        if not changed or any(getattr(self, key) is None for key in changed):
            raise ValueError("至少提交一个非空修改字段")
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("会话标题不能为空")
        return self


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    request_id: UUID
    client_message_id: UUID
    session_id: UUID
    session_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=4000)
    page_context: legacy.PageContext | None = None
    # TASK-106 才支持附件；本卡显式拒绝非空列表，避免静默丢失用户资料。
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=0)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


@router.post("", response_model=Result[dict])
def create_conversation(payload: ConversationCreate, staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.create_conversation(db, staff, payload.title, str(payload.request_id)))


@router.get("", response_model=Result[dict])
def list_conversations(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       q: str = Query("", max_length=120), status: Literal["ACTIVE", "ARCHIVED", "ALL"] = "ACTIVE",
                       staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.list_conversations(db, staff, page=page, page_size=page_size, q=q.strip(), status=status))


@router.get("/{conversation_id}", response_model=Result[dict])
def get_conversation(conversation_id: UUID, staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.get_conversation(db, staff, str(conversation_id)))


@router.patch("/{conversation_id}", response_model=Result[dict])
def update_conversation(conversation_id: UUID, payload: ConversationUpdate, staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True, exclude={"version", "request_id"})
    return Result.ok(service.update_conversation(db, staff, str(conversation_id), version=payload.version,
                                                changes=changes, request_id=str(payload.request_id)))


@router.delete("/{conversation_id}", response_model=Result[dict])
def delete_conversation(conversation_id: UUID, version: int = Query(..., ge=1), request_id: UUID = Query(...),
                        staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.clear_or_delete(db, staff, str(conversation_id), version=version, request_id=str(request_id), deleting=True))


@router.post("/{conversation_id}/clear", response_model=Result[dict])
def clear_conversation(conversation_id: UUID, payload: ConversationVersion, staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.clear_or_delete(db, staff, str(conversation_id), version=payload.version, request_id=str(payload.request_id)))


@router.get("/{conversation_id}/messages", response_model=Result[dict])
def list_messages(conversation_id: UUID, before_seq: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=50),
                  staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.list_messages(db, staff, str(conversation_id), before_seq=before_seq, limit=limit))


@router.post("/{conversation_id}/messages/{message_id}/cancel", response_model=Result[dict])
def cancel_message(conversation_id: UUID, message_id: UUID, payload: CancelRequest,
                   staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(service.cancel_message(db, staff, str(conversation_id), str(message_id), request_id=str(payload.request_id)))


@router.post("/{conversation_id}/messages/stream", response_class=StreamingResponse)
async def stream_message(conversation_id: UUID, payload: ConversationMessage, request: Request,
                         staff=Depends(get_current_staff), db: Session = Depends(get_db), redis: RedisCache = Depends(get_redis)):
    fingerprint, _ = legacy._login_session_context(request)
    # 所有权先于绑定；猜他人 UUID 不改变自己的运行登记簿。
    service.get_conversation(db, staff, str(conversation_id))
    legacy._session_registry.bind_conversation(session_id=payload.session_id, version=payload.session_version,
        staff_id=staff.id, company_id=staff.company_id, login_session_id=fingerprint, conversation_id=conversation_id)
    run, history, repeated = service.begin_message(db, staff, str(conversation_id), version=payload.version,
        client_message_id=str(payload.client_message_id), request_id=str(payload.request_id), content=payload.content,
        page_context=payload.page_context.model_dump(exclude_none=True) if payload.page_context else None,
        session_id=str(payload.session_id), session_version=payload.session_version, login_fingerprint=fingerprint,
        run_seconds=get_settings().assistant_total_timeout_seconds + 60)
    session_factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")

    def validate_login():
        current_fingerprint, _ = legacy._login_session_context(request)
        if current_fingerprint != fingerprint:
            raise BizException(ErrorCode.UNAUTHORIZED)
        legacy._session_registry.bind_conversation(session_id=payload.session_id, version=payload.session_version,
            staff_id=run.staff_id, company_id=run.company_id, login_session_id=fingerprint, conversation_id=conversation_id)

    async def start_stream(stream_db, check):
        # 只有数据库中当前仍授权的正文进入 history，客户端 history 字段直接 422 拒绝。
        actor = stream_db.get(type(staff), run.staff_id)
        provider = GuardedProvider(legacy.get_assistant_provider(actor, stream_db), check)
        trusted = legacy.AssistantStreamRequest(request_id=payload.request_id, session_id=payload.session_id,
            session_version=payload.session_version, message=payload.content,
            history=[legacy.HistoryMessage(**item) for item in history], page_context=payload.page_context)
        return await legacy.stream_message(trusted, request, staff=actor, db=stream_db, redis=redis, provider=provider)

    return StreamingResponse(events(run=run, session_factory=session_factory, validate_login=validate_login,
                                    start_stream=start_stream, repeated=repeated), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store, no-cache, no-transform", "X-Accel-Buffering": "no"})
