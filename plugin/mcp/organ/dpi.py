# -*- coding: utf-8 -*-
"""DPI 感知设置。必须在 import 任何截图库之前调用（进程级只能设一次，先到先得）。"""
import ctypes

_done = None


def ensure_dpi_awareness():
    global _done
    if _done is not None:
        return _done
    try:
        # PerMonitorV2 (Win10 1703+)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _done = "per_monitor_v2"
            return _done
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        _done = "per_monitor"
        return _done
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        _done = "system"
    except Exception:
        _done = "unaware"
    return _done
