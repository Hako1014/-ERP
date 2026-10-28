"""基于 Redis Lua 的跨进程请求去重、并发与速率限制。"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.errors import BizException, ErrorCode
from app.db.redis import RedisCache

logger = logging.getLogger(__name__)

_ACQUIRE_SCRIPT = """
local duplicate = redis.call('EXISTS', KEYS[4])
if duplicate == 1 then return -3 end
local now = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if tonumber(redis.call('ZCARD', KEYS[1])) >= tonumber(ARGV[5]) then return -1 end
local minute_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if minute_count >= tonumber(ARGV[6]) then return -2 end
local daily_limit = tonumber(ARGV[7])
if daily_limit > 0 then
  local day_count = tonumber(redis.call('GET', KEYS[3]) or '0')
  if day_count >= daily_limit then return -4 end
end
redis.call('INCR', KEYS[2])
if minute_count == 0 then redis.call('EXPIRE', KEYS[2], 60) end
if daily_limit > 0 then
  local day_count = tonumber(redis.call('INCR', KEYS[3]))
  if day_count == 1 then redis.call('EXPIRE', KEYS[3], tonumber(ARGV[8])) end
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[3]), ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
redis.call('SET', KEYS[4], ARGV[1], 'EX', 600, 'NX')
return 1
"""

_RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"


@dataclass(frozen=True)
class AssistantLease:
    active_key: str
    token: str


def acquire_request(
    redis: RedisCache,
    *,
    company_id: int,
    staff_id: int,
    request_id: str,
    requests_per_minute: int,
    concurrent_limit: int,
    daily_limit: int | None,
    lease_seconds: int,
) -> AssistantLease:
    now = time.time()
    utc_now = datetime.now(timezone.utc)
    utc_tomorrow = datetime.combine(
        utc_now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    seconds_until_utc_midnight = max(1, int(utc_tomorrow.timestamp() - now))
    prefix = f"erp:assistant:{{{company_id}:{staff_id}}}"
    token = str(uuid4())
    active_key = f"{prefix}:active"
    result = redis.eval(
        _ACQUIRE_SCRIPT,
        4,
        active_key,
        f"{prefix}:minute:{int(now // 60)}",
        f"{prefix}:day:{utc_now:%Y%m%d}",
        f"{prefix}:request:{request_id}",
        token,
        now,
        now + lease_seconds,
        lease_seconds + 60,
        concurrent_limit,
        requests_per_minute,
        daily_limit or 0,
        seconds_until_utc_midnight,
    )
    status = int(result)
    if status == -3:
        raise BizException(ErrorCode.CONFLICT, "此请求已提交，请勿重复发送")
    if status in (-1, -2, -4):
        raise BizException(
            ErrorCode.AI_RATE_LIMITED,
            "AI 助手已达到当前并发或调用额度，请稍后再试或联系管理员调整额度",
        )
    if status != 1:
        raise BizException(ErrorCode.AI_UNCONFIGURED, "AI 配额服务暂不可用，请稍后重试")
    return AssistantLease(active_key=active_key, token=token)


def release_request(redis: RedisCache, lease: AssistantLease | None) -> None:
    if lease is None:
        return
    try:
        redis.eval(_RELEASE_SCRIPT, 1, lease.active_key, lease.token)
    except (RedisError, OSError, TimeoutError):
        # 租约有 TTL；释放故障不记录消息、密钥或业务正文，避免掩盖原结果。
        logger.warning("AI concurrency lease release failed; TTL cleanup will recover")

