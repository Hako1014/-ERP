"""仅供协议测试的独立stdio进程，不连接业务数据库或Redis。"""

import logging
import os
import sys

import anyio
from mcp.server.stdio import stdio_server
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.mcp_server import create_server
from app.services.mcp_runtime import McpRuntime
from tests.test_mcp_foundation import MemoryLimits


async def main():
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR, force=True)
    engine = create_engine(os.environ["ERP_MCP_TEST_DB"])
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    server = create_server(McpRuntime(read_factory=factory, audit_factory=factory,
                                      token=os.environ["ERP_MCP_TOKEN"], limits=MemoryLimits()))
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        engine.dispose()


if __name__ == "__main__":
    anyio.run(main)
