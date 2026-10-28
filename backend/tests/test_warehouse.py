import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.warehouse import Location, Warehouse, Zone
from app.repositories.warehouse_repo import get_location_by_code, get_warehouse_by_code


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in ["warehouse", "zone", "location", "stock_location"]:
        assert name in tables


def test_location_code_unique():
    cols = {
        col
        for uniq in inspect(_engine()).get_unique_constraints("location")
        for col in uniq["column_names"]
    }
    assert "code" in cols


def test_warehouse_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        wh = Warehouse(code="WH001", name="华东仓", enable_location=True)
        db.add(wh)
        db.flush()
        db.add(Zone(warehouse_id=wh.id, code="MD02", name="MD02区"))
        db.add(Location(warehouse_id=wh.id, code="WH001-MD02区-01行-01列-06层", row_no=1, col_no=1, layer_no=6))
        db.commit()
    with Session() as db:
        assert get_warehouse_by_code(db, "WH001").name == "华东仓"
        assert get_location_by_code(db, "WH001-MD02区-01行-01列-06层").layer_no == 6


def test_warehouse_duplicate_code_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Warehouse(code="DUP", name="A"))
        db.add(Warehouse(code="DUP", name="B"))
        with pytest.raises(IntegrityError):
            db.commit()
