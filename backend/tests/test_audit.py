import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.audit import audit, write_audit
from app.db import session as session_mod
from app.db.base import Base
from app.db.session import session_scope
from app.models.audit_log import AuditLog
from app.repositories.audit_repo import list_by_target


def _use_sqlite(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    return factory


def test_write_audit_persists_on_commit(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    with session_scope() as db:
        write_audit(
            db,
            action="CREATE",
            target_type="purchase_order",
            target_id="PO1",
            summary="创建采购订单",
            detail={"qty": 3},
        )

    with factory() as db:
        rows = db.query(AuditLog).all()
        assert len(rows) == 1
        assert rows[0].action == "CREATE"
        assert rows[0].target_id == "PO1"


def test_write_audit_rolls_back_with_business(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    with pytest.raises(RuntimeError):
        with session_scope() as db:
            write_audit(db, action="CREATE", target_type="purchase_order", target_id="PO2")
            raise RuntimeError("boom")

    with factory() as db:
        assert db.query(AuditLog).count() == 0


def test_audit_decorator_persists(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    @audit("CREATE", "widget", target_id_getter=lambda r: str(r["id"]))
    def create_widget(name: str) -> dict:
        return {"id": 1, "name": name}

    with session_scope():
        create_widget("a")

    with factory() as db:
        rows = db.query(AuditLog).all()
        assert len(rows) == 1
        assert rows[0].target_id == "1"
        assert rows[0].action == "CREATE"


def test_audit_decorator_rolls_back(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    @audit("CREATE", "widget")
    def create_widget(name: str) -> dict:
        return {"id": 1}

    with pytest.raises(RuntimeError):
        with session_scope():
            create_widget("a")
            raise RuntimeError("boom")

    with factory() as db:
        assert db.query(AuditLog).count() == 0


def test_list_by_target_timeline(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    with session_scope() as db:
        write_audit(db, action="CREATE", target_type="order", target_id="O1")
        write_audit(db, action="APPROVE", target_type="order", target_id="O1")
        write_audit(db, action="CREATE", target_type="order", target_id="O2")

    with factory() as db:
        rows = list_by_target(db, "order", "O1")
        assert [r.action for r in rows] == ["APPROVE", "CREATE"]
