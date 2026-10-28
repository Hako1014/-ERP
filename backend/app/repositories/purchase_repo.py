from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.purchase import (
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanItem,
    PurchasePrepay,
    PurchaseReturn,
    PurchaseReturnItem,
)


def get_plan_by_no(db: Session, no: str) -> PurchasePlan | None:
    return db.scalar(select(PurchasePlan).where(PurchasePlan.plan_no == no))


def get_order_by_no(db: Session, no: str) -> PurchaseOrder | None:
    return db.scalar(select(PurchaseOrder).where(PurchaseOrder.order_no == no))


def get_prepay_by_no(db: Session, no: str) -> PurchasePrepay | None:
    return db.scalar(select(PurchasePrepay).where(PurchasePrepay.prepay_no == no))


def get_return_by_no(db: Session, no: str) -> PurchaseReturn | None:
    return db.scalar(select(PurchaseReturn).where(PurchaseReturn.return_no == no))


def get_invoice_by_no(db: Session, no: str) -> PurchaseInvoice | None:
    return db.scalar(select(PurchaseInvoice).where(PurchaseInvoice.invoice_no == no))


def list_plan_items(db: Session, plan_id: int) -> list[PurchasePlanItem]:
    return list(db.scalars(select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan_id)))


def list_order_items(db: Session, order_id: int) -> list[PurchaseOrderItem]:
    return list(db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.order_id == order_id)))


def list_return_items(db: Session, return_id: int) -> list[PurchaseReturnItem]:
    return list(db.scalars(select(PurchaseReturnItem).where(PurchaseReturnItem.return_id == return_id)))
