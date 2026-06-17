from __future__ import annotations

import ctypes
import logging
import os

LOGGER = logging.getLogger(__name__)

_FONT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "fonts"))
_PRETENDARD_FILES = ("Pretendard-Regular.ttf", "Pretendard-SemiBold.ttf", "Pretendard-Bold.ttf")
_FAMILY = "Pretendard"
_FALLBACK = "Segoe UI"


def _load_private(path: str) -> bool:
    try:
        FR_PRIVATE = 0x10
        result: int = ctypes.windll.gdi32.AddFontResourceExW(path, FR_PRIVATE, 0)  # type: ignore[attr-defined]
        return result > 0
    except Exception:
        return False


def load_pretendard() -> str:
    """Load Pretendard font files for this process and return the family name.

    Returns 'Pretendard' on success, 'Segoe UI' if files are missing or GDI fails.
    """
    loaded = 0
    for fname in _PRETENDARD_FILES:
        path = os.path.join(_FONT_DIR, fname)
        if os.path.exists(path):
            if _load_private(path):
                loaded += 1
            else:
                LOGGER.warning("[FONT] GDI load failed: %s", fname)
        else:
            LOGGER.warning("[FONT] File not found: %s", path)

    if loaded > 0:
        LOGGER.info("[FONT] Pretendard loaded (%d/%d)", loaded, len(_PRETENDARD_FILES))
        return _FAMILY

    LOGGER.warning("[FONT] Pretendard unavailable, using fallback")
    return _FALLBACK
