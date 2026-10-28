from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product, ProductSku


def get_product_by_code(db: Session, code: str) -> Product | None:
    return db.scalar(select(Product).where(Product.code == code))


def get_sku_by_spec_code(db: Session, spec_code: str) -> ProductSku | None:
    return db.scalar(select(ProductSku).where(ProductSku.spec_code == spec_code))


def list_skus(db: Session, product_id: int) -> list[ProductSku]:
    return list(db.scalars(select(ProductSku).where(ProductSku.product_id == product_id)))
