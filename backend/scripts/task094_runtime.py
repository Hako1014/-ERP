"""真实 MySQL/Redis 验收入口：仅处理指定 TASK094 公司，独立队列。"""

import json
import os

from celery import Celery

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.org import Company, Staff

company_id = int(os.environ["TASK094_COMPANY_ID"])
actor_id = int(os.environ["TASK094_ACTOR_ID"])
marker = os.environ["TASK094_MARKER"]
with SessionLocal() as db:
    company = db.get(Company, company_id)
    actor = db.get(Staff, actor_id)
    if not marker.startswith("TASK094-") or not company or company.short_name != marker or not actor or actor.company_id != company_id:
        raise RuntimeError("TASK094 身份/公司护栏校验失败")

# 秒数仅为本次测试夹具；所有生产默认保持不变。
os.environ["NOTIFICATION_REMINDER_RULES"] = json.dumps([{
    "key": "task094-timeout", "kind": "approval_timeout", "company_id": company_id,
    "actor_staff_id": actor_id, "enabled": True, "sla_seconds": 2, "repeat_seconds": 3600,
    "active_from": os.environ["TASK094_ACTIVE_FROM"], "timezone": "Asia/Shanghai", "batch_limit": 20,
}])
get_settings.cache_clear()

from app.main import app  # noqa: E402,F401
from app.services.notification_service import process_outbox_batch, dispatch_realtime_batch  # noqa: E402
from app.services.notification_reminder_service import scan_reminders  # noqa: E402

settings = get_settings()
celery_app = Celery("task094", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_default_queue=marker,
    task_routes={"task094.*": {"queue": marker}},
    beat_schedule={
        "task094-outbox": {"task": "task094.outbox", "schedule": 2.0},
        "task094-realtime": {"task": "task094.realtime", "schedule": 2.0},
        "task094-reminders": {"task": "task094.reminders", "schedule": 3.0},
    },
)


@celery_app.task(name="task094.outbox")
def outbox():
    return process_outbox_batch(company_id=company_id, limit=20)


@celery_app.task(name="task094.realtime")
def realtime():
    return dispatch_realtime_batch(company_id=company_id, limit=20)


@celery_app.task(name="task094.reminders")
def reminders():
    return scan_reminders()
