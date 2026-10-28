from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stock import (
    LocationAdjust,
    StockCheck,
    StockFlow,
    StockIn,
    StockOut,
    Transfer,
)


def get_stock_in_by_no(db: Session, no: str) -> StockIn | None:
    return db.scalar(select(StockIn).where(StockIn.stock_in_no == no))


def get_stock_out_by_no(db: Session, no: str) -> StockOut | None:
    return db.scalar(select(StockOut).where(StockOut.stock_out_no == no))


def get_transfer_by_no(db: Session, no: str) -> Transfer | None:
    return db.scalar(select(Transfer).where(Transfer.transfer_no == no))


def get_check_by_no(db: Session, no: str) -> StockCheck | None:
    return db.scalar(select(StockCheck).where(StockCheck.check_no == no))


def get_adjust_by_no(db: Session, no: str) -> LocationAdjust | None:
    return db.scalar(select(LocationAdjust).where(LocationAdjust.adjust_no == no))


def list_stock_flows(db: Session, product_id: int) -> list[StockFlow]:
    return list(db.scalars(select(StockFlow).where(StockFlow.product_id == product_id)))
