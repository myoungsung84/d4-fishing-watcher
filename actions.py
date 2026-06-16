from __future__ import annotations

import pyautogui

pyautogui.FAILSAFE = False


def press_key(key: str, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] press key: {key}")
        return
    pyautogui.press(key)


def move_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] move to: ({x}, {y}), move_duration={move_duration}")
        return
    pyautogui.moveTo(x, y, duration=move_duration)


def click_point(x: int, y: int, move_duration: float = 0.0, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY RUN] click at: ({x}, {y}), move_duration={move_duration}")
        return
    move_point(x, y, move_duration=move_duration, dry_run=False)
    pyautogui.click()

