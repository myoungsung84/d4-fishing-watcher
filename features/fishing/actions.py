from __future__ import annotations

from types import ModuleType

_pyautogui: ModuleType | None = None


def _get_pyautogui() -> ModuleType:
    global _pyautogui
    if _pyautogui is None:
        import pyautogui

        pyautogui.FAILSAFE = False
        _pyautogui = pyautogui
    return _pyautogui


def press_key(key: str, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] press key: {key}")
        return
    _get_pyautogui().press(key)


def move_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] move to: ({x}, {y}), move_duration={move_duration}")
        return
    _get_pyautogui().moveTo(x, y, duration=move_duration)


def click_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] click at: ({x}, {y}), move_duration={move_duration}")
        return
    move_point(x, y, move_duration=move_duration, dry_run=False)
    _get_pyautogui().click()

