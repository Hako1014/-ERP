from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.data_scope import clear_scope_policy

engine: Engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    hide_parameters=True,  # 数据库异常不输出私人会话正文、搜索词或连接参数。
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

current_session_var: ContextVar[Session | None] = ContextVar(
    "current_session", default=None
)


def get_current_session() -> Session | None:
    """返回当前事务上下文中的 Session（供横切能力复用）。"""
    return current_session_var.get()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个 Session，请求结束关闭。"""
    db = SessionLocal()
    previous_session = current_session_var.get()
    current_session_var.set(db)
    try:
        yield db
    finally:
        try:
            clear_scope_policy(db)
            db.close()
        finally:
            current_session_var.set(previous_session)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """事务边界：正常提交，异常回滚后重新抛出。"""
    db = SessionLocal()
    token = current_session_var.set(db)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        clear_scope_policy(db)
        current_session_var.reset(token)
        db.close()
