import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.data_scope import DATA_SCOPE_POLICY_KEY, ScopePolicy
from app.db.base import Base, TimestampMixin
from app.db import session as session_mod
from app.db.session import get_db, session_scope


class Widget(Base, TimestampMixin):
    __tablename__ = "widget"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))


def _use_sqlite(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    return factory


def test_session_scope_commit(monkeypatch):
    factory = _use_sqlite(monkeypatch)

    with session_scope() as db:
        db.add(Widget(name="a"))

    with factory() as db:
        assert db.query(Widget).count() == 1


def test_session_scope_rollback(monkeypatch):
    factory = _use_sqlite(monkeypatch)
    scoped_session = None

    with pytest.raises(RuntimeError):
        with session_scope() as db:
            scoped_session = db
            db.info[DATA_SCOPE_POLICY_KEY] = ScopePolicy(
                staff_id=1,
                company_id=1,
                department_id=1,
                scope_type="SELF",
            )
            db.add(Widget(name="b"))
            raise RuntimeError("boom")

    assert scoped_session is not None
    assert DATA_SCOPE_POLICY_KEY not in scoped_session.info

    with factory() as db:
        assert db.query(Widget).count() == 0


def test_get_db_cleans_context_across_fastapi_threadpool(monkeypatch):
    factory = _use_sqlite(monkeypatch)
    with factory() as db:
        db.add(Widget(name="api"))
        db.commit()

    app = FastAPI()

    @app.get("/widget-count")
    def widget_count(db: Session = Depends(get_db)):
        return {"count": db.query(Widget).count()}

    with TestClient(app) as client:
        response = client.get("/widget-count")

    assert response.status_code == 200
    assert response.json() == {"count": 1}


def test_get_db_clears_scope_policy_on_close(monkeypatch):
    _use_sqlite(monkeypatch)
    db_generator = get_db()
    db = next(db_generator)
    db.info[DATA_SCOPE_POLICY_KEY] = ScopePolicy(
        staff_id=1,
        company_id=1,
        department_id=None,
        scope_type="SELF",
    )

    db_generator.close()

    assert DATA_SCOPE_POLICY_KEY not in db.info
