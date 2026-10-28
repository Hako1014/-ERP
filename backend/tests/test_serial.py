import concurrent.futures
import random
import string
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial


class _FakeCounter:
    def __init__(self):
        self.store: dict[str, int] = {}

    def incr(self, key: str, amount: int = 1) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    def expire(self, key: str, seconds: int) -> bool:
        return True


def _dt(day: int) -> datetime:
    return datetime(2026, 9, day, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_serial_format():
    fake = _FakeCounter()
    no = generate_serial(SerialPrefix.PURCHASE_ORDER, dt=_dt(23), redis=fake)
    assert no == "CGDD20260923-0001"


def test_serial_increments():
    fake = _FakeCounter()
    a = generate_serial(SerialPrefix.STOCK_IN, dt=_dt(23), redis=fake)
    b = generate_serial(SerialPrefix.STOCK_IN, dt=_dt(23), redis=fake)
    assert a.endswith("-0001")
    assert b.endswith("-0002")


def test_serial_resets_daily():
    fake = _FakeCounter()
    a = generate_serial(SerialPrefix.ORDER, dt=_dt(23), redis=fake)
    b = generate_serial(SerialPrefix.ORDER, dt=_dt(24), redis=fake)
    assert a == "DD20260923-0001"
    assert b == "DD20260924-0001"


def test_serial_concurrent_unique():
    from app.db.redis import get_redis

    try:
        client = get_redis()
        assert client.ping()
    except Exception:
        pytest.skip("Redis 不可用，跳过并发取号测试")

    prefix = "TEST" + "".join(random.choices(string.ascii_uppercase, k=6))
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    key = f"serial:{prefix}:{now.strftime('%Y%m%d')}"
    client.delete(key)

    def worker(_: int) -> str:
        return generate_serial(prefix, dt=now, redis=client)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))

    try:
        assert len(set(results)) == 10
        seqs = {int(r.rsplit("-", 1)[-1]) for r in results}
        assert seqs == set(range(1, 11))
    finally:
        client.delete(key)
