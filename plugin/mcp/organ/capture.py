# -*- coding: utf-8 -*-
"""捕获引擎与降级链。

T0 实测结论（Win10 19045 / GTX 750Ti）：
- PrintWindow(PW_RENDERFULLCONTENT) 对前台 Chrome/Notepad/Electron 均出真内容（无黑帧）；
  注意 PrintWindow(flags=0) 对 GPU 窗口会得白图（contrast 仍 35，但客户区是空的）→ 故只用 FULL。
- windows-capture(WGC) 对后台窗口可出真内容，单帧 ~180-280ms；
  本机 IsBorderRequired 不支持，draw_border 必须传 None（传 False 会抛异常）。
- 降级链：PrintWindow(FULL) → 质量检测 → 不行则 WGC → 不行则 mss 裁屏+身份探针。
"""
import ctypes
import threading
import time

import mss
import win32gui
import win32ui
from PIL import Image

from . import quality, wininfo

PW_RENDERFULLCONTENT = 0x00000002


# ---------------- 全屏 / 区域 ----------------
def capture_screen(monitor: int = 0, region=None) -> Image.Image:
    """monitor: 0=全虚拟桌面, 1+=物理显示器。region=(x,y,w,h) 相对该 monitor 左上。"""
    with mss.MSS() as sct:
        mons = sct.monitors
        idx = monitor if 0 <= monitor < len(mons) else 0
        mon = mons[idx]
        if region:
            x, y, w, h = region
            grab = {"left": mon["left"] + x, "top": mon["top"] + y,
                    "width": w, "height": h}
        else:
            grab = mon
        raw = sct.grab(grab)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ---------------- 单窗口：三种后端 ----------------
def _printwindow(hwnd: int, flags: int) -> Image.Image:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸异常 {w}x{h}")
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flags)
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), data, "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _wgc(title: str, timeout: float = 6.0):
    from windows_capture import WindowsCapture
    result = {}
    done = threading.Event()
    cap = WindowsCapture(cursor_capture=False, draw_border=None,
                         monitor_index=None, window_name=title)

    @cap.event
    def on_frame_arrived(frame, capture_control):
        if not done.is_set():
            buf = frame.frame_buffer
            result["img"] = Image.fromarray(buf[:, :, :3][:, :, ::-1].copy())
            done.set()
            capture_control.stop()

    @cap.event
    def on_closed():
        done.set()

    ctrl = cap.start_free_threaded()
    done.wait(timeout)
    try:
        ctrl.stop()
    except Exception:
        pass
    if "img" not in result:
        raise RuntimeError(f"WGC {timeout}s 内无帧")
    return result["img"]


def _mss_crop(rect) -> Image.Image:
    l, t, r, b = rect
    with mss.MSS() as sct:
        raw = sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _restore_noactivate(hwnd):
    SW_SHOWNOACTIVATE = 4
    win32gui.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    time.sleep(0.15)


