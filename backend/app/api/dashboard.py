import json
import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.response import Result
from app.core.query_access import authorize_query
from app.db.redis import get_redis
from app.db.session import get_db
from app.models.org import Staff
from app.services import shared_queries

router = APIRouter(tags=["报表与看板"])

read_dashboard = require_permission("module:dashboard")
logger = logging.getLogger(__name__)
DASHBOARD_CACHE_TTL_SECONDS = 15


@router.get("/dashboard/overview", response_model=Result[dict])
def get_dashboard_overview(
    trend_period: Literal["day", "week", "month"] = "day",
    staff: Staff = Depends(read_dashboard),
    db: Session = Depends(get_db),
):
    # 必须先重新授权再查缓存；角色/部门/数据范围变化后旧键不可复用。
    access = authorize_query(db, "dashboard")
    business_day = datetime.now(ZoneInfo(get_settings().timezone)).date().isoformat()
    key = f"dashboard:overview:v2:{access.cache_fingerprint()}:{business_day}:{trend_period}"
    try:
        cached = get_redis().get(key)
        if cached is not None:
            return Result.ok(json.loads(cached))
    except (RedisError, OSError, ValueError):
        logger.warning("首页缓存读取失败，回退到数据库查询", exc_info=True)

    data = shared_queries.dashboard(db, trend_period=trend_period)
    try:
        get_redis().set(key, json.dumps(jsonable_encoder(data), ensure_ascii=False), ex=DASHBOARD_CACHE_TTL_SECONDS)
    except (RedisError, OSError):
        logger.warning("首页缓存写入失败", exc_info=True)
    return Result.ok(data)
