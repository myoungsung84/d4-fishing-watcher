from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk

LOGGER = logging.getLogger(__name__)


def apply_windows_window_polish(window: tk.Misc) -> None:
    if sys.platform != "win32":
        return
    _apply_dark_title_bar(window)


def set_windows_app_user_model_id(app_id: str) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        LOGGER.debug("Failed to set AppUserModelID", exc_info=True)


def _apply_dark_title_bar(window: tk.Misc) -> None:
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                return
    except Exception:
        LOGGER.debug("Failed to apply dark title bar", exc_info=True)
