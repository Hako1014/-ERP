from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.prefixes import SerialPrefix
from app.db.redis import RedisCache, get_redis

DEFAULT_SEQ_PAD = 4
_KEY_TTL_SECONDS = 2 * 24 * 3600


def _prefix_value(prefix: SerialPrefix | str) -> str:
    return prefix.value if isinstance(prefix, SerialPrefix) else str(prefix)


def generate_serial(
    prefix: SerialPrefix | str,
    *,
    dt: datetime | None = None,
    seq_pad: int = DEFAULT_SEQ_PAD,
    redis: RedisCache | None = None,
) -> str:
    """生成全局唯一单据编号：{前缀}{yyyyMMdd}-{当日序号}。

    Redis INCR 提供原子自增，保证并发下不重号、不跳号；序号按天重置。
    """
    client = redis or get_redis()
    now = dt or datetime.now(ZoneInfo(get_settings().timezone))
    prefix_str = _prefix_value(prefix)
    key = f"serial:{prefix_str}:{now.strftime('%Y%m%d')}"

    seq = client.incr(key)
    if seq == 1:
        client.expire(key, _KEY_TTL_SECONDS)

    return f"{prefix_str}{now.strftime('%Y%m%d')}-{str(seq).zfill(seq_pad)}"
