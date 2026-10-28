import uuid
from typing import Any

from app.core.logging import request_id_var


class RequestIdMiddleware:
    """为每个 HTTP 请求透传或生成 request_id，注入日志上下文与响应头。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = dict(scope.get("headers", []))
        request_id = raw_headers.get(b"x-request-id", b"").decode("latin-1") or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
