"""独立 MCP 命名空间，Redis 原子滑动窗口与并发租约。"""

import secrets

from redis import Redis

_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('EXPIRE', KEYS[1], 120)
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[4]), ARGV[1])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]) + 60)
return 1
"""
_RELEASE = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class QueryLimitExceeded(Exception):
    pass


class McpLimits:
    def __init__(self, url: str, *, rpm: int = 60, concurrent: int = 2, lease_seconds: int = 60):
        self.redis = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        self.rpm = rpm
        self.concurrent = concurrent
        self.lease_seconds = lease_seconds

    def acquire(self, company_id: int, staff_id: int) -> tuple[str, str]:
        prefix = f"erp:mcp:{{{company_id}:{staff_id}}}"
        key, nonce = f"{prefix}:active", secrets.token_hex(16)
        result = self.redis.eval(_ACQUIRE, 2, f"{prefix}:minute", key,
                                 nonce, self.rpm, self.concurrent, self.lease_seconds)
        if int(result) != 1:
            raise QueryLimitExceeded
        return key, nonce

    def release(self, lease: tuple[str, str]) -> None:
        self.redis.eval(_RELEASE, 1, *lease)
