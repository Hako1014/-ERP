from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.stock import StockFlow, StockIn
from app.repositories.stock_repo import get_stock_in_by_no, list_stock_flows


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "stock_in",
        "stock_in_item",
        "stock_out",
        "stock_out_item",
        "transfer",
        "transfer_item",
        "transfer_out",
        "transfer_out_item",
        "transfer_in",
        "transfer_in_item",
        "stock_check",
        "stock_check_item",
        "location_adjust",
        "location_adjust_item",
        "putaway",
        "stock_flow",
        "stock_warning",
        "stock_cost",
        "stock_settlement",
    ]:
        assert name in tables


def test_stock_flow_columns():
    cols = {c["name"] for c in inspect(_engine()).get_columns("stock_flow")}
    for name in ["warehouse_id", "product_id", "sku_id", "location_id", "direction", "biz_type", "qty", "source_no", "order_no", "flow_time"]:
        assert name in cols


def test_stock_doc_no_unique():
    insp = inspect(_engine())
    for table in ["stock_in", "stock_out", "transfer", "stock_check", "location_adjust"]:
        assert insp.get_unique_constraints(table)


def test_stock_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(StockIn(stock_in_no="RKD20260923-0001", warehouse_id=1, stock_in_type="PURCHASE", total_qty=Decimal("10")))
        db.flush()
        db.add(StockFlow(warehouse_id=1, product_id=1, direction="IN", biz_type="PURCHASE", qty=Decimal("10")))
        db.commit()
    with Session() as db:
        assert get_stock_in_by_no(db, "RKD20260923-0001").warehouse_id == 1
        assert len(list_stock_flows(db, 1)) == 1
