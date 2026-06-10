# -*- coding: utf-8 -*-
"""时序捕获：等渲染稳定 / 等画面变化 / 等窗口出现。硬上限 30s，永不返回帧序列。"""
import time

import numpy as np

from . import capture, wininfo

MAX_TIMEOUT = 30.0


def _frame_of(target_hwnd, mode):
    img, rpt = capture.capture_window(target_hwnd, mode=mode)
    return img, rpt


def _diff(a, b):
    if a is None or b is None or a.size != b.size:
        return 999.0
    aa = np.asarray(a.convert("L").resize((160, 120)), dtype=np.float64)
    bb = np.asarray(b.convert("L").resize((160, 120)), dtype=np.float64)
    return float(np.abs(aa - bb).mean())


def wait_capture(hwnd: int, until: str = "stable", timeout: float = 8.0,
                 mode: str = "quiet", change_threshold: float = 8.0,
                 stable_threshold: float = 1.5, poll_ms: int = 300):
    timeout = min(max(timeout, 0.5), MAX_TIMEOUT)
    deadline = time.time() + timeout
    rpt = {"until": until, "timeout": timeout, "polls": 0, "satisfied": False}

    if until == "window_appears":
        # hwnd 可能尚不存在；这里把 hwnd 当作 None，调用方改用 title 轮询
        return None, {**rpt, "error": "window_appears 须用 wait_for_window(title)"}

    prev = None
    last_img, last_rpt = None, None
    while time.time() < deadline:
        rpt["polls"] += 1
        img, crpt = _frame_of(hwnd, mode)
        last_img, last_rpt = img, crpt
        if img is not None:
            d = _diff(prev, img)
            if until == "stable" and prev is not None and d <= stable_threshold:
                rpt["satisfied"] = True
                rpt["final_diff"] = round(d, 2)
                break
            if until == "change" and prev is not None and d >= change_threshold:
                rpt["satisfied"] = True
                rpt["final_diff"] = round(d, 2)
                break
            prev = img
        time.sleep(poll_ms / 1000.0)
    return last_img, {**rpt, "capture": last_rpt}


def wait_for_window(title_substr: str, timeout: float = 10.0, poll_ms: int = 400):
    timeout = min(max(timeout, 0.5), MAX_TIMEOUT)
    deadline = time.time() + timeout
    polls = 0
    while time.time() < deadline:
        polls += 1
        cands = [w for w in wininfo.list_windows(include_occlusion=False)
                 if title_substr.lower() in w["title"].lower()]
        if cands:
            return cands[0]["hwnd"], {"satisfied": True, "polls": polls,
                                      "hwnd": cands[0]["hwnd"],
                                      "title": cands[0]["title"]}
        time.sleep(poll_ms / 1000.0)
    return None, {"satisfied": False, "polls": polls,
                  "error": f"{timeout}s 内未出现标题含 '{title_substr}' 的窗口"}
