import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.response import Result
from app.core.config import get_settings
from app.core.data_scope import resolve_policy
from app.core.errors import BizException, ErrorCode
from app.core.ip_access import require_allowed_ip
from app.api.deps import get_current_staff
from app.core.permissions import permissions_for_roles, visible_modules
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.org import Company, Department, Position, Staff
from app.repositories.rbac_repo import list_staff_role_codes
from app.services.auth_service import authenticate, reset_password_with_code, send_reset_code
from app.services.assistant_attachment_files import PrivateAttachmentStorage, validate as validate_private_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    account: str | None = Field(default=None, min_length=3, max_length=64)
    phone: str | None = Field(default=None, min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=64)

    @model_validator(mode="after")
    def validate_account(self) -> "LoginRequest":
        if not self.account and not self.phone:
            raise ValueError("请输入登录账号")
        return self


class SendCodeRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")


class ResetPasswordRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=64)


class MyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=64)


AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}
PRIVATE_AVATAR_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Referrer-Policy": "no-referrer",
}


def _masked_phone(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) < 7:
        return "*" * len(value)
    return f"{value[:3]}****{value[-4:]}"


def _profile_contract(db: Session, staff: Staff) -> dict:
    company = db.get(Company, staff.company_id)
    department = db.get(Department, staff.department_id) if staff.department_id else None
    position = db.get(Position, staff.position_id) if staff.position_id else None
    display_name = (staff.display_name or "").strip() or staff.name
    return {
        "staff_id": staff.id,
        "name": staff.name,
        "display_name": display_name,
        "staff_no": staff.staff_no,
        "login_account": staff.login_account,
        "phone_masked": _masked_phone(staff.phone),
        "company_id": staff.company_id,
        "company_name": ((company.short_name or company.full_name) if company else None),
        "department_id": staff.department_id,
        "department_name": department.name if department else None,
        "position_id": staff.position_id,
        "position_name": position.name if position else None,
        "roles": list_staff_role_codes(db, staff.id),
        "data_scope": resolve_policy(db, staff).scope_type,
        "gender": staff.gender,
        "region": staff.region,
        "is_active": staff.is_active,
        "avatar_version": staff.avatar_sha256[:16] if staff.avatar_sha256 else None,
    }


@router.post("/login", response_model=Result[dict])
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    staff = authenticate(db, req.account or req.phone or "", req.password)
    require_allowed_ip(db, staff.company_id, request.client.host if request.client else None)
    roles = list_staff_role_codes(db, staff.id)
    token = create_access_token(staff.id, staff.name, roles)
    return Result.ok(
        {
            "token": token,
            "staff_id": staff.id,
            "name": (staff.display_name or "").strip() or staff.name,
            "formal_name": staff.name,
            "display_name": (staff.display_name or "").strip() or staff.name,
            "avatar_version": staff.avatar_sha256[:16] if staff.avatar_sha256 else None,
            "roles": roles,
        }
    )


@router.post("/forgot-password/send-code", response_model=Result[dict])
def send_code(req: SendCodeRequest, request: Request, db: Session = Depends(get_db)):
    code = send_reset_code(db, req.phone)
    settings = get_settings()
    local_debug = (
        settings.debug and settings.sms_mock_show_code
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
    )
    return Result.ok({"sent": True, **({"mock_code": code} if local_debug else {})})


@router.post("/forgot-password/reset", response_model=Result[dict])
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset_password_with_code(db, req.phone, req.code, req.new_password)
    return Result.ok()


