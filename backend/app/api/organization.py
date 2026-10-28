from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services.assistant_attachment_files import PrivateAttachmentStorage
from app.services import organization_directory as directory

router = APIRouter(prefix="/organization", tags=["组织人员目录"])
read_directory = require_permission("organization:read")
DIRECTORY_AVATAR_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Referrer-Policy": "no-referrer",
}


@router.get("/departments", response_model=Result[dict])
def list_departments(
    staff: Staff = Depends(read_directory),
    db: Session = Depends(get_db),
):
    return Result.ok({"items": directory.list_departments(db, staff.company_id)})


@router.get("/directory", response_model=Result[dict])
def list_directory(
    q: str | None = Query(default=None, max_length=64),
    department_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    staff: Staff = Depends(read_directory),
    db: Session = Depends(get_db),
):
    return Result.ok(directory.list_people(
        db,
        company_id=staff.company_id,
        department_id=department_id,
        keyword=q,
        page=page,
        page_size=page_size,
    ))


@router.get("/directory/{staff_id}", response_model=Result[dict])
def get_directory_person(
    staff_id: int,
    staff: Staff = Depends(read_directory),
    db: Session = Depends(get_db),
):
    return Result.ok(directory.get_person(db, company_id=staff.company_id, staff_id=staff_id))


@router.get("/directory/{staff_id}/avatar")
def get_directory_avatar(
    staff_id: int,
    staff: Staff = Depends(read_directory),
    db: Session = Depends(get_db),
):
    employee = directory.get_person_avatar(db, company_id=staff.company_id, staff_id=staff_id)
    content = PrivateAttachmentStorage().read(employee.avatar_storage_key)
    return Response(content, media_type=employee.avatar_media_type, headers=DIRECTORY_AVATAR_HEADERS)
