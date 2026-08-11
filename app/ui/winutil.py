from __future__ import annotations

import ctypes
import sys


def apply_titlebar_theme(widget, dark: bool) -> None:
    """Windows 11/10 다크 타이틀바 적용. 비-Windows 플랫폼에서는 아무 것도 하지 않는다."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win10 20H1+)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass
