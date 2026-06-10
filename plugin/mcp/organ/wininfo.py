# -*- coding: utf-8 -*-
"""窗口枚举与寻址。Alt-Tab 视角 + UWP 进程穿透 + 遮挡探针。"""
import ctypes
from ctypes import wintypes
import os

import win32api
import win32con
import win32gui
import win32process

DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9
GA_ROOT = 2
GA_ROOTOWNER = 3


def get_process_name(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        h = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            name = os.path.basename(win32process.GetModuleFileNameEx(h, 0))
        finally:
            h.close()
        # UWP 穿透：顶层是 ApplicationFrameHost，找子窗口里的真实进程
        if name.lower() == "applicationframehost.exe":
            real = _uwp_real_process(hwnd, pid)
            if real:
                return real
        return name
    except Exception:
        return "?"


def _uwp_real_process(hwnd, host_pid):
    found = {}

    def cb(child, _):
        try:
            cls = win32gui.GetClassName(child)
            if cls == "Windows.UI.Core.CoreWindow":
                _, cpid = win32process.GetWindowThreadProcessId(child)
                if cpid != host_pid:
                    h = win32api.OpenProcess(
                        win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, cpid)
                    try:
                        found["name"] = os.path.basename(
                            win32process.GetModuleFileNameEx(h, 0))
                    finally:
                        h.close()
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return found.get("name")


def is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except Exception:
        return False
    return cloaked.value != 0


def _is_alt_tab_window(hwnd: int) -> bool:
    """Raymond Chen 的 Alt-Tab 候选窗口判定。"""
    if not win32gui.IsWindowVisible(hwnd):
        return False
    # 沿 owner 链找最后活动弹窗，须等于自身
    root = win32gui.GetAncestor(hwnd, GA_ROOTOWNER)
    walk = root
    try:
        while True:
            last = ctypes.windll.user32.GetLastActivePopup(walk)
            if win32gui.IsWindowVisible(last):
                break
            if last == walk:
                break
            walk = last
        if walk != hwnd:
            return False
    except Exception:
        pass
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex & win32con.WS_EX_TOOLWINDOW:
        if not (ex & win32con.WS_EX_APPWINDOW):
            return False
    return True


def get_window_rect(hwnd: int):
    """优先用 DWM 扩展边界（排除不可见阴影），回退 GetWindowRect。"""
    try:
        r = wintypes.RECT()
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
        if res == 0:
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return win32gui.GetWindowRect(hwnd)


def occlusion_probe(hwnd: int, rect=None) -> dict:
    """5 采样点 WindowFromPoint→GA_ROOT 对比目标根窗口；match_ratio<0.6 视为被遮挡。"""
    if rect is None:
        rect = win32gui.GetWindowRect(hwnd)
    l, t, r, b = rect
    if r <= l or b <= t:
        return {"match_ratio": 0.0, "possibly_occluded": True, "sampled": 0}
    target_root = win32gui.GetAncestor(hwnd, GA_ROOT)
    pts = [((l + r) // 2, (t + b) // 2),
           (l + (r - l) // 4, t + (b - t) // 4),
           (r - (r - l) // 4, t + (b - t) // 4),
           (l + (r - l) // 4, b - (b - t) // 4),
           (r - (r - l) // 4, b - (b - t) // 4)]
    hits = 0
    for x, y in pts:
        try:
            top = win32gui.WindowFromPoint((x, y))
            if top and win32gui.GetAncestor(top, GA_ROOT) == target_root:
                hits += 1
        except Exception:
            pass
    ratio = hits / len(pts)
    return {"match_ratio": round(ratio, 2),
            "possibly_occluded": ratio < 0.6, "sampled": len(pts)}


def list_windows(filter_text: str = None, include_occlusion: bool = True) -> list:
    out = []

    def cb(hwnd, _):
        if not _is_alt_tab_window(hwnd) or is_cloaked(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        proc = get_process_name(hwnd)
        if filter_text:
            f = filter_text.lower()
            if f not in title.lower() and f not in proc.lower():
                return
        rect = get_window_rect(hwnd)
        minimized = bool(win32gui.IsIconic(hwnd))
        item = {
            "hwnd": hwnd, "title": title, "process": proc,
            "rect": list(rect), "minimized": minimized,
        }
        if include_occlusion and not minimized:
            item["occlusion"] = occlusion_probe(hwnd, rect)
        out.append(item)

    win32gui.EnumWindows(cb, None)
    return out


def resolve_target(target: dict) -> dict:
    """把 {hwnd|title|process} 解析为唯一窗口。多匹配返回候选列表。"""
    if not isinstance(target, dict):
        return {"error": "target 必须是 {hwnd:..} 或 {title:..} 或 {process:..}"}
    if "hwnd" in target and target["hwnd"]:
        hwnd = int(target["hwnd"])
        if not win32gui.IsWindow(hwnd):
            return {"error": f"hwnd {hwnd} 不是有效窗口"}
        return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
                "process": get_process_name(hwnd)}
    key = "title" if target.get("title") else "process" if target.get("process") else None
    if not key:
        return {"error": "target 需要 hwnd / title / process 之一"}
    needle = str(target[key]).lower()
    cands = [w for w in list_windows(include_occlusion=False)
             if needle in str(w[key]).lower()]
    if not cands:
        return {"error": f"没有窗口的 {key} 匹配 '{target[key]}'"}
    if len(cands) > 1:
        return {"ambiguous": True,
                "candidates": [{"hwnd": c["hwnd"], "title": c["title"],
                                "process": c["process"]} for c in cands]}
    return {"hwnd": cands[0]["hwnd"], "title": cands[0]["title"],
            "process": cands[0]["process"]}
