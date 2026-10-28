from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.reminder_config import ReminderRule

# 项目根目录（backend/app/core/config.py 向上三级）。
BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """应用配置，可通过环境变量或根目录 .env 覆盖。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "电商 ERP API"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    database_url: str = "mysql+pymysql://erp:erp@localhost:3307/erp"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = str(BASE_DIR / "uploads")
    max_upload_size_mb: int = 10

    timezone: str = "Asia/Shanghai"
    money_precision: int = 2

    jwt_secret: str = "dev-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    sms_code_ttl_seconds: int = 300
    sms_mock_show_code: bool = False
    # 前端 dev server 端口为 5273（5173 落在 Windows 动态端口排除区，宿主机无法 bind）。
    cors_allowed_origins: str = "http://localhost:5273,http://127.0.0.1:5273"
    platform_system_staff_id: int = 0
    # JSON 数组配置；默认不启用任何规则，也不访问提醒数据库。
    notification_reminder_rules: list[ReminderRule] = Field(default_factory=list)
    # MCP仅通过stdio提供只读工具，令牌由ERP_MCP_TOKEN单独注入。
    mcp_query_timeout_seconds: int = Field(default=10, ge=1, le=30)
    mcp_query_requests_per_minute: int = Field(default=60, ge=1, le=600)
    mcp_query_concurrent: int = Field(default=2, ge=1, le=4)
    mcp_query_max_result_bytes: int = Field(default=262144, ge=4096, le=1048576)

    # 角色 AI 助手。密钥只从部署环境注入，不写入仓库、不下发前端、不记入审计详情。
    # 用户指定 V4 Flash；使用 DeepSeek 当前规范 ID，并以真实调用确认路由。
    assistant_enabled: bool = True
    assistant_provider: str = Field(
        default="deepseek",
        validation_alias=AliasChoices("AI_PROVIDER", "ASSISTANT_PROVIDER", "assistant_provider"),
    )
    assistant_model: str = Field(
        default="deepseek-flash",
        validation_alias=AliasChoices(
            "DEEPSEEK_MODEL", "ASSISTANT_MODEL", "assistant_model"
        ),
    )
    assistant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEEPSEEK_APIKEY",
            "DEEPSEEK_API_KEY",
            "ASSISTANT_API_KEY",
            "assistant_api_key",
        ),
    )
    # Fernet key must be supplied from the deployment secret store before an
    # administrator can persist an API key in the database.
    assistant_config_encryption_key: str = ""
    assistant_base_url: str = "https://api.deepseek.com"
    assistant_max_output_tokens: int = Field(default=2048, ge=1, le=16_384)
    assistant_first_token_timeout_seconds: int = Field(default=30, ge=1, le=300)
    assistant_total_timeout_seconds: int = Field(default=90, ge=1, le=600)
    assistant_history_max_messages: int = Field(default=12, ge=1, le=40)
    assistant_conversation_retention_days: int | None = Field(default=None, ge=1)
    assistant_deleted_retention_days: int = Field(default=7, ge=1, le=7)
    # 不得位于公共 upload_dir 内；仅通过带登录鉴权的附件接口读取。
    assistant_private_dir: str = str(BASE_DIR / "private_uploads" / "assistant")
    assistant_file_max_mb: int = Field(default=10, ge=1, le=25)
    assistant_files_per_message: int = Field(default=5, ge=1, le=10)
    assistant_files_total_mb: int = Field(default=25, ge=1, le=100)
    assistant_files_owner_quota_mb: int = Field(default=250, ge=1, le=10240)
    assistant_image_max_pixels: int = Field(default=20_000_000, ge=1, le=40_000_000)
    assistant_pdf_max_pages: int = Field(default=20, ge=1, le=100)
    assistant_zip_max_entries: int = Field(default=1000, ge=1, le=2000)
    assistant_zip_max_mb: int = Field(default=50, ge=1, le=100)
    assistant_orphan_hours: int = Field(default=24, ge=1, le=24)


@lru_cache
def get_settings() -> Settings:
    return Settings()
