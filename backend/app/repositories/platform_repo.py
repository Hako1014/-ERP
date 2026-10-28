from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.platform import Logistics, Platform, Shop


def list_platforms(db: Session) -> list[Platform]:
    return list(db.scalars(select(Platform)))


def list_shops(db: Session, platform_id: int) -> list[Shop]:
    return list(db.scalars(select(Shop).where(Shop.platform_id == platform_id)))


def get_logistics_by_id(db: Session, logistics_id: int) -> Logistics | None:
    return db.get(Logistics, logistics_id)