def capture_window(hwnd: int, mode: str = "quiet", allow_unverified_bbox=False,
                   restore_if_minimized=False, wait_stable=False,
                   delay_ms: int = 0):
    """返回 (PIL.Image|None, capture_report dict)。

    capture_report.verdict ∈ ok|blank_suspected|occluded_risk|identity_mismatch|
                              frozen_frame_risk|minimized|failed
    """
    rpt = {"method": None, "verdict": None, "warnings": [], "attempts": []}
    if not win32gui.IsWindow(hwnd):
        rpt["verdict"] = "failed"
        rpt["warnings"].append("无效窗口句柄")
        return None, rpt

    title = win32gui.GetWindowText(hwnd)
    minimized = bool(win32gui.IsIconic(hwnd))
    if minimized:
        if restore_if_minimized:
            _restore_noactivate(hwnd)
            minimized = bool(win32gui.IsIconic(hwnd))
        if minimized:
            rpt["verdict"] = "minimized"
            rpt["warnings"].append("窗口已最小化，无有效像素；可设 restore_if_minimized=true")
            return None, rpt

    if delay_ms:
        time.sleep(min(delay_ms, 5000) / 1000.0)

    if mode == "foreground":
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            rpt["warnings"].append("SetForegroundWindow 失败，可能因无输入焦点")
        time.sleep(0.25)

    rect = wininfo.get_window_rect(hwnd)
    occ = wininfo.occlusion_probe(hwnd, rect)
    chromium = _is_chromium(title, hwnd)

    # ① PrintWindow(FULL)
    img = None
    try:
        img = _printwindow(hwnd, PW_RENDERFULLCONTENT)
        a = quality.assess_window_capture(hwnd, img, rect)
        rpt["attempts"].append({"method": "printwindow_full", "quality": a})
        if not a["likely_blank"]:
            rpt["method"] = "printwindow_full"
            # PrintWindow 对 GDI 窗口即便被遮挡也取实时内容；冻结旧帧只是 Chromium 的隐患
            rpt["verdict"] = _frozen_or_ok(occ, rpt, chromium)
            return _finalize(img, rect, rpt, occ)
        rpt["warnings"].append("PrintWindow 疑似空白/低信息，尝试 WGC")
    except Exception as e:
        rpt["attempts"].append({"method": "printwindow_full", "error": str(e)[:160]})

    # ② WGC
    try:
        img = _wgc(title)
        a = quality.assess_window_capture(hwnd, img, rect)
        rpt["attempts"].append({"method": "wgc", "quality": a})
        if not a["likely_blank"]:
            rpt["method"] = "wgc"
            rpt["verdict"] = _frozen_or_ok(occ, rpt, chromium)
            return _finalize(img, rect, rpt, occ)
        rpt["warnings"].append("WGC 也疑似空白")
    except Exception as e:
        rpt["attempts"].append({"method": "wgc", "error": str(e)[:160]})

    # ③ mss 裁屏 + 身份探针
    if occ["possibly_occluded"] and not allow_unverified_bbox:
        rpt["method"] = None
        rpt["verdict"] = "occluded_risk"
        rpt["warnings"].append(
            f"窗口被遮挡(match_ratio={occ['match_ratio']})，裁屏会拍到上层窗口；"
            "建议 mode=foreground，或确认后 allow_unverified_bbox=true")
        return None, rpt
    try:
        img = _mss_crop(rect)
        a = quality.assess_window_capture(hwnd, img, rect)
        rpt["attempts"].append({"method": "screen_crop", "quality": a})
        rpt["method"] = "screen_crop"
        if not occ["possibly_occluded"]:
            rpt["verdict"] = "blank_suspected" if a["likely_blank"] else "ok"
        else:
            rpt["verdict"] = "identity_mismatch"
            rpt["warnings"].append("已按 allow_unverified_bbox 放行，但身份未验证")
        return _finalize(img, rect, rpt, occ)
    except Exception as e:
        rpt["attempts"].append({"method": "screen_crop", "error": str(e)[:160]})

    rpt["verdict"] = "failed"
    return None, rpt


CHROMIUM_HINTS = ("chrome", "edge", "electron", "claude", "code", "discord",
                  "slack", "spotify", "msedge", "brave", "vivaldi", "opera")


def _is_chromium(title: str, hwnd: int) -> bool:
    try:
        proc = wininfo.get_process_name(hwnd).lower()
    except Exception:
        proc = ""
    blob = (proc + " " + (title or "")).lower()
    return any(h in blob for h in CHROMIUM_HINTS)


def _frozen_or_ok(occ, rpt, chromium: bool):
    # Chromium 被遮挡时因 occlusion 节流可能渲染冻结旧帧；GDI/原生窗口经 PrintWindow
    # 取的是实时内容，遮挡不致旧帧——故只对 Chromium 系给冻结帧风险提示
    if occ["possibly_occluded"] and chromium:
        rpt["warnings"].append(
            "Chromium 系窗口被遮挡：可能是渲染冻结的旧帧，"
            "需要最新画面请 mode=foreground")
        return "frozen_frame_risk"
    return "ok"


def _finalize(img, rect, rpt, occ):
    rpt["occlusion"] = occ
    rpt["window_rect"] = list(rect)
    return img, rpt
