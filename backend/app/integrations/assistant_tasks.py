"""助手独占正文清理；导入不会启动 Celery 进程。"""

from app.db.session import SessionLocal
from app.integrations.platform_tasks import celery_app
from app.services.assistant_conversations import cleanup_expired
from app.services.assistant_attachments import cleanup_expired as cleanup_attachments


@celery_app.task(name="erp.assistant.cleanup_conversations")
def cleanup_conversations_task() -> dict:
    with SessionLocal() as db:
        result = cleanup_expired(db, limit=1000)
        result.update(cleanup_attachments(db, limit=1000))
        return result
