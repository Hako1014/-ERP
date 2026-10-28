from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.order import Order, OrderItem
from app.models.platform import Platform, Shop
from app.repositories.order_repo import get_order_by_no


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "order",
        "order_item",
        "return_order",
        "return_order_item",
        "exchange_order",
        "exchange_order_item",
        "return_register",
        "wave_strategy",
        "wave",
        "load_no",
        "print_template",
        "waybill",
        "pick_list",
        "delivery_list",
        "platform",
        "shop",
        "shop_api",
        "logistics",
        "freight_template",
        "area_rule",
    ]:
        assert name in tables


def test_order_no_unique():
    cols = {
        c
        for u in inspect(_engine()).get_unique_constraints("order")
        for c in u["column_names"]
    }
    assert "order_no" in cols


def test_order_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        platform = Platform(name="淘宝")
        db.add(platform)
        db.flush()
        shop = Shop(platform_id=platform.id, name="示例店铺")
        db.add(shop)
        db.flush()
        order = Order(order_no="DD20260923-0001", shop_id=shop.id, paid_amount=Decimal("9.90"))
        db.add(order)
        db.flush()
        db.add(OrderItem(order_id=order.id, product_id=1, qty=Decimal("1"), price=Decimal("9.90")))
        db.commit()
    with Session() as db:
        assert get_order_by_no(db, "DD20260923-0001").shop_id == 1


def test_order_duplicate_no_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Platform(name="淘宝"))
        db.add(Shop(platform_id=1, name="店铺"))
        db.add(Order(order_no="DUP", shop_id=1))
        db.add(Order(order_no="DUP", shop_id=1))
        with pytest.raises(IntegrityError):
            db.commit()
