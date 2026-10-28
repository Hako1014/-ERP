from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode


def validate_new_password(password: str) -> str:
    """仅约束新设置的密码；历史密码仍可登录，避免锁死已有账号。"""
    has_letter = any("a" <= c.lower() <= "z" for c in password)
    has_digit = any("0" <= c <= "9" for c in password)
    if not 8 <= len(password) <= 64 or not has_letter or not has_digit:
        raise BizException(ErrorCode.BAD_REQUEST, "密码需为 8-64 位，且同时包含字母和数字")
    return password


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(staff_id: int, name: str, roles: list[str]) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(staff_id),
        "staff_id": staff_id,
        "name": name,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
