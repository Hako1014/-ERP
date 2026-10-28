"""通知 Outbox 与实时信号的周期补偿任务。"""

from app.integrations.platform_tasks import celery_app
from app.services.notification_service import (
    dispatch_realtime_batch,
    process_outbox_batch,
)
from app.services.notification_reminder_service import scan_reminders


@celery_app.task(name="erp.notification.scan_reminders")
def scan_notification_reminders_task() -> list[dict]:
    return scan_reminders()


@celery_app.task(name="erp.notification.process_outbox")
def process_notification_outbox_task() -> dict[str, int]:
    return process_outbox_batch(limit=100)


@celery_app.task(name="erp.notification.dispatch_realtime")
def dispatch_notification_realtime_task() -> dict[str, int]:
    return dispatch_realtime_batch(limit=100)
