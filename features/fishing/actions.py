from __future__ import annotations

import logging
from types import ModuleType

_pyautogui: ModuleType | None = None
LOGGER = logging.getLogger(__name__)


def _get_pyautogui() -> ModuleType:
    global _pyautogui
    if _pyautogui is None:
        import pyautogui

        pyautogui.FAILSAFE = False
        _pyautogui = pyautogui
    return _pyautogui


def press_key(key: str, dry_run: bool = False) -> None:
    if dry_run:
        LOGGER.info("[DRY RUN] press key: %s", key)
        return
    _get_pyautogui().press(key)


def move_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        LOGGER.info("[DRY RUN] move to: (%s, %s), move_duration=%s", x, y, move_duration)
        return
    _get_pyautogui().moveTo(x, y, duration=move_duration)


def click_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        LOGGER.info("[DRY RUN] click at: (%s, %s), move_duration=%s", x, y, move_duration)
        return
    move_point(x, y, move_duration=move_duration, dry_run=False)
    _get_pyautogui().click()

