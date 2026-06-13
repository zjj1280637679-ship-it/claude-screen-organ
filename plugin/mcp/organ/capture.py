# -*- coding: utf-8 -*-
"""窗口内容获取引擎。

设计立场（2026-06-13 修订）：用户说"截图"时，要的不是"屏幕截图"，而是
**目标窗口的干净图像**——哪怕窗口在后台、被完全遮挡。所以本引擎默认走
**窗口内容直取**：PrintWindow 与 WGC 直接从窗口自己的渲染表面拿像素，
与窗口是否被遮挡、是否在前台**完全无关**，也不抢焦点、不干扰用户。

只有降级链最末端的 mss 裁屏才是真"截屏"（从屏幕那块矩形抠图），它才会
拍到上层窗口——因此它**默认禁用**，需 allow_screen_crop=true 显式开启，
且自带遮挡身份门控。直取拿不到时不默认建议抢焦点（那会打断用户），而是
诚实报 background_unavailable，把"退化截屏 / 前台截取"作为显式选项交还调用方。

T0 实测（Win10 19045 / GTX 750Ti）：
- PrintWindow(PW_RENDERFULLCONTENT) 对 Chrome/Notepad/Electron 出真内容；flags=0 对 GPU 窗口得白图故只用 FULL。
- WGC 单帧 ~180-280ms；本机 IsBorderRequired 不支持，draw_border 必须传 None。
- PrintWindow 与 WGC 帧尺寸均 = win32gui.GetWindowRect（含 DWM 阴影），故其像素原点是 GetWindowRect 左上，
  与 mss 裁屏用的 DWM 扩展边界（排除阴影）不同——裁剪/打码必须按各后端的真实像素原点。
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
PRINTWINDOW_TIMEOUT = 2.0   # 挂起进程的 PrintWindow 会同步阻塞，超时即降级
WGC_TIMEOUT = 7.0


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


def _wgc(title: str, timeout: float = WGC_TIMEOUT):
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


def _run_with_timeout(fn, timeout: float):
    """在守护线程跑 fn，返回 (result, timed_out)。ctypes/SendMessage 阻塞无法强杀，
    超时后线程随目标窗口恢复自然回收（daemon，不泄漏进程）。"""
    box = {}

    def worker():
        try:
            box["img"] = fn()
        except Exception as e:  # noqa: BLE001 — 透传给主线程重抛
            box["err"] = e

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None, True
    if "err" in box:
        raise box["err"]
    return box.get("img"), False


def _title_is_unique(hwnd: int, title: str) -> bool:
    """WGC 只能按 window_name=title 选窗（不认 hwnd）。标题为空或非唯一时，
    WGC 可能绑定到 OS 枚举序里第一个同名窗口而非目标——此时必须跳过 WGC。"""
    if not title:
        return False
    same = [w for w in wininfo.list_windows(include_occlusion=False)
            if w["title"] == title]
    return len(same) <= 1


def capture_window(hwnd: int, mode: str = "quiet", allow_screen_crop: bool = False,
                   restore_if_minimized: bool = False, delay_ms: int = 0):
    """获取单窗口的干净图像。默认后台直取（PrintWindow→WGC），不抢焦点、不退化截屏。

    mode: quiet(默认,后台直取) | foreground(置前再取,会抢焦点,显式选择)
    allow_screen_crop: 直取失败时允许退化为屏幕裁剪(本质是截屏,被遮挡会拍到上层窗口)
    返回 (PIL.Image|None, capture_report)。verdict ∈
      ok | blank_suspected | frozen_frame_risk | background_unavailable |
      screen_crop_occluded | minimized | failed
    """
    rpt = {"method": None, "verdict": None, "warnings": [], "attempts": []}
    if not win32gui.IsWindow(hwnd):
        rpt["verdict"] = "failed"
        rpt["warnings"].append("无效窗口句柄")
        return None, rpt

    title = win32gui.GetWindowText(hwnd)
    restored = False
    minimized = bool(win32gui.IsIconic(hwnd))
    if minimized:
        if restore_if_minimized:
            _restore_noactivate(hwnd)
            restored = True
            minimized = bool(win32gui.IsIconic(hwnd))
        if minimized:
            rpt["verdict"] = "minimized"
            rpt["silent_ok"] = True   # 没有干扰，只是静默下拿不到
            rpt["capability_boundary"] = (
                "窗口最小化无有效像素；静默约束下无法获取。授权 restore_if_minimized 可恢复"
                "（SW_SHOWNOACTIVATE：会显形但不抢焦点）。")
            rpt["warnings"].append("窗口已最小化，无有效像素。")
            rpt["next_actions"] = [{"hint": "恢复后再取（窗口显形，不抢焦点）",
                                    "params": {"restore_if_minimized": True}}]
            return None, rpt

    if delay_ms:
        time.sleep(min(delay_ms, 5000) / 1000.0)

    if mode == "foreground":
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            rpt["warnings"].append("SetForegroundWindow 失败，可能因无输入焦点")
        time.sleep(0.25)

    chromium = _is_chromium(title, hwnd)
    # PrintWindow/WGC 帧的像素原点 = GetWindowRect 左上（含阴影），裁剪/打码以此为基准
    pw_origin = tuple(win32gui.GetWindowRect(hwnd)[:2])
    last_blank = None  # 直取拿到帧但判空白：区分"内容空白"与"完全拿不到"

    # ① PrintWindow(FULL) —— 窗口内容直取，与遮挡无关，带超时防挂起
    try:
        img, timed_out = _run_with_timeout(
            lambda: _printwindow(hwnd, PW_RENDERFULLCONTENT), PRINTWINDOW_TIMEOUT)
        if timed_out:
            rpt["attempts"].append({"method": "printwindow_full",
                                    "error": "timeout(窗口 UI 线程可能挂起)"})
        else:
            status, vd = _adopt(hwnd, img, pw_origin, "printwindow_full", rpt, chromium)
            if status == "ok":
                return _finalize(img, pw_origin, rpt, vd, mode, restored)
            if status == "blank":
                last_blank = (img, pw_origin, "printwindow_full")
    except Exception as e:
        rpt["attempts"].append({"method": "printwindow_full", "error": str(e)[:160]})

    # ② WGC —— 窗口内容直取；标题非唯一/空时无法按 hwnd 定位，跳过以免截错窗
    if not _title_is_unique(hwnd, title):
        rpt["attempts"].append({"method": "wgc",
                                "skipped": "标题非唯一/为空，WGC 无法按 hwnd 定位，已跳过"})
    else:
        try:
            img, timed_out = _run_with_timeout(lambda: _wgc(title), WGC_TIMEOUT + 0.5)
            if timed_out:
                rpt["attempts"].append({"method": "wgc", "error": "timeout"})
            else:
                gwr = win32gui.GetWindowRect(hwnd)
                exp = (gwr[2] - gwr[0], gwr[3] - gwr[1])
                if abs(img.width - exp[0]) > 8 or abs(img.height - exp[1]) > 8:
                    rpt["attempts"].append(
                        {"method": "wgc",
                         "error": f"几何不符 {img.size}!={exp}，疑似截到同名错窗，丢弃"})
                else:
                    status, vd = _adopt(hwnd, img, pw_origin, "wgc", rpt, chromium)
                    if status == "ok":
                        return _finalize(img, pw_origin, rpt, vd, mode, restored)
                    if status == "blank":
                        last_blank = (img, pw_origin, "wgc")
        except Exception as e:
            rpt["attempts"].append({"method": "wgc", "error": str(e)[:160]})

    # ③ 直取拿到帧但内容空白 —— 返回该帧 + blank_suspected（区分于完全拿不到）
    if last_blank is not None:
        bimg, borigin, bmethod = last_blank
        rpt["method"] = bmethod
        rpt["warnings"].append(
            "直取拿到帧但内容疑似空白（加载中/未渲染）；delay_ms 或 wait_capture(until=stable) 后重试")
        return _finalize(bimg, borigin, rpt, "blank_suspected", mode, restored)

    # ④ 直取完全失败 —— 默认不退化截屏、不抢焦点，把选择权交还调用方
    if not allow_screen_crop:
        rpt["verdict"] = "background_unavailable"
        rpt["silent_ok"] = True   # 没干扰，但静默下拿不到
        rpt["capability_boundary"] = (
            "直取(PrintWindow/WGC)未取到有效像素（受保护内容/特殊合成）。静默约束下无更优解："
            "截屏需窗口可见、前台需抢焦点，二者都会突破静默。")
        rpt["warnings"].append(
            "窗口内容直取(PrintWindow/WGC)未取到有效像素，可能是受保护内容/特殊合成渲染。")
        rpt["next_actions"] = [
            {"hint": "退化为屏幕裁剪（注意：这是截屏，窗口被遮挡时会拍到上层窗口）",
             "params": {"allow_screen_crop": True}},
            {"hint": "置前后再取（会抢焦点、打断你当前的操作）",
             "params": {"mode": "foreground"}},
        ]
        return None, rpt

    # ④ mss 裁屏 —— 显式 opt-in 的真截屏。贴近抓拍时刻复检遮挡（避免 probe→crop 竞态）
    rect = wininfo.get_window_rect(hwnd)
    occ = wininfo.occlusion_probe(hwnd, rect)
    if occ["possibly_occluded"]:
        rpt["verdict"] = "screen_crop_occluded"
        rpt["silent_ok"] = False   # 已在截屏路径（opt-in）
        rpt["occlusion"] = occ
        rpt["capability_boundary"] = (
            "屏幕裁剪会拍到上层窗口；要拿到该窗口本身需 mode=foreground 置前（抢焦点）。")
        rpt["warnings"].append(
            f"窗口被遮挡(match_ratio={occ['match_ratio']})，屏幕裁剪会拍到上层窗口；"
            "建议改用 mode=foreground。")
        rpt["next_actions"] = [{"hint": "置前后截取（抢焦点）", "params": {"mode": "foreground"}}]
        return None, rpt
    try:
        img = _mss_crop(rect)
        a = quality.assess_window_capture(hwnd, img, rect[:2])
        rpt["attempts"].append({"method": "screen_crop", "quality": a})
        if a.get("client_crop_failed"):
            rpt["warnings"].append("客户区裁剪未生效，空白检测仅基于整图（标题栏内容可能掩盖空客户区）")
        rpt["method"] = "screen_crop"
        rpt["verdict"] = "blank_suspected" if a["likely_blank"] else "ok"
        rpt["occlusion"] = occ
        return _finalize(img, rect[:2], rpt, rpt["verdict"], mode, restored)
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


def _adopt(hwnd, img, pw_origin, method, rpt, chromium):
    """直取帧的质量评估 + 采纳判定。返回 ("ok"|"blank", verdict)。
    "blank"=拿到帧但内容疑似空白；主循环暂存，直取链都空白时返回它 + blank_suspected。"""
    a = quality.assess_window_capture(hwnd, img, pw_origin)
    rpt["attempts"].append({"method": method, "quality": a})
    if a.get("client_crop_failed"):
        rpt["warnings"].append("客户区裁剪未生效，空白检测仅基于整图（标题栏内容可能掩盖空客户区）")
    if a["likely_blank"]:
        rpt["warnings"].append(f"{method} 疑似空白/低信息，继续尝试其他后端")
        return "blank", None
    rpt["method"] = method
    return "ok", _direct_verdict(hwnd, rpt, chromium)


def _direct_verdict(hwnd, rpt, chromium: bool):
    """直取成功的 verdict。GDI/原生窗口经 PrintWindow/WGC 取实时内容，遮挡无害故 ok；
    只有 Chromium 系被遮挡时因 occlusion 节流可能是渲染冻结的旧帧——仅此情形才算 occlusion。"""
    if chromium:
        occ = wininfo.occlusion_probe(hwnd, wininfo.get_window_rect(hwnd))
        if occ["possibly_occluded"]:
            rpt["occlusion"] = occ
            rpt["capability_boundary"] = (
                "被遮挡的 Chromium 窗口直取可能是渲染冻结的旧帧；要最新画面需 mode=foreground"
                "（抢焦点，突破静默）。")
            rpt["warnings"].append(
                "Chromium 系窗口被遮挡：可能是渲染冻结的旧帧，需要最新画面请 mode=foreground")
            return "frozen_frame_risk"
    return "ok"


def _finalize(img, pixel_origin, rpt, verdict, mode="quiet", restored=False):
    rpt["verdict"] = verdict
    rpt["pixel_origin"] = list(pixel_origin)   # 图像像素(0,0)对应的屏幕坐标，裁剪/打码基准
    # 声明：此结果为达成是否突破了静默（不点不挪到表层）
    intrusion = []
    if mode == "foreground":
        intrusion.append("foreground(抢焦点)")
    if restored:
        intrusion.append("restore(窗口显形)")
    if rpt.get("method") == "screen_crop":
        intrusion.append("screen_crop(屏幕截屏)")
    rpt["intrusion_used"] = intrusion
    rpt.setdefault("silent_ok", len(intrusion) == 0)
    return img, rpt
