from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.core.data_scope import scope_conditions

from app.models.finance import AccountSubject, Voucher, VoucherItem


def get_subject_by_code(db: Session, code: str) -> AccountSubject | None:
    return db.scalar(select(AccountSubject).where(AccountSubject.code == code))


def get_voucher_by_no(db: Session, no: str) -> Voucher | None:
    return db.scalar(select(Voucher).where(Voucher.voucher_no == no))


def get_voucher_by_id(db: Session, voucher_id: int) -> Voucher | None:
    return db.get(Voucher, voucher_id)


def get_active_voucher_by_reference(
    db: Session, reference: str
) -> Voucher | None:
    return db.scalar(
        select(Voucher)
        .where(
            Voucher.reference == reference,
            Voucher.status != "INVALID",
        )
        .order_by(Voucher.id.desc())
    )


def list_voucher_items(db: Session, voucher_id: int) -> list[VoucherItem]:
    return list(
        db.scalars(
            select(VoucherItem)
            .where(VoucherItem.voucher_id == voucher_id)
            .order_by(VoucherItem.id)
        )
    )


def get_subjects_by_ids(
    db: Session, subject_ids: set[int]
) -> dict[int, AccountSubject]:
    if not subject_ids:
        return {}
    return {
        subject.id: subject
        for subject in db.scalars(
            select(AccountSubject).where(AccountSubject.id.in_(subject_ids))
        )
    }


def list_vouchers(
    db: Session,
    *,
    status: str | None = None,
    keyword: str | None = None,
    business_date_from: datetime | None = None,
    business_date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[int, list[Voucher]]:
    conditions = scope_conditions(db, Voucher.created_by)
    if status:
        conditions.append(Voucher.status == status)
    if business_date_from is not None:
        conditions.append(Voucher.business_date >= business_date_from)
    if business_date_to is not None:
        conditions.append(Voucher.business_date <= business_date_to)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Voucher.voucher_no.like(like),
                Voucher.reference.like(like),
                Voucher.id.in_(
                    select(VoucherItem.voucher_id).where(
                        VoucherItem.summary.like(like)
                    )
                ),
            )
        )

    total = db.scalar(select(func.count(Voucher.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Voucher)
            .where(*conditions)
            .order_by(Voucher.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return total, rows
