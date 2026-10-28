from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.warehouse import Location, StockLocation, Warehouse, Zone


def get_warehouse_by_code(db: Session, code: str) -> Warehouse | None:
    return db.scalar(select(Warehouse).where(Warehouse.code == code))


def get_location_by_code(db: Session, code: str) -> Location | None:
    return db.scalar(select(Location).where(Location.code == code))


def list_warehouses(db: Session) -> list[Warehouse]:
    return list(db.scalars(select(Warehouse)))


def list_zones(db: Session, warehouse_id: int) -> list[Zone]:
    return list(db.scalars(select(Zone).where(Zone.warehouse_id == warehouse_id)))


def list_locations(db: Session, warehouse_id: int) -> list[Location]:
    return list(db.scalars(select(Location).where(Location.warehouse_id == warehouse_id)))


def list_stock_locations(db: Session, location_id: int) -> list[StockLocation]:
    return list(db.scalars(select(StockLocation).where(StockLocation.location_id == location_id)))
