"""复用原模型/工具/配额流程的持久 SSE 适配器，每次落库后才向客户端发布。"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from app.core.data_scope import clear_scope_policy
from app.core.errors import BizException, ErrorCode
from app.services import assistant_conversations as conversations


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


async def guarded_await(awaitable, check):
    """上游尚未返回时也检查取消/清空/撤权，及时取消在途模型任务。"""
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.5)
            check()
        return task.result()
    except BaseException:
        task.cancel()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        # start 已经返回 session 的竞态下也必须释放上游连接。
        if callable(getattr(result, "aclose", None)):
            await result.aclose()
        raise


class GuardedProvider:
    """在上游等待前后重新核验运行身份，取消后不继续处理工具轮次。"""

    def __init__(self, provider, check: Callable[[], None]):
        self.provider, self.check = provider, check

    async def start(self, messages):
        self.check()
        session = await guarded_await(self.provider.start(messages), self.check)
        try:
            self.check()
        except BaseException:
            await session.aclose()
            raise
        return GuardedSession(session, self.check)

    @property
    def start_with_tools(self):
        return self._start_with_tools if callable(getattr(self.provider, "start_with_tools", None)) else None

    async def _start_with_tools(self, messages, tools):
        self.check()
        turn = await guarded_await(self.provider.start_with_tools(messages, tools), self.check)
        self.check()
        return turn


class GuardedSession:
    def __init__(self, session, check):
        self.session, self.check = session, check

    @property
    def usage(self):
        return getattr(self.session, "usage", None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.check()
        chunk = await guarded_await(self.session.__anext__(), self.check)
        self.check()
        return chunk

    async def aclose(self):
        await self.session.aclose()


async def events(*, run: conversations.Run, session_factory, validate_login: Callable[[], None],
                 start_stream, repeated: bool = False) -> AsyncIterator[bytes]:
    def check():
        validate_login()
        with session_factory() as db:
            conversations.assert_run(db, run)

    upstream = None
    stream_db = None
    terminal = False
    try:
        if repeated:
            with session_factory() as db:
                reply, version = conversations.replay_message(db, run)
            yield sse("accepted", {"request_id": run.request_id, "user_message_id": run.user_id,
                                   "seq": run.user_seq, "conversation_version": version, "replayed": True})
            # 相同幂等键只重放持久状态；生成中也不会发起第二次模型请求。
            yield sse("snapshot", {"message": reply, "conversation_version": version})
            yield sse("done", {"request_id": run.request_id, "assistant_message_id": reply["id"],
                               "seq": reply["seq"], "status": reply["status"], "conversation_version": version,
                               "finish_reason": "replayed"})
            terminal = True
            return
        check()
        yield sse("accepted", {"request_id": run.request_id, "user_message_id": run.user_id,
                               "assistant_message_id": run.assistant_id, "seq": run.user_seq,
                               "conversation_version": run.version, "replayed": False})
        stream_db = session_factory()
        upstream = await start_stream(stream_db, check)
        async for frame in upstream.body_iterator:
            check()
            # 原流每次 yield 一个完整事件；协议文本只在进程内解析，不进入普通日志。
            text = frame.decode() if isinstance(frame, bytes) else frame
            event = next(line[7:] for line in text.splitlines() if line.startswith("event: "))
            data = json.loads(next(line[6:] for line in text.splitlines() if line.startswith("data: ")))
            with session_factory() as db:
                data = conversations.persist_event(db, run, event, data)
            terminal = event in ("done", "error")
            yield sse(event, data)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = exc.code if isinstance(exc, BizException) else ErrorCode.INTERNAL_ERROR
        status = "INTERRUPTED" if code in (ErrorCode.UNAUTHORIZED, ErrorCode.FORBIDDEN, ErrorCode.NOT_FOUND,
                    ErrorCode.CONFLICT, ErrorCode.AI_SESSION_EXPIRED, ErrorCode.AI_SESSION_INVALID,
                    ErrorCode.AI_SESSION_VERSION_CONFLICT) else "FAILED"
        # 禁止将上游错误/SQL 参数中的正文或连接信息写入响应和日志。
        with session_factory() as db:
            conversations.interrupt_run(db, run, status=status, error_code=code.value)
        terminal = True
        yield sse("error", {"request_id": run.request_id, "code": code.value,
                            "message": code.default_message, "assistant_message_id": run.assistant_id,
                            "seq": run.assistant_seq, "status": status, "retryable": False})
    finally:
        # 先封存，再释放旧流；迟到 delta 无法越过会话版本或终态条件。
        if not terminal and not repeated:
            with session_factory() as db:
                conversations.interrupt_run(db, run)
        if upstream is not None:
            await upstream.body_iterator.aclose()
        if stream_db is not None:
            clear_scope_policy(stream_db)
            stream_db.close()
