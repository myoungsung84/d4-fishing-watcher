from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.paths import get_data_dir

LOGGER = logging.getLogger(__name__)
SETTINGS_PATH = get_data_dir() / "settings.json"

FUNCTION_KEYS = {f"F{index}" for index in range(1, 13)}
LETTERS = {chr(code) for code in range(ord("A"), ord("Z") + 1)}
DIGITS = {str(index) for index in range(10)}
NUMPAD_DIGITS = {f"Num{index}" for index in range(10)}
NAMED_KEYS = {"Insert", "Home", "End", "PageUp", "PageDown"}
ALLOWED_HOTKEYS = FUNCTION_KEYS | LETTERS | DIGITS | NUMPAD_DIGITS | NAMED_KEYS
RESERVED_HOTKEYS = {"Escape"}

KEY_ALIASES = {
    "ESC": "Escape",
    "ESCAPE": "Escape",
    "PGUP": "PageUp",
    "PAGEUP": "PageUp",
    "PAGE_UP": "PageUp",
    "PRIOR": "PageUp",
    "PGDN": "PageDown",
    "PAGEDOWN": "PageDown",
    "PAGE_DOWN": "PageDown",
    "NEXT": "PageDown",
    "INS": "Insert",
    "INSERT": "Insert",
    "HOME": "Home",
    "END": "End",
}

PYAUTOGUI_KEY_NAMES = {
    "PageUp": "pageup",
    "PageDown": "pagedown",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    **{f"F{index}": f"f{index}" for index in range(1, 13)},
    **{f"Num{index}": f"num{index}" for index in range(10)},
}


@dataclass(frozen=True)
class HotkeySettings:
    start_fishing: str = "PageUp"
    stop_fishing: str = "PageDown"


@dataclass(frozen=True)
class GameKeySettings:
    social_menu: str = "3"
    interact_pickup: str = "R"
    reel: str = "E"


@dataclass(frozen=True)
class AppSettings:
    hotkeys: HotkeySettings
    game_keys: GameKeySettings


DEFAULT_HOTKEY_SETTINGS = HotkeySettings()
DEFAULT_GAME_KEY_SETTINGS = GameKeySettings()
DEFAULT_SETTINGS = AppSettings(hotkeys=DEFAULT_HOTKEY_SETTINGS, game_keys=DEFAULT_GAME_KEY_SETTINGS)


def normalize_hotkey(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip().replace(" ", "").replace("-", "_")
    if not normalized:
        return None

    upper = normalized.upper()
    if upper in KEY_ALIASES:
        return KEY_ALIASES[upper]

    if upper.startswith("NUM") and len(upper) == 4 and upper[-1].isdigit():
        return f"Num{upper[-1]}"

    if upper.startswith("KP_") and len(upper) == 4 and upper[-1].isdigit():
        return f"Num{upper[-1]}"

    if upper.startswith("F") and upper[1:].isdigit():
        key = f"F{int(upper[1:])}"
        return key if key in FUNCTION_KEYS else None

    if len(upper) == 1 and (upper in LETTERS or upper in DIGITS):
        return upper

    return None


def normalize_game_key(value: object) -> str | None:
    normalized = normalize_hotkey(value)
    if normalized is None:
        return None
    if normalized in RESERVED_HOTKEYS:
        return None
    return normalized


def is_allowed_hotkey(value: object) -> bool:
    normalized = normalize_hotkey(value)
    return normalized in ALLOWED_HOTKEYS


def validate_hotkey_pair(start_fishing: object, stop_fishing: object) -> tuple[HotkeySettings | None, str | None]:
    if _is_esc_key(start_fishing) or _is_esc_key(stop_fishing):
        return None, "ESC는 단축키로 사용할 수 없습니다."

    start = normalize_hotkey(start_fishing)
    stop = normalize_hotkey(stop_fishing)
    if start is None or stop is None:
        return None, "단축키 값이 비어 있거나 지원하지 않는 키입니다."
    if start not in ALLOWED_HOTKEYS or stop not in ALLOWED_HOTKEYS:
        return None, "지원하지 않는 키입니다."
    if start == stop:
        return None, "시작과 중지 단축키는 서로 달라야 합니다."
    return HotkeySettings(start_fishing=start, stop_fishing=stop), None


def validate_game_keys(
    social_menu: object,
    interact_pickup: object,
    reel: object,
) -> tuple[GameKeySettings | None, str | None]:
    normalized_social = normalize_game_key(social_menu)
    normalized_interact = normalize_game_key(interact_pickup)
    normalized_reel = normalize_game_key(reel)
    if normalized_social is None or normalized_interact is None or normalized_reel is None:
        return None, "게임 조작키 값이 비어 있거나 지원하지 않는 키입니다."
    return (
        GameKeySettings(
            social_menu=normalized_social,
            interact_pickup=normalized_interact,
            reel=normalized_reel,
        ),
        None,
    )


def pyautogui_key_name(value: object) -> str | None:
    normalized = normalize_game_key(value)
    if normalized is None:
        return None
    if normalized in PYAUTOGUI_KEY_NAMES:
        return PYAUTOGUI_KEY_NAMES[normalized]
    return normalized.lower()


def _is_esc_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().replace(" ", "").replace("_", "").replace("-", "").upper() in {
        "ESC",
        "ESCAPE",
    }


def load_settings(path: Path = SETTINGS_PATH) -> AppSettings:
    if not path.exists():
        LOGGER.info("Settings file not found; using defaults")
        return DEFAULT_SETTINGS

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.warning("Settings file is invalid; using defaults", exc_info=True)
        return DEFAULT_SETTINGS

    try:
        hotkeys_payload = payload.get("hotkeys", {}) if isinstance(payload, dict) else {}
        hotkeys, error = validate_hotkey_pair(
            hotkeys_payload.get("start_fishing", DEFAULT_SETTINGS.hotkeys.start_fishing),
            hotkeys_payload.get("stop_fishing", DEFAULT_SETTINGS.hotkeys.stop_fishing),
        )
        if hotkeys is None:
            LOGGER.warning("Invalid hotkey settings in %s: %s", path, error)
            hotkeys = DEFAULT_SETTINGS.hotkeys

        game_keys_payload = payload.get("game_keys", {}) if isinstance(payload, dict) else {}
        game_keys, error = validate_game_keys(
            game_keys_payload.get("social_menu", DEFAULT_SETTINGS.game_keys.social_menu),
            game_keys_payload.get("interact_pickup", DEFAULT_SETTINGS.game_keys.interact_pickup),
            game_keys_payload.get("reel", DEFAULT_SETTINGS.game_keys.reel),
        )
        if game_keys is None:
            LOGGER.warning("Invalid game key settings in %s: %s", path, error)
            game_keys = DEFAULT_SETTINGS.game_keys
        return AppSettings(hotkeys=hotkeys, game_keys=game_keys)
    except Exception:
        LOGGER.warning("Failed to load settings; using defaults", exc_info=True)
        return DEFAULT_SETTINGS


def save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> bool:
    payload = {
        "hotkeys": {
            "start_fishing": settings.hotkeys.start_fishing,
            "stop_fishing": settings.hotkeys.stop_fishing,
        },
        "game_keys": {
            "social_menu": settings.game_keys.social_menu,
            "interact_pickup": settings.game_keys.interact_pickup,
            "reel": settings.game_keys.reel,
        },
    }
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
        LOGGER.info("Settings saved to %s", path)
        return True
    except Exception:
        LOGGER.exception("Failed to save settings to %s", path)
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            LOGGER.debug("Failed to remove temporary settings file", exc_info=True)
        return False
