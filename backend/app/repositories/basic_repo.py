from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.basic import Brand, Category, SpecValue, Unit, UnitGroup


def list_categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category)))


def get_category(db: Session, category_id: int) -> Category | None:
    return db.get(Category, category_id)


def list_brands(db: Session) -> list[Brand]:
    return list(db.scalars(select(Brand)))


def list_units(db: Session) -> list[Unit]:
    return list(db.scalars(select(Unit)))


def list_unit_groups(db: Session) -> list[UnitGroup]:
    return list(db.scalars(select(UnitGroup)))


def list_spec_values(db: Session, spec_type_id: int) -> list[SpecValue]:
    return list(db.scalars(select(SpecValue).where(SpecValue.spec_type_id == spec_type_id)))
