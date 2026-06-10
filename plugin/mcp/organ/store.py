# -*- coding: utf-8 -*-
"""截图落盘与生命周期。默认 %LOCALAPPDATA%\\screen-organ\\captures，
装为插件后用 ${CLAUDE_PLUGIN_DATA}。启动时清理 >24h 或超 200 张的旧文件。"""
import os
import time

MAX_AGE_SEC = 24 * 3600
MAX_FILES = 200


def capture_dir() -> str:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or \
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                     "screen-organ")
    d = os.path.join(base, "captures")
    os.makedirs(d, exist_ok=True)
    return d


def cleanup(d: str = None) -> dict:
    d = d or capture_dir()
    now = time.time()
    files = []
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            files.append((p, os.path.getmtime(p)))
    removed = 0
    # 按年龄
    for p, mt in files:
        if now - mt > MAX_AGE_SEC:
            try:
                os.remove(p); removed += 1
            except Exception:
                pass
    # 按数量（保留最新 MAX_FILES）
    remain = [(p, mt) for p, mt in files if os.path.exists(p)]
    remain.sort(key=lambda x: x[1], reverse=True)
    for p, _ in remain[MAX_FILES:]:
        try:
            os.remove(p); removed += 1
        except Exception:
            pass
    return {"dir": d, "removed": removed, "kept": min(len(remain), MAX_FILES)}


def usage(d: str = None) -> dict:
    d = d or capture_dir()
    n = 0
    total = 0
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            n += 1
            total += os.path.getsize(p)
    return {"dir": d, "files": n, "bytes": total}


def new_path(label: str, ext: str, counter: list = [0]) -> str:
    """生成不依赖 time-of-day 随机的稳定文件名（毫秒+进程内计数器）。"""
    counter[0] += 1
    ms = int(time.time() * 1000)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (label or "cap"))[:40]
    return os.path.join(capture_dir(), f"{safe}-{ms}-{counter[0]}.{ext}")
