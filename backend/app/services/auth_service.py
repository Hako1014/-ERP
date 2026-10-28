import secrets

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.security import hash_password, validate_new_password, verify_password
from app.core.audit import write_audit
from app.db.redis import get_redis
from app.models.org import Staff
from app.integrations.sms import get_sms_adapter
from app.repositories.org_repo import get_staff_by_account, get_staff_by_phone


def _sms_key(phone: str) -> str:
    return f"sms:reset:{phone}"


def _cooldown_key(phone: str) -> str:
    return f"sms:reset:cooldown:{phone}"


def _attempts_key(phone: str) -> str:
    return f"sms:reset:attempts:{phone}"


def authenticate(db: Session, account: str, password: str) -> Staff:
    staff = get_staff_by_account(db, account)
    if staff is None or not staff.password_hash:
        raise BizException(ErrorCode.UNAUTHORIZED, "账号或密码错误")
    if not verify_password(password, staff.password_hash):
        raise BizException(ErrorCode.UNAUTHORIZED, "账号或密码错误")
    if not staff.is_active:
        raise BizException(ErrorCode.FORBIDDEN, "账号已停用")
    return staff


def send_reset_code(db: Session, phone: str) -> str:
    staff = get_staff_by_phone(db, phone)
    if staff is None:
        raise BizException(ErrorCode.NOT_FOUND, "手机号未注册")
    cache = get_redis()
    if not cache.set(_cooldown_key(phone), "1", ex=60, nx=True):
        raise BizException(ErrorCode.BAD_REQUEST, "验证码发送过于频繁，请稍后重试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        get_sms_adapter().send_reset_code(phone, code)
        cache.set(_sms_key(phone), code, ex=get_settings().sms_code_ttl_seconds)
        cache.delete(_attempts_key(phone))
    except Exception as exc:
        cache.delete(_cooldown_key(phone))
        raise BizException(ErrorCode.CONFLICT, "验证码发送失败") from exc
    return code


def reset_password_with_code(db: Session, phone: str, code: str, new_password: str) -> None:
    validate_new_password(new_password)
    staff = get_staff_by_phone(db, phone)
    if staff is None:
        raise BizException(ErrorCode.NOT_FOUND, "手机号未注册")
    cache = get_redis()
    attempts = cache.incr(_attempts_key(phone))
    if attempts == 1:
        cache.expire(_attempts_key(phone), get_settings().sms_code_ttl_seconds)
    if attempts > 5:
        raise BizException(ErrorCode.BAD_REQUEST, "验证码尝试次数过多，请重新获取")
    saved = cache.get(_sms_key(phone))
    if not saved or saved != code:
        raise BizException(ErrorCode.BAD_REQUEST, "验证码错误或已过期")
    staff.password_hash = hash_password(new_password)
    write_audit(
        db, action="RESET_PASSWORD", target_type="staff", target_id=str(staff.id),
        summary="员工通过验证码重置密码", actor_id=staff.id, actor_name=staff.name,
    )
    cache.delete(_sms_key(phone), _attempts_key(phone), _cooldown_key(phone))
    db.commit()
