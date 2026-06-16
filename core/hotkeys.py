from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.settings import HotkeySettings, normalize_hotkey, validate_hotkey_pair

LOGGER = logging.getLogger(__name__)

HotkeyCallback = Callable[[], None]


@dataclass(frozen=True)
class HotkeyRegistration:
    start_fishing: str
    stop_fishing: str


class GlobalHotkeyManager:
    def __init__(
        self,
        *,
        on_start: HotkeyCallback,
        on_stop: HotkeyCallback,
    ) -> None:
        self._on_start = on_start
        self._on_stop = on_stop
        self._listener = None
        self._lock = threading.Lock()
        self._registration: Optional[HotkeyRegistration] = None
        self._modifiers_pressed: set[str] = set()
        self._last_triggered_at: dict[str, float] = {}
        self._debounce_seconds = 0.35

    @property
    def registration(self) -> Optional[HotkeyRegistration]:
        with self._lock:
            return self._registration

    def start(self, settings: HotkeySettings) -> None:
        self.register(settings)

    def register(self, settings: HotkeySettings) -> None:
        hotkeys, error = validate_hotkey_pair(settings.start_fishing, settings.stop_fishing)
        if hotkeys is None:
            raise ValueError(error or "Invalid hotkey settings")

        keyboard = self._get_keyboard_module()
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.daemon = True
        listener.start()

        with self._lock:
            old_listener = self._listener
            self._listener = listener
            self._registration = HotkeyRegistration(
                start_fishing=hotkeys.start_fishing,
                stop_fishing=hotkeys.stop_fishing,
            )
            self._modifiers_pressed.clear()
            self._last_triggered_at.clear()
            if old_listener is not None:
                old_listener.stop()
            LOGGER.info(
                "Global hotkey listener started: start=%s stop=%s",
                hotkeys.start_fishing,
                hotkeys.stop_fishing,
            )

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        listener = self._listener
        self._listener = None
        self._registration = None
        self._modifiers_pressed.clear()
        self._last_triggered_at.clear()
        if listener is not None:
            listener.stop()
            LOGGER.info("Global hotkey listener stopped")

    def _on_press(self, key: object) -> None:
        key_name = normalize_pynput_key(key)
        LOGGER.debug("Raw global hotkey press: %r normalized=%s", key, key_name)
        modifier = normalize_modifier_key(key)
        if modifier is not None:
            self._modifiers_pressed.add(modifier)
            return

        if key_name is None:
            return

        with self._lock:
            registration = self._registration
            has_modifier = bool(self._modifiers_pressed)

        if registration is None or has_modifier:
            return

        if key_name == registration.start_fishing:
            self._trigger("start", self._on_start)
        elif key_name == registration.stop_fishing:
            self._trigger("stop", self._on_stop)

    def _on_release(self, key: object) -> None:
        modifier = normalize_modifier_key(key)
        if modifier is not None:
            self._modifiers_pressed.discard(modifier)

    def _trigger(self, action: str, callback: HotkeyCallback) -> None:
        now = time.monotonic()
        previous = self._last_triggered_at.get(action, 0.0)
        if now - previous < self._debounce_seconds:
            LOGGER.debug("Repeated global hotkey ignored: %s", action)
            return
        self._last_triggered_at[action] = now
        LOGGER.info("Global hotkey triggered: %s", action)
        callback()

    @staticmethod
    def _get_keyboard_module():
        from pynput import keyboard

        return keyboard


def normalize_pynput_key(key: object) -> str | None:
    vk = getattr(key, "vk", None)
    if isinstance(vk, int) and 96 <= vk <= 105:
        return f"Num{vk - 96}"

    char = getattr(key, "char", None)
    if isinstance(char, str) and len(char) == 1:
        return normalize_hotkey(char)

    name = getattr(key, "name", None)
    if isinstance(name, str):
        return normalize_hotkey(name)

    text = str(key)
    if text.startswith("Key."):
        return normalize_hotkey(text[4:])
    return normalize_hotkey(text)


def normalize_tk_key(keysym: str, keycode: int | None = None, state: int = 0) -> tuple[str | None, str | None]:
    if state & 0x0004 or state & 0x0008 or state & 0x0001:
        return None, "조합키는 지원하지 않습니다."

    if keycode is not None and 96 <= keycode <= 105:
        return f"Num{keycode - 96}", None

    if keysym == "Escape":
        return None, "ESC는 단축키로 사용할 수 없습니다."

    if keysym in {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"}:
        return None, "Ctrl, Alt, Shift 단독키는 사용할 수 없습니다."

    normalized = normalize_hotkey(keysym)
    if normalized is None:
        return None, "지원하지 않는 키입니다."
    return normalized, None


def normalize_modifier_key(key: object) -> str | None:
    name = getattr(key, "name", None)
    if not isinstance(name, str):
        text = str(key)
        name = text[4:] if text.startswith("Key.") else text
    lowered = name.lower()
    if lowered.startswith("ctrl"):
        return "ctrl"
    if lowered.startswith("alt"):
        return "alt"
    if lowered.startswith("shift"):
        return "shift"
    return None
