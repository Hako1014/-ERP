"""stdio入口：在backend目录执行 python -m app.mcp_server。"""

import logging
import os
import sys

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, Tool, ToolAnnotations
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.mcp_credentials import ACCESS_TOOL
from app.services.mcp_limits import McpLimits
from app.services.mcp_runtime import AccessResult, McpRuntime
from app.services.mcp_tool_output_schemas import (
    get_tool_output_schema,
    include_error_output_schema,
)
from app.services.mcp_tool_registry import ToolEnvelope, get_tool


def create_server(runtime: McpRuntime) -> Server:
    server = Server("erp-readonly", version="1.0.0")

    @server.list_tools()
    async def list_tools():
        result = await runtime.execute(ACCESS_TOOL, {}, discovery=True)
        if isinstance(result, CallToolResult):
            data = result.structuredContent
            raise McpError(ErrorData(code=-32001, message=data["message"], data=data))
        tools = []
        for name in result:
            if name == ACCESS_TOOL:
                tools.append(Tool(
                    name=ACCESS_TOOL,
                    description="查询本人的有效MCP权限、模块及数据范围；不接受身份或公司参数。",
                    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                    outputSchema=include_error_output_schema(
                        AccessResult.model_json_schema(),
                    ),
                    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
                ))
                continue
            spec = get_tool(name)
            if spec is None:
                continue
            tools.append(Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.arguments.model_json_schema(),
                outputSchema=get_tool_output_schema(
                    spec.name, ToolEnvelope.model_json_schema(),
                ),
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
            ))
        return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        # 自行严格验证并返回安全错误，SDK默认错误可能回显用户参数。
        return await runtime.execute(name, arguments)

    return server


async def main():
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    logging.getLogger("mcp").setLevel(logging.ERROR)
    settings = get_settings()
    if make_url(settings.database_url).drivername != "mysql+pymysql":
        logger = logging.getLogger(__name__)
        logger.error("MCP生产入口要求mysql+pymysql数据源")
        return
    kwargs = {"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10}
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=4,
                           max_overflow=0, pool_timeout=5, connect_args=kwargs)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    runtime = McpRuntime(read_factory=factory, audit_factory=factory,
                         token=os.environ.get("ERP_MCP_TOKEN", ""),
                         limits=McpLimits(settings.redis_url,
                                          rpm=settings.mcp_query_requests_per_minute,
                                          concurrent=settings.mcp_query_concurrent,
                                          lease_seconds=60),
                         timeout_seconds=settings.mcp_query_timeout_seconds,
                         max_result_bytes=settings.mcp_query_max_result_bytes)
    server = create_server(runtime)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        anyio.run(main)
    except Exception:
        # 启动/连接异常不打印可能含连接参数的完整异常。
        logging.basicConfig(stream=sys.stderr, level=logging.ERROR, force=True)
        logging.getLogger(__name__).error("MCP进程未完成，请检查服务端配置和依赖")
        sys.exit(1)