@router.get("/me/permissions", response_model=Result[dict])
def my_permissions(staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    roles = list_staff_role_codes(db, staff.id)
    codes = permissions_for_roles(roles)
    company = db.get(Company, staff.company_id)
    return Result.ok(
        {
            "staff_id": staff.id,
            "name": (staff.display_name or "").strip() or staff.name,
            "formal_name": staff.name,
            "display_name": (staff.display_name or "").strip() or staff.name,
            "avatar_version": staff.avatar_sha256[:16] if staff.avatar_sha256 else None,
            "company_id": staff.company_id,
            "company_name": (
                (company.short_name or company.full_name) if company else None
            ),
            "roles": roles,
            "permissions": sorted(codes),
            "modules": visible_modules(codes),
        }
    )


@router.get("/me/profile", response_model=Result[dict])
def my_profile(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    return Result.ok(_profile_contract(db, staff))


@router.put("/me/profile", response_model=Result[dict])
def update_my_profile(
    payload: MyProfileUpdate,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise BizException(ErrorCode.BAD_REQUEST, "请至少修改一项个人资料")
    for field, value in values.items():
        if value is not None and not isinstance(value, str):
            raise BizException(ErrorCode.BAD_REQUEST, "个人资料格式不正确")
        cleaned = value.strip() if isinstance(value, str) else None
        setattr(staff, field, cleaned or None)
    db.commit()
    db.refresh(staff)
    return Result.ok(_profile_contract(db, staff), message="个人资料已保存")


@router.post("/me/avatar", response_model=Result[dict])
async def upload_my_avatar(
    response: Response,
    file: UploadFile = File(...),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    try:
        chunks: list[bytes] = []
        size = 0
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > AVATAR_MAX_BYTES:
                raise BizException(ErrorCode.VALIDATION_ERROR, "头像不能超过 5 MiB")
            chunks.append(chunk)
        raw = b"".join(chunks)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise BizException(ErrorCode.VALIDATION_ERROR, "头像仅支持 JPEG、PNG 或 WebP 图片")
        clean_data, _safe_filename, media_type = await run_in_threadpool(
            validate_private_file, raw, file.filename or "avatar.png"
        )
        if media_type not in AVATAR_MEDIA_TYPES or len(clean_data) > AVATAR_MAX_BYTES:
            raise BizException(ErrorCode.VALIDATION_ERROR, "头像格式或大小不符合要求")
        storage = PrivateAttachmentStorage()
        key = await run_in_threadpool(storage.save, clean_data)
        old_key = staff.avatar_storage_key
        staff.avatar_storage_key = key
        staff.avatar_media_type = media_type
        staff.avatar_size = len(clean_data)
        staff.avatar_sha256 = hashlib.sha256(clean_data).hexdigest()
        try:
            db.commit()
        except Exception:
            db.rollback()
            # 保留新文件以覆盖提交结果不明的情况，避免删掉可能已被数据库引用的文件。
            raise
        if old_key and old_key != key:
            try:
                await run_in_threadpool(storage.remove, old_key)
            except OSError:
                logger.warning("旧头像文件清理失败，员工 ID=%s", staff.id)
        db.refresh(staff)
        response.headers.update(PRIVATE_AVATAR_HEADERS)
        return Result.ok(_profile_contract(db, staff), message="头像已更新")
    finally:
        await file.close()


@router.get("/me/avatar")
def read_my_avatar(
    response: Response,
    staff: Staff = Depends(get_current_staff),
):
    if not staff.avatar_storage_key or staff.avatar_media_type not in AVATAR_MEDIA_TYPES:
        raise BizException(ErrorCode.NOT_FOUND, "尚未设置头像")
    content = PrivateAttachmentStorage().read(staff.avatar_storage_key)
    response.headers.update(PRIVATE_AVATAR_HEADERS)
    return Response(content, media_type=staff.avatar_media_type, headers=PRIVATE_AVATAR_HEADERS)


@router.delete("/me/avatar", response_model=Result[dict])
def delete_my_avatar(
    response: Response,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    old_key = staff.avatar_storage_key
    staff.avatar_storage_key = None
    staff.avatar_media_type = None
    staff.avatar_size = None
    staff.avatar_sha256 = None
    db.commit()
    if old_key:
        try:
            PrivateAttachmentStorage().remove(old_key)
        except OSError:
            logger.warning("头像文件清理失败，员工 ID=%s", staff.id)
    response.headers.update(PRIVATE_AVATAR_HEADERS)
    db.refresh(staff)
    return Result.ok(_profile_contract(db, staff), message="头像已移除")
