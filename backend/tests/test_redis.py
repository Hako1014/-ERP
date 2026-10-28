from app.db import redis as redis_mod
from app.db.redis import RedisCache


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def ping(self):
        return True

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


def _make_cache() -> RedisCache:
    cache = RedisCache.__new__(RedisCache)
    cache._client = _FakeRedis()
    return cache


def test_redis_set_get_delete():
    cache = _make_cache()
    assert cache.set("k", "v")
    assert cache.get("k") == "v"
    assert cache.ping() is True
    assert cache.delete("k") == 1
    assert cache.get("k") is None


def test_redis_lazy_singleton(monkeypatch):
    monkeypatch.setattr(redis_mod, "_cache", None)
    first = redis_mod.get_redis()
    second = redis_mod.get_redis()
    assert first is second
