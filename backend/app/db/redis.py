from typing import Any

import redis

from app.core.config import get_settings


class RedisCache:
    """Redis 客户端薄封装，便于测试与替换。"""

    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        return bool(self._client.set(key, value, ex=ex, nx=nx))

    def delete(self, *keys: str) -> int:
        return self._client.delete(*keys)

    def incr(self, key: str, amount: int = 1) -> int:
        return int(self._client.incr(key, amount))

    def expire(self, key: str, seconds: int) -> bool:
        return bool(self._client.expire(key, seconds))

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """执行 Redis Lua 原子脚本，供跨进程配额与并发治理使用。"""
        return self._client.eval(script, numkeys, *keys_and_args)

    def publish(self, channel: str, message: str) -> int:
        """发布轻量实时信号；业务消息正文仍以数据库 Inbox 为准。"""
        return int(self._client.publish(channel, message))

    def pubsub(self):
        """创建请求级订阅；调用方负责在 SSE 断开时关闭。"""
        return self._client.pubsub(ignore_subscribe_messages=True)


_cache: RedisCache | None = None


def get_redis() -> RedisCache:
    """惰性单例：首次调用时创建 Redis 客户端。"""
    global _cache
    if _cache is None:
        _cache = RedisCache(get_settings().redis_url)
    return _cache
