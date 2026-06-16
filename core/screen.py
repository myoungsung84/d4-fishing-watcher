from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

Roi = Optional[Tuple[int, int, int, int]]
DIABLO_WINDOW_TITLE_KEYWORDS = ("Diablo IV", "디아블로 IV")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    width: int
    height: int
    right: int
    bottom: int


def _get_win32gui():
    import win32gui

    return win32gui


def _get_capture_modules():
    import mss

    return mss


def _window_rect_from_handle(handle: int) -> Optional[WindowRect]:
    win32gui = _get_win32gui()
    if not handle or not win32gui.IsWindow(handle):
        return None
    if not win32gui.IsWindowVisible(handle) or win32gui.IsIconic(handle):
        return None

    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width <= 0 or height <= 0:
        return None
    return WindowRect(left=left, top=top, width=width, height=height, right=right, bottom=bottom)


def find_diablo_window_rect(
    keywords: tuple[str, ...] = DIABLO_WINDOW_TITLE_KEYWORDS,
) -> Optional[WindowRect]:
    win32gui = _get_win32gui()
    keyword_lowers = tuple(keyword.lower() for keyword in keywords)
    matches: list[WindowRect] = []

    def enum_callback(handle: int, _extra: object) -> bool:
        title = win32gui.GetWindowText(handle) or ""
        normalized_title = title.lower()
        if any(keyword in normalized_title for keyword in keyword_lowers):
            rect = _window_rect_from_handle(handle)
            if rect is not None:
                matches.append(rect)
        return True

    win32gui.EnumWindows(enum_callback, None)
    return matches[0] if matches else None


def get_diablo_window_rect() -> Optional[WindowRect]:
    return find_diablo_window_rect()


def get_active_window_title() -> str:
    win32gui = _get_win32gui()
    handle = win32gui.GetForegroundWindow()
    if not handle:
        return ""
    return win32gui.GetWindowText(handle) or ""


def get_foreground_title() -> str:
    return get_active_window_title()


def get_foreground_rect() -> Optional[Tuple[int, int, int, int]]:
    win32gui = _get_win32gui()
    handle = win32gui.GetForegroundWindow()
    if not handle:
        return None

    rect = _window_rect_from_handle(handle)
    if rect is None:
        return None
    return rect.left, rect.top, rect.width, rect.height


def get_active_window_center() -> Optional[Tuple[int, int]]:
    rect = get_foreground_rect()
    if rect is None:
        return None

    left, top, width, height = rect
    return left + (width // 2), top + (height // 2)


def is_target_window_active(keywords: list[str]) -> bool:
    title = get_foreground_title()
    normalized_title = title.lower()
    return any(keyword.lower() in normalized_title for keyword in keywords)


def local_to_screen_point(
    x: int,
    y: int,
    window_rect: Optional[WindowRect] = None,
) -> tuple[int, int]:
    rect = window_rect or get_diablo_window_rect()
    if rect is None:
        return int(x), int(y)
    return rect.left + int(x), rect.top + int(y)


def local_roi_to_screen_roi(
    roi: tuple[int, int, int, int],
    window_rect: Optional[WindowRect] = None,
) -> tuple[int, int, int, int]:
    rect = window_rect or get_diablo_window_rect()
    if rect is None:
        return roi
    left, top, width, height = roi
    return rect.left + int(left), rect.top + int(top), int(width), int(height)


def screen_roi_to_local_roi(
    roi: tuple[int, int, int, int],
    window_rect: WindowRect,
) -> tuple[int, int, int, int]:
    left, top, width, height = roi
    return int(left) - window_rect.left, int(top) - window_rect.top, int(width), int(height)


def clamp_roi_to_window(
    roi: tuple[int, int, int, int],
    window_rect_or_size: WindowRect | tuple[int, int],
) -> tuple[int, int, int, int]:
    left, top, width, height = roi
    if isinstance(window_rect_or_size, WindowRect):
        max_width = window_rect_or_size.width
        max_height = window_rect_or_size.height
    else:
        max_width, max_height = window_rect_or_size

    clamped_left = max(0, int(left))
    clamped_top = max(0, int(top))
    clamped_right = min(int(max_width), int(left) + max(1, int(width)))
    clamped_bottom = min(int(max_height), int(top) + max(1, int(height)))
    return (
        clamped_left,
        clamped_top,
        max(1, clamped_right - clamped_left),
        max(1, clamped_bottom - clamped_top),
    )


def clamp_roi(
    roi: tuple[int, int, int, int],
    window_rect_or_size: WindowRect | tuple[int, int],
) -> tuple[int, int, int, int]:
    return clamp_roi_to_window(roi, window_rect_or_size)


def capture_window(
    rect: WindowRect,
    roi: Roi = None,
) -> tuple[np.ndarray, CaptureRegion]:
    mss = _get_capture_modules()
    if roi is None:
        local_roi = (0, 0, rect.width, rect.height)
    else:
        local_roi = clamp_roi_to_window(roi, rect)

    screen_roi = local_roi_to_screen_roi(local_roi, rect)
    region = {
        "left": int(screen_roi[0]),
        "top": int(screen_roi[1]),
        "width": int(screen_roi[2]),
        "height": int(screen_roi[3]),
    }

    with mss.mss() as sct:
        grabbed = sct.grab(region)
        frame: np.ndarray = np.array(grabbed)[:, :, :3]
        capture_region = CaptureRegion(
            left=int(local_roi[0]),
            top=int(local_roi[1]),
            width=region["width"],
            height=region["height"],
        )
        return frame, capture_region


def capture_diablo_window(roi: Roi = None) -> tuple[np.ndarray, CaptureRegion]:
    rect = get_diablo_window_rect()
    if rect is None:
        return capture_screen_fallback(roi)
    return capture_window(rect, roi)


def capture_screen_fallback(roi: Roi = None) -> tuple[np.ndarray, CaptureRegion]:
    mss = _get_capture_modules()
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        if roi is None:
            region = {
                "left": monitor["left"],
                "top": monitor["top"],
                "width": monitor["width"],
                "height": monitor["height"],
            }
        else:
            region = {
                "left": int(roi[0]),
                "top": int(roi[1]),
                "width": int(roi[2]),
                "height": int(roi[3]),
            }

        grabbed = sct.grab(region)
        frame: np.ndarray = np.array(grabbed)[:, :, :3]
        capture_region = CaptureRegion(
            left=region["left"],
            top=region["top"],
            width=region["width"],
            height=region["height"],
        )
        return frame, capture_region


def capture_screen(roi: Roi = None) -> tuple[np.ndarray, CaptureRegion]:
    """Capture Diablo IV as a local-coordinate frame, with full-screen fallback."""
    rect = get_diablo_window_rect()
    if rect is None:
        LOGGER.warning("[screen] Diablo IV window not found; falling back to full virtual screen capture")
        return capture_screen_fallback(roi)
    return capture_window(rect, roi)
