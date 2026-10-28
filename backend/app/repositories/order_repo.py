from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order, OrderItem


def get_order_by_no(db: Session, no: str) -> Order | None:
    return db.scalar(select(Order).where(Order.order_no == no))


def list_order_items(db: Session, order_id: int) -> list[OrderItem]:
    return list(db.scalars(select(OrderItem).where(OrderItem.order_id == order_id)))
