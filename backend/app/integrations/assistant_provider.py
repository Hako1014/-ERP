"""DeepSeek 兼容 SSE Provider，以及仅本地显式启用的 mock Provider。

项目已固定 HTTPX 0.28.1。评估维护中的 OpenAI Python SDK 后，因其当前传输栈
与项目固定版本不兼容，本模块直接复用 HTTPX，明确控制取消、超时且不配置重试。
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.errors import ErrorCode


def inline_image_content(data: bytes, media_type: str) -> dict[str, Any]:
    """构造只含已授权内存字节的图像块；不接受 URL、路径或客户端地址。"""
    if media_type not in {"image/jpeg", "image/png", "image/webp"} or not data or len(data) > 15 * 1024 * 1024:
        raise ProviderFailure(
            ErrorCode.VALIDATION_ERROR,
            "image_input_rejected",
            "图片格式或大小不受支持，请压缩后重试",
        )
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


@dataclass(frozen=True)
class ProviderFailure(Exception):
    code: ErrorCode
    reason: str
    message: str
    retryable: bool = False


class ProviderSession(Protocol):
    usage: ProviderUsage | None

    def __aiter__(self) -> AsyncIterator[str]: ...

    async def __anext__(self) -> str: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ProviderTurn:
    content: str
    tool_calls: tuple[ProviderToolCall, ...] = ()
    usage: ProviderUsage | None = None


class AssistantProvider(Protocol):
    async def start(self, messages: list[dict[str, Any]]) -> ProviderSession: ...

    async def start_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderTurn: ...


def _failure_for_status(status_code: int) -> ProviderFailure:
    if status_code in (401, 403):
        return ProviderFailure(
            ErrorCode.AI_AUTH_FAILED,
            "provider_auth_failed",
            ErrorCode.AI_AUTH_FAILED.default_message,
        )
    if status_code == 402:
        return ProviderFailure(
            ErrorCode.AI_INSUFFICIENT_BALANCE,
            "provider_insufficient_balance",
            ErrorCode.AI_INSUFFICIENT_BALANCE.default_message,
        )
    if status_code == 429:
        return ProviderFailure(
            ErrorCode.AI_RATE_LIMITED,
            "provider_rate_limited",
            ErrorCode.AI_RATE_LIMITED.default_message,
            retryable=True,
        )
    return ProviderFailure(
        ErrorCode.AI_PROVIDER_ERROR,
        "provider_service_error",
        ErrorCode.AI_PROVIDER_ERROR.default_message,
        retryable=status_code >= 500,
    )


def _provider_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        port = parsed.port
    except ValueError:
        parsed = urlsplit("")
        port = -1
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.deepseek.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderFailure(
            ErrorCode.AI_UNCONFIGURED,
            "provider_host_not_allowed",
            ErrorCode.AI_UNCONFIGURED.default_message,
        )
    return "https://api.deepseek.com/chat/completions"


def _parse_usage(value: Any) -> ProviderUsage | None:
    if not isinstance(value, dict):
        return None
    prompt = value.get("prompt_tokens")
    completion = value.get("completion_tokens")
    total = value.get("total_tokens")
    if not all(isinstance(item, int) and item >= 0 for item in (prompt, completion, total)):
        return None
    return ProviderUsage(prompt, completion, total)


class DeepSeekSession:
    def __init__(self, client: httpx.AsyncClient, response: httpx.Response):
        self._client = client
        self._response = response
        self._iterator = self._read_deltas()
        self._closed = False
        self.usage: ProviderUsage | None = None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        return await self._iterator.__anext__()

    async def _read_deltas(self) -> AsyncIterator[str]:
        received_done = False
        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    received_done = True
                    return
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    raise ProviderFailure(
                        ErrorCode.AI_PROVIDER_ERROR,
                        "provider_invalid_stream",
                        ErrorCode.AI_PROVIDER_ERROR.default_message,
                    ) from None
                if not isinstance(event, dict) or event.get("error"):
                    raise ProviderFailure(
                        ErrorCode.AI_PROVIDER_ERROR,
                        "provider_stream_error",
                        ErrorCode.AI_PROVIDER_ERROR.default_message,
                    )
                self.usage = _parse_usage(event.get("usage")) or self.usage
                choices = event.get("choices")
                if not isinstance(choices, list):
                    continue
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") or {}
                    # 仅输出最终回答；推理与工具载荷留在服务端，且当前不开放工具分发。
                    if delta.get("tool_calls") or delta.get("function_call"):
                        raise ProviderFailure(
                            ErrorCode.AI_PROVIDER_ERROR,
                            "unexpected_tool_payload",
                            ErrorCode.AI_PROVIDER_ERROR.default_message,
                        )
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text
            if not received_done:
                raise ProviderFailure(
                    ErrorCode.AI_PROVIDER_ERROR,
                    "provider_stream_incomplete",
                    ErrorCode.AI_PROVIDER_ERROR.default_message,
                    retryable=True,
                )
        except httpx.TimeoutException:
            raise ProviderFailure(
                ErrorCode.AI_TIMEOUT,
                "provider_timeout",
                ErrorCode.AI_TIMEOUT.default_message,
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise ProviderFailure(
                ErrorCode.AI_PROVIDER_ERROR,
                "provider_connection_error",
                ErrorCode.AI_PROVIDER_ERROR.default_message,
                retryable=True,
            ) from None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._iterator.aclose()
        finally:
            try:
                await self._response.aclose()
            finally:
                await self._client.aclose()


class DeepSeekProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    async def start(self, messages: list[dict[str, Any]]) -> ProviderSession:
        settings = self._settings
        if not settings.assistant_enabled:
            raise ProviderFailure(
                ErrorCode.AI_UNCONFIGURED,
                "assistant_disabled",
                ErrorCode.AI_UNCONFIGURED.default_message,
            )
        if not settings.assistant_api_key or not settings.assistant_model:
            raise ProviderFailure(
                ErrorCode.AI_UNCONFIGURED,
                "assistant_not_configured",
                ErrorCode.AI_UNCONFIGURED.default_message,
            )

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            transport=self._transport,
        )
        url = _provider_url(settings.assistant_base_url)
        request = client.build_request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {settings.assistant_api_key}"},
            json={
                "model": settings.assistant_model,
                "messages": messages,
                "stream": True,
                # DeepSeek V4.1-Flash 默认启用思考模式；本助手只展示最终回答，
                # 关闭思考模式避免 reasoning tokens 耗尽小输出上限且不转发推理内容。
                "thinking": {"type": "disabled"},
                "max_tokens": settings.assistant_max_output_tokens,
                "stream_options": {"include_usage": True},
            },
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            raise ProviderFailure(
                ErrorCode.AI_TIMEOUT,
                "provider_timeout",
                ErrorCode.AI_TIMEOUT.default_message,
                retryable=True,
            ) from None
        except httpx.TransportError:
            await client.aclose()
            raise ProviderFailure(
                ErrorCode.AI_PROVIDER_ERROR,
                "provider_connection_error",
                ErrorCode.AI_PROVIDER_ERROR.default_message,
                retryable=True,
            ) from None
        except BaseException:
            await client.aclose()
            raise

        if response.status_code < 200 or response.status_code >= 300:
            await response.aclose()
            await client.aclose()
            raise _failure_for_status(response.status_code)
        return DeepSeekSession(client, response)

    async def start_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        """完整收集一轮工具调用；工具参数仅交给后端校验，不流向浏览器。"""
        settings = self._settings
        if not settings.assistant_enabled:
            raise ProviderFailure(
                ErrorCode.AI_UNCONFIGURED,
                "assistant_disabled",
                ErrorCode.AI_UNCONFIGURED.default_message,
            )
        if not settings.assistant_api_key or not settings.assistant_model:
            raise ProviderFailure(
                ErrorCode.AI_UNCONFIGURED,
                "assistant_not_configured",
                ErrorCode.AI_UNCONFIGURED.default_message,
            )

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            transport=self._transport,
        )
        url = _provider_url(settings.assistant_base_url)
        request = client.build_request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {settings.assistant_api_key}"},
            json={
                "model": settings.assistant_model,
                "messages": messages,
                "stream": True,
                "thinking": {"type": "disabled"},
                "max_tokens": settings.assistant_max_output_tokens,
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "stream_options": {"include_usage": True},
            },
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            raise ProviderFailure(
                ErrorCode.AI_TIMEOUT,
                "provider_timeout",
                ErrorCode.AI_TIMEOUT.default_message,
                retryable=True,
            ) from None
        except httpx.TransportError:
            await client.aclose()
            raise ProviderFailure(
                ErrorCode.AI_PROVIDER_ERROR,
                "provider_connection_error",
                ErrorCode.AI_PROVIDER_ERROR.default_message,
                retryable=True,
            ) from None
        except BaseException:
            await client.aclose()
            raise

        if response.status_code < 200 or response.status_code >= 300:
            await response.aclose()
            await client.aclose()
            raise _failure_for_status(response.status_code)

        content: list[str] = []
        tool_fragments: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        received_done = False
        usage: ProviderUsage | None = None
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    received_done = True
                    break
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    raise ProviderFailure(
                        ErrorCode.AI_PROVIDER_ERROR,
                        "provider_invalid_stream",
                        ErrorCode.AI_PROVIDER_ERROR.default_message,
                    ) from None
                if not isinstance(event, dict) or event.get("error"):
                    raise ProviderFailure(
                        ErrorCode.AI_PROVIDER_ERROR,
                        "provider_stream_error",
                        ErrorCode.AI_PROVIDER_ERROR.default_message,
                    )
                usage = _parse_usage(event.get("usage")) or usage
                choices = event.get("choices")
                if not isinstance(choices, list):
                    continue
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    if isinstance(choice.get("finish_reason"), str):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        content.append(text)
                    fragments = delta.get("tool_calls")
                    if fragments is None:
                        if delta.get("function_call"):
                            raise ProviderFailure(
                                ErrorCode.AI_PROVIDER_ERROR,
                                "unsupported_function_call_payload",
                                ErrorCode.AI_PROVIDER_ERROR.default_message,
                            )
                        continue
                    if not isinstance(fragments, list) or len(tool_fragments) > 3:
                        raise ProviderFailure(
                            ErrorCode.AI_PROVIDER_ERROR,
                            "provider_tool_payload_invalid",
                            ErrorCode.AI_PROVIDER_ERROR.default_message,
                        )
                    for fragment in fragments:
                        if not isinstance(fragment, dict):
                            continue
                        index = fragment.get("index")
                        if not isinstance(index, int) or index < 0 or index >= 3:
                            raise ProviderFailure(
                                ErrorCode.AI_PROVIDER_ERROR,
                                "provider_tool_payload_invalid",
                                ErrorCode.AI_PROVIDER_ERROR.default_message,
                            )
                        item = tool_fragments.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        for field in ("id",):
                            value = fragment.get(field)
                            if isinstance(value, str):
                                item[field] += value
                        function = fragment.get("function")
                        if isinstance(function, dict):
                            for field in ("name", "arguments"):
                                value = function.get(field)
                                if isinstance(value, str):
                                    item[field] += value
                        if len(item["arguments"]) > 8_000:
                            raise ProviderFailure(
                                ErrorCode.AI_PROVIDER_ERROR,
                                "provider_tool_payload_too_large",
                                ErrorCode.AI_PROVIDER_ERROR.default_message,
                            )
            if not received_done:
                raise ProviderFailure(
                    ErrorCode.AI_PROVIDER_ERROR,
                    "provider_stream_incomplete",
                    ErrorCode.AI_PROVIDER_ERROR.default_message,
                    retryable=True,
                )
            if tool_fragments and finish_reason != "tool_calls":
                raise ProviderFailure(
                    ErrorCode.AI_PROVIDER_ERROR,
                    "provider_tool_finish_invalid",
                    ErrorCode.AI_PROVIDER_ERROR.default_message,
                )
            calls = tuple(
                ProviderToolCall(
                    id=item["id"],
                    name=item["name"],
                    arguments=item["arguments"],
                )
                for _, item in sorted(tool_fragments.items())
            )
            if any(not call.id or not call.name for call in calls):
                raise ProviderFailure(
                    ErrorCode.AI_PROVIDER_ERROR,
                    "provider_tool_payload_invalid",
                    ErrorCode.AI_PROVIDER_ERROR.default_message,
                )
            return ProviderTurn("".join(content), calls, usage)
        except httpx.TimeoutException:
            raise ProviderFailure(
                ErrorCode.AI_TIMEOUT,
                "provider_timeout",
                ErrorCode.AI_TIMEOUT.default_message,
                retryable=True,
            ) from None
        except httpx.TransportError:
            raise ProviderFailure(
                ErrorCode.AI_PROVIDER_ERROR,
                "provider_connection_error",
                ErrorCode.AI_PROVIDER_ERROR.default_message,
                retryable=True,
            ) from None
        finally:
            await response.aclose()
            await client.aclose()


class _MockSession:
    def __init__(self, response: str):
        self._parts = [response[index : index + 12] for index in range(0, len(response), 12)]
        self._index = 0
        self._closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._closed or self._index >= len(self._parts):
            raise StopAsyncIteration
        await asyncio.sleep(0)
        part = self._parts[self._index]
        self._index += 1
        return part

    async def aclose(self) -> None:
        self._closed = True


class MockProvider:
    """仅用于显式本地开发的响应生成器，不创建网络客户端。"""

    async def start(self, messages: list[dict[str, str]]) -> ProviderSession:
        last_message = next(
            (item["content"] for item in reversed(messages) if item["role"] == "user"),
            "",
        )
        answer = (
            "【本地模拟模式】这不是 DeepSeek 的回答，也没有查询 ERP 数据。"
            f"我已收到你的问题：{last_message}"
        )
        return _MockSession(answer)
