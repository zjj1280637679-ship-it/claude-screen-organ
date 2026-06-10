# -*- coding: utf-8 -*-
"""L3：通过真实 stdio 传输启动 server.py 子进程，做 MCP 握手 + 工具调用。
这验证 Claude Code 实际加载插件时走的同一条路径（JSON-RPC over stdio）。"""
import os
import sys

import pytest

MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
SERVER = os.path.join(MCP_DIR, "server.py")


@pytest.mark.anyio
async def test_stdio_handshake_and_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"screen_info", "list_windows", "capture_screen",
                    "capture_window", "read_text", "wait_capture"} <= names

            # screen_info 端到端
            r = await session.call_tool("screen_info", {})
            txt = next((c.text for c in r.content if hasattr(c, "text")), "")
            assert "dpi_mode" in txt
            assert "per_monitor_v2" in txt

            # capture_screen 端到端，inline 返回图片块
            r2 = await session.call_tool(
                "capture_screen", {"max_edge": 600, "return_mode": "inline"})
            kinds = [type(c).__name__ for c in r2.content]
            assert any("Image" in k for k in kinds), f"应有图片块, 实际 {kinds}"


@pytest.fixture
def anyio_backend():
    return "asyncio"
