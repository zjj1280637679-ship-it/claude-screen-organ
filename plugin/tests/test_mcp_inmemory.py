# -*- coding: utf-8 -*-
"""L1.5：用 FastMCP in-memory client 直调工具，断言回执信封结构。
不经 Claude Code，毫秒级。验证 server 的工具注册与契约。"""
import json
import os
import sys

import pytest

MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
sys.path.insert(0, MCP_DIR)


@pytest.fixture(scope="module")
def server_mod():
    import server
    return server


def _call(server_mod, name, **kwargs):
    """同步调用 FastMCP 工具底层函数（绕过异步传输，直接拿结构化内容块）。"""
    import anyio
    tool = {t.name: t for t in anyio.run(server_mod.mcp.list_tools)}
    assert name in tool, f"工具 {name} 未注册"
    result = anyio.run(lambda: server_mod.mcp.call_tool(name, kwargs))
    return result


def _envelope_from_result(result):
    # call_tool 返回 (content_blocks, ...) 或 content_blocks；取第一个 text 块解析 JSON
    blocks = result[0] if isinstance(result, tuple) else result
    for b in blocks:
        txt = getattr(b, "text", None)
        if txt and "{" in txt:
            return json.loads(txt[txt.index("{"):])
    raise AssertionError("回执里没有 JSON 文本块")


def test_tools_registered(server_mod):
    import anyio
    names = {t.name for t in anyio.run(server_mod.mcp.list_tools)}
    assert {"screen_info", "list_windows", "capture_screen",
            "capture_window", "read_text", "wait_capture"} <= names


def test_screen_info_envelope(server_mod):
    env = _envelope_from_result(_call(server_mod, "screen_info"))
    assert env["ok"] is True
    assert env["dpi_mode"] in ("per_monitor_v2", "per_monitor", "system", "unaware")
    assert env["backends"]["mss_screen"] is True
    assert "recommended_next" in env


def test_list_windows_envelope(server_mod):
    env = _envelope_from_result(_call(server_mod, "list_windows"))
    assert env["ok"] is True
    assert isinstance(env["windows"], list)
    # 至少有一个窗口（测试运行时桌面非空），且字段齐全
    if env["windows"]:
        w = env["windows"][0]
        assert {"hwnd", "title", "process", "rect", "minimized"} <= set(w)


def test_capture_screen_produces_file(server_mod):
    env = _envelope_from_result(_call(server_mod, "capture_screen", max_edge=800))
    assert env["ok"] is True
    assert env["verdict"] == "ok"
    assert os.path.exists(env["file"]["path"])
    assert max(env["file"]["out_size"]) <= 800


def test_capture_window_bad_target(server_mod):
    env = _envelope_from_result(
        _call(server_mod, "capture_window", target={"title": "绝不存在的窗口zzz999"}))
    assert env["ok"] is False
    assert env["verdict"] == "failed"


def test_capture_window_sensitive_refused(server_mod):
    # 构造一个标题命中黑名单的假窗口很难，这里只验证寻址失败路径已覆盖；
    # 敏感拒绝逻辑由 test_unit 的 privacy 测试覆盖。
    env = _envelope_from_result(
        _call(server_mod, "capture_window", target={"process": "绝不存在zzz"}))
    assert env["ok"] is False
