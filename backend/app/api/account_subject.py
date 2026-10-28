from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import account_subject_service

router = APIRouter(tags=["财务管理-会计科目与项目核算"])

read_finance = require_permission("module:finance")
manage_finance = require_permission("finance:manage")


class ProjectCategoryCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


class ProjectCategoryUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


class SubjectCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    balance_direction: str = Field(default="DEBIT", max_length=8)
    is_auxiliary: bool = False
    is_qty_amount: bool = False
    measure_unit: str | None = Field(default=None, max_length=32)
    is_cash: bool = False
    is_bank: bool = False
    is_journal: bool = False
    is_cash_equivalent: bool = False
    project_category_id: int | None = Field(default=None, ge=1)
    is_enabled: bool = True


class SubjectUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    balance_direction: str | None = Field(default=None, max_length=8)
    is_auxiliary: bool | None = None
    is_qty_amount: bool | None = None
    measure_unit: str | None = Field(default=None, max_length=32)
    is_cash: bool | None = None
    is_bank: bool | None = None
    is_journal: bool | None = None
    is_cash_equivalent: bool | None = None
    project_category_id: int | None = Field(default=None, ge=1)
    is_enabled: bool | None = None


class SeedImportRequest(BaseModel):
    update_existing: bool = False


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


@router.get("/account-subjects/meta", response_model=Result[dict])
def get_subject_meta(
    staff: Staff = Depends(read_finance),
):
    del staff
    return Result.ok(account_subject_service.list_meta())


@router.post("/account-subjects/import-seed", response_model=Result[dict])
def import_kingdee_subjects(
    req: SeedImportRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    result = account_subject_service.import_kingdee_subjects(
        db,
        staff=staff,
        update_existing=req.update_existing,
    )
    return Result.ok(result, message="金蝶科目种子已导入")


@router.get("/account-subjects", response_model=Result[dict])
def list_subjects(
    keyword: str | None = Query(default=None, max_length=64),
    category: str | None = None,
    balance_direction: str | None = None,
    is_enabled: bool | None = None,
    is_auxiliary: bool | None = None,
    is_qty_amount: bool | None = None,
    project_category_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(
        account_subject_service.list_subjects(
            db,
            keyword=keyword,
            category=category,
            balance_direction=balance_direction,
            is_enabled=is_enabled,
            is_auxiliary=is_auxiliary,
            is_qty_amount=is_qty_amount,
            project_category_id=project_category_id,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/account-subjects", response_model=Result[dict])
def create_subject(
    req: SubjectCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    subject = account_subject_service.create_subject(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        account_subject_service.subject_dict(db, subject),
        message="会计科目已创建",
    )


@router.get("/account-subjects/{subject_id}", response_model=Result[dict])
def get_subject(
    subject_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    subject = account_subject_service.get_subject_or_404(db, subject_id)
    return Result.ok(account_subject_service.subject_dict(db, subject))


@router.put("/account-subjects/{subject_id}", response_model=Result[dict])
def update_subject(
    subject_id: int,
    req: SubjectUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    subject = account_subject_service.update_subject(
        db,
        subject_id=subject_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        account_subject_service.subject_dict(db, subject),
        message="会计科目已更新",
    )


@router.delete("/account-subjects/{subject_id}", response_model=Result[dict])
def delete_subject(
    subject_id: int,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    account_subject_service.delete_subject(
        db, subject_id=subject_id, staff=staff
    )
    return Result.ok(message="会计科目已删除")


@router.get("/project-categories", response_model=Result[list[dict]])
def list_project_categories(
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    return Result.ok(account_subject_service.list_project_categories(db))


@router.post("/project-categories", response_model=Result[dict])
def create_project_category(
    req: ProjectCategoryCreateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    category = account_subject_service.create_project_category(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        account_subject_service.project_category_dict(category),
        message="项目核算类别已创建",
    )


@router.get(
    "/project-categories/{category_id}",
    response_model=Result[dict],
)
def get_project_category(
    category_id: int,
    staff: Staff = Depends(read_finance),
    db: Session = Depends(get_db),
):
    del staff
    category = account_subject_service.get_project_category_or_404(
        db, category_id
    )
    return Result.ok(
        account_subject_service.project_category_dict(category)
    )


@router.put(
    "/project-categories/{category_id}",
    response_model=Result[dict],
)
def update_project_category(
    category_id: int,
    req: ProjectCategoryUpdateRequest,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    category = account_subject_service.update_project_category(
        db,
        category_id=category_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        account_subject_service.project_category_dict(category),
        message="项目核算类别已更新",
    )


@router.delete(
    "/project-categories/{category_id}",
    response_model=Result[dict],
)
def delete_project_category(
    category_id: int,
    staff: Staff = Depends(manage_finance),
    db: Session = Depends(get_db),
):
    account_subject_service.delete_project_category(
        db, category_id=category_id, staff=staff
    )
    return Result.ok(message="项目核算类别已删除")
