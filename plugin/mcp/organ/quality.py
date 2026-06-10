# -*- coding: utf-8 -*-
"""黑帧/低信息帧检测。四指标阈值移植自 screen-guardian（实测打磨过），
加本项目 T0 实测发现的关键改进：窗口图必须裁出客户区单独检测
（白图黑边框会把 contrast 拉到 35+，骗过整图指标——T0 实证的假阴性）。"""
import ctypes

import numpy as np
import win32gui
from PIL import Image, ImageFilter


def blank_metrics(img: Image.Image) -> dict:
    g = img.convert("L")
    g.thumbnail((384, 384))
    a = np.asarray(g, dtype=np.float64)
    brightness = float(a.mean())
    contrast = float(a.std())
    edges = float(np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=np.float64).mean())
    hist, _ = np.histogram(a, bins=256, range=(0, 255))
    p = hist / max(hist.sum(), 1)
    p = p[p > 0]
    entropy = float(-(p * np.log2(p)).sum() / 8.0)
    likely_blank = (
        (brightness <= 3 and contrast <= 1 and entropy <= 0.1)        # uniform_dark
        or (brightness >= 252 and contrast <= 1 and entropy <= 0.1)   # uniform_light
        or (contrast <= 3 and edges <= 1.2 and entropy <= 0.45)       # low_information
        or (brightness >= 252 and contrast <= 6 and entropy <= 0.2)   # bright_low_info
    )
    return {
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
        "edges": round(edges, 3),
        "entropy": round(entropy, 3),
        "likely_blank": bool(likely_blank),
    }


def client_area_crop(hwnd: int, window_img: Image.Image, window_rect: tuple):
    """从窗口截图中裁出客户区。window_rect 是 GetWindowRect 的 (l,t,r,b)。"""
    try:
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        sx, sy = win32gui.ClientToScreen(hwnd, (cl, ct))
        wl, wt = window_rect[0], window_rect[1]
        x0, y0 = sx - wl, sy - wt
        x1, y1 = x0 + (cr - cl), y0 + (cb - ct)
        if x1 - x0 < 32 or y1 - y0 < 32:
            return None
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(window_img.width, x1); y1 = min(window_img.height, y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return window_img.crop((x0, y0, x1, y1))
    except Exception:
        return None


def assess_window_capture(hwnd: int, img: Image.Image, window_rect: tuple) -> dict:
    """整图 + 客户区双重检测。任何一层 likely_blank 即判可疑。"""
    whole = blank_metrics(img)
    client = None
    client_img = client_area_crop(hwnd, img, window_rect)
    if client_img is not None:
        client = blank_metrics(client_img)
    suspicious = whole["likely_blank"] or (client is not None and client["likely_blank"])
    return {"whole": whole, "client": client, "likely_blank": bool(suspicious)}
