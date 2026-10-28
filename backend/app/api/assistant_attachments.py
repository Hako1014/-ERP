"""所有附件操作均需 Bearer 登录与明确会话，不暴露磁盘路径。"""

from uuid import UUID
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_staff
from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.db.session import get_db
from app.services import assistant_attachments as service
from app.services.assistant_attachment_files import PrivateAttachmentStorage, policy, preview

class BoundedUploadRoute(APIRoute):
    """在 multipart 写入临时文件前限制整个请求，而不只限制解析后的文件。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method != "POST" or request.url.path.rstrip("/") != "/assistant/attachments":
                return await handler(request)
            maximum = policy()["max_file_bytes"] + 64 * 1024
            try:
                declared = int(request.headers.get("content-length", "0"))
            except ValueError:
                raise BizException(ErrorCode.VALIDATION_ERROR, "上传请求长度无效") from None
            if declared > maximum:
                raise BizException(ErrorCode.VALIDATION_ERROR, "文件或上传请求超过大小限制")
            size = 0

            async def receive():
                nonlocal size
                event = await request.receive()
                size += len(event.get("body", b""))
                if size > maximum:
                    raise HTTPException(status_code=422, detail="文件或上传请求超过大小限制")
                return event

            return await handler(Request(request.scope, receive))

        return bounded


router = APIRouter(prefix="/assistant/attachments", tags=["AI 助手私有附件"], route_class=BoundedUploadRoute)
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer"}


@router.get("/policy", response_model=Result[dict])
def attachment_policy(response: Response, staff=Depends(get_current_staff)):
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(policy())


@router.post("", response_model=Result[dict])
async def upload(response: Response, conversation_id: UUID = Form(...), file: UploadFile = File(...),
                 staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    # 分块读取并有界拒绝；不信任 Content-Type / Content-Length。
    chunks, size, maximum = [], 0, policy()["max_file_bytes"]
    try:
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > maximum:
                raise BizException(ErrorCode.VALIDATION_ERROR, "文件超过单文件大小限制")
            chunks.append(chunk)
        result = await run_in_threadpool(service.upload, db, staff, str(conversation_id), b"".join(chunks), file.filename)
        response.headers.update(PRIVATE_HEADERS)
        return Result.ok(result)
    finally:
        await file.close()


@router.get("", response_model=Result[list])
def pending(response: Response, conversation_id: UUID = Query(...), staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(service.list_pending(db, staff, str(conversation_id)))


class SaveAttachments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    version: int = Field(ge=1)
    request_id: UUID
    client_message_id: UUID
    attachment_ids: list[UUID] = Field(min_length=1, max_length=10)
    content: str = Field(default="", max_length=4000)


@router.post("/messages", response_model=Result[dict])
def save(payload: SaveAttachments, response: Response, staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(service.save_message(db, staff, str(payload.conversation_id), version=payload.version,
        client_message_id=str(payload.client_message_id), request_id=str(payload.request_id),
        attachment_ids=[str(item) for item in payload.attachment_ids], content=payload.content.strip()))


@router.get("/{attachment_id}", response_model=Result[dict])
def metadata(attachment_id: UUID, response: Response, conversation_id: UUID = Query(...), staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    row, _ = service.owned(db, staff, str(conversation_id), str(attachment_id))
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(service.contract(row))


@router.get("/{attachment_id}/preview", response_model=Result[dict])
def safe_preview(attachment_id: UUID, response: Response, conversation_id: UUID = Query(...), staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    row, _ = service.owned(db, staff, str(conversation_id), str(attachment_id))
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(preview(PrivateAttachmentStorage().read(row.storage_key), row.media_type))


@router.get("/{attachment_id}/content")
def content(attachment_id: UUID, conversation_id: UUID = Query(...), staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    row, _ = service.owned(db, staff, str(conversation_id), str(attachment_id))
    return Response(PrivateAttachmentStorage().read(row.storage_key), media_type=row.media_type,
        headers={**PRIVATE_HEADERS, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.display_name, safe='')}"})


@router.delete("/{attachment_id}", response_model=Result[dict])
def remove(attachment_id: UUID, response: Response, conversation_id: UUID = Query(...), staff=Depends(get_current_staff), db: Session = Depends(get_db)):
    response.headers.update(PRIVATE_HEADERS)
    return Result.ok(service.remove(db, staff, str(conversation_id), str(attachment_id)))
