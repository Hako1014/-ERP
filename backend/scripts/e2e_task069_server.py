"""Serve the real API against local MySQL with all E2E writes rolled back.

Start only for an explicitly approved test run. A single connection keeps the
browser's multi-request workflow in one outer transaction; stopping the server
rolls it back, including any fixture records created by the test.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from time import time

import uvicorn
from sqlalchemy.orm import Session

from app.db import redis as redis_module
from app.db.session import engine, get_db
from app.main import app


class TransactionalTestRedis:
    """Process-local Redis subset so E2E never mutates a shared Redis instance."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: object, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None)
        return removed

    def incr(self, key: str, amount: int = 1) -> int:
        # Keep generated numbers away from already-persisted daily sequences.
        value = int(self.values.get(key, str(int(time() * 1000)))) + amount
        self.values[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        return key in self.values


def main() -> None:
    if engine.dialect.name != "mysql":
        raise RuntimeError("TASK-069 E2E requires local MySQL")

    connection = engine.connect()
    transaction = connection.begin()
    lock = Lock()
    original_override = app.dependency_overrides.get(get_db)
    original_cache = redis_module._cache
    redis_module._cache = TransactionalTestRedis()

    @contextmanager
    def locked_session():
        with lock:
            db = Session(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield db
            finally:
                db.close()

    def override_get_db():
        with locked_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    finally:
        if original_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = original_override
        transaction.rollback()
        connection.close()
        redis_module._cache = original_cache
        print("TASK-069 MySQL transaction rolled back", flush=True)


if __name__ == "__main__":
    main()
