"""TASK-080：只保存助手会话元数据，不保存对话原文。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

from app.core.errors import BizException, ErrorCode

MAX_ACTIVE_SESSIONS = 20_000


@dataclass(frozen=True)
class AssistantSession:
    session_id: UUID
    staff_id: int
    company_id: int
    login_session_id: str
    version: int
    expires_at: datetime
    deadline: float
    conversation_id: UUID | None = None


@dataclass(frozen=True)
class AssistantSessionContract:
    session_id: UUID
    version: int
    expires_at: datetime


class AssistantSessionRegistry:
    """进程内短期会话登记簿；保存归属/版本/过期时间，不持有消息内容。"""

    def __init__(self, *, clock=monotonic, utcnow=lambda: datetime.now(timezone.utc)) -> None:
        self._clock = clock
        self._utcnow = utcnow
        self._lock = Lock()
        self._sessions: dict[UUID, AssistantSession] = {}

    def create(
        self,
        *,
        staff_id: int,
        company_id: int,
        login_session_id: str,
        ttl_seconds: int,
    ) -> AssistantSessionContract:
        with self._lock:
            self._prune_expired()
            if len(self._sessions) >= MAX_ACTIVE_SESSIONS:
                raise BizException(ErrorCode.AI_RATE_LIMITED, "助手临时会话数量已达上限，请稍后重试")
            session = self._new_session(
                session_id=uuid4(),
                staff_id=staff_id,
                company_id=company_id,
                login_session_id=login_session_id,
                version=1,
                ttl_seconds=ttl_seconds,
            )
            self._sessions[session.session_id] = session
            return self._contract(session)

    def clear(
        self,
        *,
        session_id: UUID,
        version: int,
        staff_id: int,
        company_id: int,
        login_session_id: str,
        ttl_seconds: int,
    ) -> AssistantSessionContract:
        with self._lock:
            session = self._get_owned(
                session_id=session_id,
                staff_id=staff_id,
                company_id=company_id,
                login_session_id=login_session_id,
            )
            if session.version != version:
                raise BizException(
                    ErrorCode.AI_SESSION_VERSION_CONFLICT,
                    "助手会话已更新，请刷新后重试",
                )
            refreshed = self._new_session(
                session_id=session_id,
                staff_id=staff_id,
                company_id=company_id,
                login_session_id=login_session_id,
                version=session.version + 1,
                ttl_seconds=ttl_seconds,
            )
            refreshed = replace(refreshed, conversation_id=session.conversation_id)
            self._sessions[session_id] = refreshed
            return self._contract(refreshed)

    def validate(
        self,
        *,
        session_id: UUID,
        version: int,
        staff_id: int,
        company_id: int,
        login_session_id: str,
    ) -> None:
        with self._lock:
            session = self._get_owned(
                session_id=session_id,
                staff_id=staff_id,
                company_id=company_id,
                login_session_id=login_session_id,
            )
            if session.version != version:
                raise BizException(
                    ErrorCode.AI_SESSION_VERSION_CONFLICT,
                    "助手会话已清空或更新，请重新提问",
                )

    def bind_conversation(
        self, *, session_id: UUID, version: int, staff_id: int, company_id: int,
        login_session_id: str, conversation_id: UUID,
    ) -> None:
        """一个登录期运行 session 只能服务一个持久会话，避免跨会话复用。"""
        with self._lock:
            session = self._get_owned(session_id=session_id, staff_id=staff_id,
                                      company_id=company_id, login_session_id=login_session_id)
            if session.version != version:
                raise BizException(ErrorCode.AI_SESSION_VERSION_CONFLICT)
            if session.conversation_id not in (None, conversation_id):
                raise BizException(ErrorCode.CONFLICT, "运行会话已绑定其他会话，请新建运行会话")
            self._sessions[session_id] = replace(session, conversation_id=conversation_id)

    def _get_owned(
        self,
        *,
        session_id: UUID,
        staff_id: int,
        company_id: int,
        login_session_id: str,
    ) -> AssistantSession:
        session = self._sessions.get(session_id)
        requested_session_expired = bool(
            session is not None and session.deadline <= self._clock()
        )
        # 惰性回收登记簿中的其他过期元数据；原文从不在服务端保存。
        self._prune_expired()
        if requested_session_expired:
            raise BizException(ErrorCode.AI_SESSION_EXPIRED, "助手会话已过期，临时对话已清理")
        session = self._sessions.get(session_id)
        if session is None:
            raise BizException(ErrorCode.AI_SESSION_INVALID, "助手会话无效，请新建会话")
        if (
            session.staff_id != staff_id
            or session.company_id != company_id
            or session.login_session_id != login_session_id
        ):
            raise BizException(ErrorCode.FORBIDDEN, "无权访问此助手会话")
        return session

    def _new_session(
        self,
        *,
        session_id: UUID,
        staff_id: int,
        company_id: int,
        login_session_id: str,
        version: int,
        ttl_seconds: int,
    ) -> AssistantSession:
        now = self._utcnow()
        return AssistantSession(
            session_id=session_id,
            staff_id=staff_id,
            company_id=company_id,
            login_session_id=login_session_id,
            version=version,
            expires_at=now + timedelta(seconds=ttl_seconds),
            deadline=self._clock() + ttl_seconds,
        )

    def _contract(self, session: AssistantSession) -> AssistantSessionContract:
        return AssistantSessionContract(
            session_id=session.session_id,
            version=session.version,
            expires_at=session.expires_at,
        )

    def _prune_expired(self) -> None:
        now = self._clock()
        self._sessions = {
            session_id: session
            for session_id, session in self._sessions.items()
            if session.deadline > now
        }
