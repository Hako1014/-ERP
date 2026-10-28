import functools
import inspect
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.data_scope import DATA_SCOPE_POLICY_KEY, QUERY_COMPANY_SCOPE_KEY, ScopePolicy
from app.db.session import get_current_session
from app.models.audit_log import AuditLog


def _serialize_detail(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(detail)


def write_audit(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    summary: str | None = None,
    reason: str | None = None,
    detail: Any = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    company_id: int | None = None,
) -> AuditLog:
    """在当前业务事务内写入审计记录，不单独提交，随业务事务一并提交/回滚。"""
    if company_id is None:
        company_id = session.info.get(QUERY_COMPANY_SCOPE_KEY)
    if company_id is None:
        policy = session.info.get(DATA_SCOPE_POLICY_KEY)
        if isinstance(policy, ScopePolicy):
            company_id = policy.company_id
    entry = AuditLog(
        company_id=company_id,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        reason=reason,
        detail=_serialize_detail(detail),
    )
    session.add(entry)
    return entry


def audit(
    action: str,
    target_type: str,
    *,
    target_id_getter: Callable[[Any], str | None] | None = None,
) -> Callable:
    """审计装饰器：函数成功返回后，在当前事务内写入审计记录。"""

    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                _record(action, target_type, target_id_getter, result)
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            _record(action, target_type, target_id_getter, result)
            return result

        return sync_wrapper

    return decorator


def _record(
    action: str,
    target_type: str,
    target_id_getter: Callable[[Any], str | None] | None,
    result: Any,
) -> None:
    session = get_current_session()
    if session is None:
        raise RuntimeError("审计装饰器要求当前上下文存在数据库会话（需在事务内调用）")
    target_id = target_id_getter(result) if target_id_getter else None
    write_audit(session, action=action, target_type=target_type, target_id=target_id)
