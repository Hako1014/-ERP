"""Serve TASK-088 browser E2E only against its dedicated MySQL schema."""

from __future__ import annotations

import argparse
import os
from threading import RLock
from typing import Any

from sqlalchemy.engine import URL, make_url

from app.core.config import get_settings

DATABASE_NAME = "erp_task088_test"


class Task088TestRedis:
    """Process-local Redis subset so this E2E never mutates shared Redis."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.lock = RLock()

    def get(self, key: str) -> str | None:
        with self.lock:
            return self.values.get(key)

    def set(
        self,
        key: str,
        value: object,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        with self.lock:
            if nx and key in self.values:
                return False
            self.values[key] = str(value)
            return True

    def delete(self, *keys: str) -> int:
        with self.lock:
            removed = 0
            for key in keys:
                removed += int(self.values.pop(key, None) is not None)
                removed += int(self.sorted_sets.pop(key, None) is not None)
            return removed

    def incr(self, key: str, amount: int = 1) -> int:
        with self.lock:
            value = int(self.values.get(key, "0")) + amount
            self.values[key] = str(value)
            return value

    def expire(self, key: str, seconds: int) -> bool:
        return key in self.values or key in self.sorted_sets

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        with self.lock:
            if "ZREMRANGEBYSCORE" in script and "ZADD" in script:
                active_key, minute_key, day_key, request_key = keys
                token, now, deadline = str(args[0]), float(args[1]), float(args[2])
                concurrent_limit = int(args[4])
                requests_per_minute = int(args[5])
                daily_limit = int(args[6])
                if request_key in self.values:
                    return -3
                active = self.sorted_sets.setdefault(active_key, {})
                self.sorted_sets[active_key] = {
                    member: score for member, score in active.items() if score > now
                }
                active = self.sorted_sets[active_key]
                if len(active) >= concurrent_limit:
                    return -1
                minute_count = int(self.values.get(minute_key, "0"))
                if minute_count >= requests_per_minute:
                    return -2
                day_count = int(self.values.get(day_key, "0"))
                if daily_limit > 0 and day_count >= daily_limit:
                    return -4
                self.values[minute_key] = str(minute_count + 1)
                if daily_limit > 0:
                    self.values[day_key] = str(day_count + 1)
                active[token] = deadline
                self.values[request_key] = token
                return 1
            if "ZREM" in script and numkeys == 1:
                members = self.sorted_sets.get(keys[0], {})
                return int(members.pop(str(args[0]), None) is not None)
            if "EXISTS" in script and "SET" in script:
                key, updated = str(keys[0]), str(args[0])
                if key in self.values:
                    return 0
                self.values[key] = updated
                return 1
            if "GET" in script and "SET" in script:
                key, previous, updated = str(keys[0]), str(args[0]), str(args[1])
                if self.values.get(key) != previous:
                    return 0
                self.values[key] = updated
                return 1
            if "GET" in script and "DEL" in script:
                key, previous = str(keys[0]), str(args[0])
                if self.values.get(key) != previous:
                    return 0
                self.values.pop(key, None)
                return 1
        raise RuntimeError("TASK-088 E2E 遇到未支持的 Redis 操作")


def _target_url(source_url: URL) -> URL:
    if not source_url.drivername.startswith("mysql"):
        raise RuntimeError("TASK-088 E2E 只允许 MySQL")
    if source_url.database not in {"erp", DATABASE_NAME}:
        raise RuntimeError("数据库连接配置不是 erp 或 TASK-088 隔离库；拒绝启动")
    return source_url.set(database=DATABASE_NAME)


def _seed_purchase_plan_serial(redis: Task088TestRedis, engine: Any) -> None:
    """Resume the isolated in-memory sequence after a server restart."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.core.prefixes import SerialPrefix
    from app.models.purchase import PurchasePlan

    prefix = SerialPrefix.PURCHASE_PLAN.value
    business_date = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d")
    plan_prefix = f"{prefix}{business_date}-"
    with Session(engine) as db:
        plan_numbers = list(
            db.scalars(
                select(PurchasePlan.plan_no).where(
                    PurchasePlan.plan_no.like(f"{plan_prefix}%")
                )
            )
        )

    existing_sequences = [
        int(plan_no.rsplit("-", 1)[-1])
        for plan_no in plan_numbers
        if plan_no.rsplit("-", 1)[-1].isdigit()
    ]
    if existing_sequences:
        redis.set(
            f"serial:{prefix}:{business_date}",
            max(existing_sequences),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    target_url = _target_url(make_url(get_settings().database_url))
    # Set before importing the application engine; the app cannot inherit erp.
    os.environ["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
    get_settings.cache_clear()

    import uvicorn

    from app import main as app_module
    from app.core import serial
    from app.db.session import engine

    # The application imports get_redis directly in API modules. Override the
    # assistant dependency and serial helper so no external Redis is contacted.
    from app.db.redis import get_redis

    redis = Task088TestRedis()
    original_serial_redis = serial.get_redis
    original_api_override = app_module.app.dependency_overrides.get(get_redis)
    serial.get_redis = lambda: redis
    app_module.app.dependency_overrides[get_redis] = lambda: redis

    with engine.connect() as connection:
        actual_database = connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
    if actual_database != DATABASE_NAME or engine.dialect.name != "mysql":
        raise RuntimeError("后端数据库身份校验失败；服务未启动")
    _seed_purchase_plan_serial(redis, engine)

    try:
        print(
            f"TASK-088 后端仅绑定 127.0.0.1:{args.port}，"
            f"数据库={DATABASE_NAME}，Redis=进程内隔离模拟。",
            flush=True,
        )
        uvicorn.run(app_module.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if original_api_override is None:
            app_module.app.dependency_overrides.pop(get_redis, None)
        else:
            app_module.app.dependency_overrides[get_redis] = original_api_override
        serial.get_redis = original_serial_redis
        engine.dispose()
        print("TASK-088 本地后端已停止。", flush=True)


if __name__ == "__main__":
    main()
