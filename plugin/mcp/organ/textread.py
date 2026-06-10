# -*- coding: utf-8 -*-
"""文本提取：WinRT OCR + UIA。

T0 实测结论：
- WinRT OCR 对原生分辨率截图中文识别率偏低（"截图插件"被识别为"截条适件"），
  放大 2x 后中文准确率明显提升（4/5 已知串命中）→ OCR 前默认放大小图。
- UIA GetValuePattern/GetTextPattern 读 Notepad 编辑区文本可靠、含已知串。
- UIA IsPassword 对 WinForms 密码框可靠返回 True（值读出为空，天然脱敏）；
  对 tkinter show='*' 不上报为密码框（无独立 EditControl），属已知局限。
"""
import time

from PIL import Image

from . import privacy


def ocr_image(img: Image.Image, lang: str = "zh-CN", upscale_min_height: int = 1200):
    import winocr
    work = img
    scale = 1.0
    if img.height < upscale_min_height:
        scale = min(3.0, max(2.0, upscale_min_height / max(img.height, 1)))
        work = img.resize((int(img.width * scale), int(img.height * scale)),
                          Image.LANCZOS)
    t0 = time.perf_counter()
    res = winocr.recognize_pil_sync(work, lang)
    ms = round((time.perf_counter() - t0) * 1000)
    lines = [ln["text"] for ln in res.get("lines", [])] if isinstance(res, dict) else []
    text = res["text"] if isinstance(res, dict) else getattr(res, "text", "")
    return {"engine": "winrt_ocr", "lang": lang, "upscale": round(scale, 2),
            "ms": ms, "text": text, "lines": lines}


def uia_text(hwnd: int, max_chars: int = 20000):
    """读窗口控件树文本，剥离 IsPassword 节点内容。"""
    import uiautomation as auto
    pieces = []
    password_nodes = 0
    node_count = 0
    with auto.UIAutomationInitializerInThread():
        win = auto.ControlFromHandle(hwnd)
        if not win:
            return {"engine": "uia", "error": "无法从句柄获取 UIA 元素"}
        # 先试编辑区整体取值（Notepad 等单文档场景最准）
        try:
            edit = win.EditControl(searchDepth=8)
            if edit.Exists(0, 0):
                if bool(edit.GetPropertyValue(auto.PropertyId.IsPasswordProperty)):
                    password_nodes += 1
                else:
                    try:
                        v = edit.GetValuePattern().Value
                    except Exception:
                        v = edit.GetTextPattern().DocumentRange.GetText(max_chars)
                    if v:
                        pieces.append(v)
        except Exception:
            pass
        # 再补名字/文本类控件
        for c, _d in auto.WalkControl(win, maxDepth=10):
            node_count += 1
            if node_count > 3000:
                break
            try:
                if c.ControlTypeName == "EditControl" and \
                        bool(c.GetPropertyValue(auto.PropertyId.IsPasswordProperty)):
                    password_nodes += 1
                    continue
                name = c.Name
                if name and c.ControlTypeName in (
                        "TextControl", "ButtonControl", "HyperlinkControl",
                        "MenuItemControl", "TabItemControl", "ListItemControl"):
                    pieces.append(name)
            except Exception:
                continue
    joined = "\n".join(dict.fromkeys(p.strip() for p in pieces if p and p.strip()))
    return {"engine": "uia", "node_count": node_count,
            "password_nodes_stripped": password_nodes,
            "text": joined[:max_chars]}


def read_text(hwnd: int, img: Image.Image, engine: str = "auto", lang: str = "zh-CN"):
    """engine: auto|ocr|uia。auto = 先试 UIA，文本太少再用 OCR 兜底。"""
    result = {"requested_engine": engine}
    if engine in ("auto", "uia"):
        try:
            u = uia_text(hwnd)
            result["uia"] = u
            if engine == "uia" or len((u.get("text") or "")) >= 20:
                result["text"] = privacy.wrap_untrusted(u.get("text", ""))
                result["used"] = "uia"
                result["password_nodes_stripped"] = u.get("password_nodes_stripped", 0)
                return result
        except Exception as e:
            result["uia"] = {"error": repr(e)[:200]}
    if engine in ("auto", "ocr") and img is not None:
        try:
            o = ocr_image(img, lang)
            result["ocr"] = {k: o[k] for k in ("ms", "upscale", "lang")}
            result["text"] = privacy.wrap_untrusted(o.get("text", ""))
            result["used"] = "ocr"
            return result
        except Exception as e:
            result["ocr"] = {"error": repr(e)[:200]}
    result.setdefault("text", privacy.wrap_untrusted(""))
    result.setdefault("used", "none")
    return result
