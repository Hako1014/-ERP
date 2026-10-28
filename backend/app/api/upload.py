from io import BytesIO

from fastapi import APIRouter, Depends, File, UploadFile
from PIL import Image

from app.core.config import get_settings
from app.api.deps import get_current_staff
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.services.storage import Storage, get_storage

router = APIRouter(tags=["文件上传"])

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


def _extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _is_valid_image(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def validate_and_save(
    data: bytes,
    filename: str | None,
    storage: Storage,
    max_bytes: int,
) -> str:
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise BizException(ErrorCode.BAD_REQUEST, "不支持的文件类型，仅支持图片")
    if len(data) > max_bytes:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"文件大小不能超过 {max_bytes // (1024 * 1024)}MB"
        )
    if not _is_valid_image(data):
        raise BizException(ErrorCode.BAD_REQUEST, "文件内容不是有效图片")
    return storage.save(data, ext)


@router.post("/upload", response_model=Result[dict])
async def upload(file: UploadFile = File(...), staff=Depends(get_current_staff)):
    data = await file.read()
    settings = get_settings()
    url = validate_and_save(
        data,
        file.filename,
        get_storage(),
        settings.max_upload_size_mb * 1024 * 1024,
    )
    return Result.ok({"url": url, "filename": file.filename, "size": len(data)})
