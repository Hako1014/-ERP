from datetime import datetime
from pathlib import Path
from typing import Protocol
import uuid

from app.core.config import get_settings


class Storage(Protocol):
    """文件存储抽象：生产可替换为对象存储实现。"""

    def save(self, data: bytes, extension: str) -> str:
        """保存文件并返回访问 URL。"""


class LocalStorage:
    """本地磁盘存储，按日期分目录，返回可访问的 /uploads 相对路径。"""

    def __init__(self, upload_dir: Path | str, url_prefix: str = "/uploads"):
        self.upload_dir = Path(upload_dir)
        self.url_prefix = url_prefix

    def save(self, data: bytes, extension: str) -> str:
        now = datetime.now()
        subdir = self.upload_dir / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        subdir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{extension}"
        (subdir / filename).write_bytes(data)
        return f"{self.url_prefix}/{now:%Y}/{now:%m}/{now:%d}/{filename}"


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().upload_dir)
    return _storage
