from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.basic import Brand, Category, Unit
from app.models.product import Product, ProductSku
from app.repositories.product_repo import get_product_by_code, get_sku_by_spec_code


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "category",
        "brand",
        "unit",
        "unit_group",
        "unit_group_item",
        "spec_type",
        "spec_value",
        "product",
        "product_sku",
    ]:
        assert name in tables


def test_unique_indexes():
    insp = inspect(_engine())
    product_unique = {
        col for uniq in insp.get_unique_constraints("product") for col in uniq["column_names"]
    }
    sku_unique = {
        col for uniq in insp.get_unique_constraints("product_sku") for col in uniq["column_names"]
    }
    assert "code" in product_unique
    assert "spec_code" in sku_unique
    assert "barcode" in sku_unique


def test_product_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        brand = Brand(name="示例品牌")
        category = Category(name="示例分类")
        unit = Unit(name="个")
        db.add_all([brand, category, unit])
        db.flush()
        product = Product(name="示例商品", code="SP001", brand_id=brand.id, category_id=category.id, main_unit_id=unit.id, online_price=Decimal("9.90"))
        db.add(product)
        db.flush()
        db.add(ProductSku(product_id=product.id, spec_name="默认", spec_code="SP001-01", barcode="690000000001", sale_price=Decimal("9.90")))
        db.commit()
    with Session() as db:
        assert get_product_by_code(db, "SP001").name == "示例商品"
        assert get_sku_by_spec_code(db, "SP001-01").barcode == "690000000001"


def test_product_duplicate_code_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Product(name="A", code="DUP"))
        db.add(Product(name="B", code="DUP"))
        with pytest.raises(IntegrityError):
            db.commit()
