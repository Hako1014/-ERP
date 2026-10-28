import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.data_scope import initialize_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.query_access import current_actor_access
from app.core.security import decode_token
from app.db.session import get_db
from app.models.org import Staff
from app.repositories.org_repo import get_staff_by_id

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Staff:
    if credentials is None:
        raise BizException(ErrorCode.UNAUTHORIZED, "未登录")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise BizException(ErrorCode.UNAUTHORIZED, "登录已过期或无效")
    staff_id = payload.get("staff_id")
    if not staff_id:
        raise BizException(ErrorCode.UNAUTHORIZED, "无效的登录凭证")
    staff = get_staff_by_id(db, staff_id)
    if staff is None or not staff.is_active:
        raise BizException(ErrorCode.UNAUTHORIZED, "用户不存在")
    initialize_scope_policy(db, staff)
    return staff


def require_permission(*required: str):
    """依赖工厂：校验当前用户是否拥有任一所需权限，否则返回 403。"""

    def checker(
        staff: Staff = Depends(get_current_staff),
        db: Session = Depends(get_db),
    ) -> Staff:
        _, _, codes = current_actor_access(db, staff.id)
        if "*" in codes or any(code in codes for code in required):
            return staff
        raise BizException(ErrorCode.FORBIDDEN, "无权限执行该操作")

    return checker
