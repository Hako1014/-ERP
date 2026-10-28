"""平台订单 Celery 任务。导入模块不会启动 worker/beat。"""

from celery import Celery
from sqlalchemy import select

from app.core.config import get_settings
from app.core.permissions import permissions_for_roles
from app.db.session import SessionLocal
from app.models.org import Staff
from app.models.platform import Shop
from app.repositories.rbac_repo import list_staff_role_codes
from app.services.platform_sync_service import pull_shop_orders

settings = get_settings()
celery_app = Celery("erp_platform", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.beat_schedule = {
    "pull-enabled-platform-shops": {
        "task": "erp.platform.pull_enabled_shops",
        "schedule": 300.0,
    }
}
celery_app.conf.update(imports=("app.integrations.notification_tasks", "app.integrations.assistant_tasks"))
celery_app.conf.beat_schedule.update(
    {
        "cleanup-assistant-conversations": {
            "task": "erp.assistant.cleanup_conversations",
            "schedule": 3600.0,
        },
        "scan-notification-reminders": {
            "task": "erp.notification.scan_reminders",
            "schedule": 60.0,
        },
        "process-notification-outbox": {
            "task": "erp.notification.process_outbox",
            "schedule": 10.0,
        },
        "dispatch-notification-realtime": {
            "task": "erp.notification.dispatch_realtime",
            "schedule": 10.0,
        },
    }
)


@celery_app.task(name="erp.platform.pull_shop_orders")
def pull_shop_orders_task(shop_id: int, staff_id: int) -> dict:
    with SessionLocal() as db:
        staff = db.get(Staff, staff_id)
        if staff is None or not staff.is_active:
            raise ValueError("抓单员工不存在或已停用")
        permissions = permissions_for_roles(list_staff_role_codes(db, staff.id))
        if not ({"order:audit", "order:ship", "*"} & permissions):
            raise ValueError("抓单员工已失去订单管理权限")
        return pull_shop_orders(db, shop_id=shop_id, staff=staff)


@celery_app.task(name="erp.platform.pull_enabled_shops")
def pull_enabled_shops_task() -> dict:
    staff_id = get_settings().platform_system_staff_id
    if not staff_id:
        return {"queued": 0, "reason": "未配置自动抓单员工"}
    with SessionLocal() as db:
        staff = db.get(Staff, staff_id)
        if staff is None or not staff.is_active:
            raise ValueError("自动抓单员工不存在或已停用")
        permissions = permissions_for_roles(list_staff_role_codes(db, staff.id))
        if not ({"order:audit", "order:ship", "*"} & permissions):
            raise ValueError("自动抓单员工没有订单管理权限")
        shop_ids = list(db.scalars(select(Shop.id).where(Shop.auto_sync_order.is_(True), Shop.is_locked.is_(False))))
    for shop_id in shop_ids:
        pull_shop_orders_task.delay(shop_id, staff_id)
    return {"queued": len(shop_ids)}
