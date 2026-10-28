from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.org import Company, Department, Position, Staff


def get_staff_by_id(db: Session, staff_id: int) -> Staff | None:
    return db.get(Staff, staff_id)


def get_staff_by_login_account(db: Session, login_account: str) -> Staff | None:
    return db.scalar(select(Staff).where(Staff.login_account == login_account))


def get_staff_by_account(db: Session, account: str) -> Staff | None:
    """按登录账号查询，兼容历史手机号登录。"""
    return db.scalar(
        select(Staff).where(
            or_(
                Staff.login_account == account,
                Staff.phone == account,
            )
        )
    )


def get_staff_by_phone(db: Session, phone: str) -> Staff | None:
    return db.scalar(select(Staff).where(Staff.phone == phone))


def list_departments(db: Session, company_id: int) -> list[Department]:
    return list(db.scalars(select(Department).where(Department.company_id == company_id)))


def list_positions(db: Session, company_id: int) -> list[Position]:
    return list(db.scalars(select(Position).where(Position.company_id == company_id)))


def get_company(db: Session, company_id: int) -> Company | None:
    return db.get(Company, company_id)
