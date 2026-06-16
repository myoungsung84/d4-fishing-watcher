from __future__ import annotations

import shutil
import sqlite3
import threading
import time
import math
import random
import base64
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Protocol

import cv2
import numpy as np

from app import config

from features.fishing.actions import click_point, move_point, press_key
from features.fishing.detector import (
    DetectionResult,
    ReadyColorBlob,
    TemplateImage,
    detect_ready_color_blobs,
    find_best_match,
    find_best_match_multi_scale,
    load_template,
)
from core.screen import (
    CaptureRegion,
    WindowRect,
    capture_screen,
    clamp_roi_to_window,
    find_diablo_window_rect,
    get_active_window_title,
    get_foreground_rect,
    is_target_window_active,
    local_to_screen_point,
    screen_roi_to_local_roi,
)
from ui.overlay import OverlayController, OverlaySnapshot

USER_LOG_MAX_LINES = 14


def _get_keyboard_module():
    from pynput import keyboard

    return keyboard


class LogColor:
    RESET = "\033[0m"
    GRAY = "\033[90m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"


def log_info(message: str) -> None:
    print(f"{LogColor.CYAN}{message}{LogColor.RESET}")


def log_success(message: str) -> None:
    print(f"{LogColor.GREEN}{message}{LogColor.RESET}")


def log_warn(message: str) -> None:
    print(f"{LogColor.YELLOW}{message}{LogColor.RESET}")


def log_error(message: str) -> None:
    print(f"{LogColor.RED}{message}{LogColor.RESET}")


def log_dim(message: str) -> None:
    print(f"{LogColor.GRAY}{message}{LogColor.RESET}")


@dataclass
class RuntimeState:
    running: bool = False
    stop_requested: bool = False
    idle_announced: bool = False


class FishingCycleResult(Enum):
    SUCCESS = auto()
    TIMEOUT = auto()
    CANCELLED = auto()
    FAILED = auto()
    RETRY = auto()


@dataclass
class FishingSession:
    start_template: TemplateImage
    ready_template: TemplateImage
    default_ready_roi_local: Optional[tuple[int, int, int, int]]


class HotkeyListener(Protocol):
    daemon: bool

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


state = RuntimeState()


@dataclass
class FishingSessionStats:
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    cast_count: int = 0
    catch_count: int = 0
    is_running: bool = False


@dataclass
class FishingTotalStats:
    total_cast_count: int = 0
    total_catch_count: int = 0
    total_run_seconds: int = 0
    sessions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class UserLogEntry:
    time: str
    type: str
    message: str

    def render(self) -> str:
        return f"{self.time} {self.message}"


class UserLogBuffer:
    def __init__(self, max_lines: int = 10) -> None:
        self._lines: Deque[UserLogEntry] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def add(self, message: str, log_type: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._lines.append(
                UserLogEntry(
                    time=timestamp,
                    type=log_type,
                    message=message,
                )
            )

    def get_lines(self) -> List[UserLogEntry]:
        with self._lock:
            return list(self._lines)


session_stats = FishingSessionStats()
total_stats = FishingTotalStats()
session_stats_lock = threading.Lock()
user_logs = UserLogBuffer(max_lines=USER_LOG_MAX_LINES)
overlay_controller: Optional[OverlayController] = None
hotkey_listener: Optional[HotkeyListener] = None
STATS_DB_PATH = config.resolve_path("data/fishing_stats.db")
today_cast_count = 0
today_catch_count = 0
today_run_seconds = 0
today_key = date.today().isoformat()
run_started_at: Optional[float] = None
last_ready_icon_pos: Optional[tuple[int, int]] = None
last_start_icon_pos: Optional[tuple[int, int]] = None
last_ready_miss_saved_at: float = 0.0
ready_debug_snapshot: Optional[Dict[str, Any]] = None
last_ready_debug_overlay_update_at: float = 0.0
current_fishing_search_roi: Optional[tuple[int, int, int, int]] = None
roi_selection_in_progress = False
roi_selection_cancel_requested = False
roi_state_lock = threading.Lock()
current_window_rect: Optional[WindowRect] = None
window_rect_lock = threading.Lock()
start_hotkey_enabled = True
clear_roi_on_stop = True


def refresh_diablo_window_rect(log_missing: bool = True) -> Optional[WindowRect]:
    rect = find_diablo_window_rect(tuple(config.CONFIG.window_keywords))
    with window_rect_lock:
        global current_window_rect
        current_window_rect = rect

    if rect is None and log_missing:
        log_warn("[WINDOW] Diablo IV 창을 찾지 못해 전체 화면 fallback 캡처를 사용합니다")
    elif rect is not None and log_missing:
        log_dim(
            f"[WINDOW] rect screen=({rect.left},{rect.top},{rect.width}x{rect.height})"
        )
    return rect


def get_current_window_rect() -> Optional[WindowRect]:
    with window_rect_lock:
        return current_window_rect


def get_local_window_bounds() -> Optional[tuple[int, int, int, int]]:
    rect = get_current_window_rect() or refresh_diablo_window_rect(log_missing=False)
    if rect is None:
        return None
    return 0, 0, rect.width, rect.height


def get_local_window_size() -> tuple[int, int]:
    rect = get_current_window_rect() or refresh_diablo_window_rect(log_missing=False)
    if rect is not None:
        return rect.width, rect.height
    return config.CONFIG.fallback_base_width, config.CONFIG.fallback_base_height


def local_point_to_screen(point_local: tuple[int, int]) -> tuple[int, int]:
    return local_to_screen_point(point_local[0], point_local[1], get_current_window_rect())


def get_default_ready_roi_local() -> Optional[tuple[int, int, int, int]]:
    if config.CONFIG.ready_roi is not None:
        return clamp_roi_to_window(config.CONFIG.ready_roi, get_local_window_size())

    width, height = get_local_window_size()
    left_ratio, top_ratio, width_ratio, height_ratio = config.CONFIG.ready_roi_ratio
    roi = (
        int(width * left_ratio),
        int(height * top_ratio),
        int(width * width_ratio),
        int(height * height_ratio),
    )
    return clamp_roi_to_window(roi, (width, height))


def get_current_fishing_search_roi() -> Optional[tuple[int, int, int, int]]:
    with roi_state_lock:
        return current_fishing_search_roi


def set_current_fishing_search_roi(roi: Optional[tuple[int, int, int, int]]) -> None:
    global current_fishing_search_roi
    with roi_state_lock:
        current_fishing_search_roi = roi


def clear_current_fishing_search_roi() -> None:
    set_current_fishing_search_roi(None)


def has_current_fishing_search_roi() -> bool:
    return get_current_fishing_search_roi() is not None


def set_roi_selection_in_progress(value: bool) -> None:
    global roi_selection_in_progress
    with roi_state_lock:
        roi_selection_in_progress = value


def is_roi_selection_in_progress() -> bool:
    with roi_state_lock:
        return roi_selection_in_progress


def request_roi_selection_cancel(value: bool = True) -> None:
    global roi_selection_cancel_requested
    with roi_state_lock:
        roi_selection_cancel_requested = value


def is_roi_selection_cancel_requested() -> bool:
    with roi_state_lock:
        return roi_selection_cancel_requested


def add_user_log(message: str, log_type: str = "info") -> None:
    user_logs.add(message, log_type)

    if overlay_controller is not None:
        overlay_controller.update_snapshot(build_overlay_snapshot())


def encode_ready_debug_image(crop_bgr: Optional[np.ndarray]) -> Optional[str]:
    if crop_bgr is None or crop_bgr.size == 0:
        return None

    max_width = 220
    max_height = 120
    height, width = crop_bgr.shape[:2]
    scale = min(max_width / max(1, width), max_height / max(1, height), 1.0)
    if scale < 1.0:
        crop_bgr = cv2.resize(
            crop_bgr,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    ok, encoded = cv2.imencode(".png", crop_bgr)
    if not ok:
        return None
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def update_ready_debug_snapshot(
    *,
    status: str,
    roi_type: str,
    scale: float,
    score: float,
    threshold: float,
    pos: Optional[tuple[int, int]],
    skip_reason: Optional[str] = None,
    roi: Optional[tuple[int, int, int, int]] = None,
    search_roi: Optional[tuple[int, int, int, int]] = None,
    active_bobber_roi: Optional[tuple[int, int, int, int]] = None,
    crop_bgr: Optional[np.ndarray] = None,
    elapsed_text: Optional[str] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
    ready_color: Optional[Dict[str, Any]] = None,
) -> None:
    global ready_debug_snapshot, last_ready_debug_overlay_update_at
    now = time.monotonic()
    image_updated = (
        crop_bgr is not None
        and now - last_ready_debug_overlay_update_at >= config.CONFIG.ready_debug_overlay_interval
    )
    image_data = None
    if image_updated:
        image_data = encode_ready_debug_image(crop_bgr)
        last_ready_debug_overlay_update_at = now

    ready_debug_snapshot = {
        "enabled": True,
        "status": status,
        "roi_type": roi_type,
        "scale": scale,
        "score": score,
        "threshold": threshold,
        "pos": pos,
        "skip_reason": skip_reason,
        "roi": roi,
        "search_roi": search_roi,
        "active_bobber_roi": active_bobber_roi,
        "scan_time": datetime.now().strftime("%H:%M:%S"),
        "elapsed": elapsed_text,
        "candidates": candidates or [],
        "ready_color": ready_color or {},
        "image": image_data,
        "image_updated": image_updated,
    }

    if overlay_controller is not None and state.running:
        overlay_controller.update_snapshot(build_overlay_snapshot())


def clear_ready_debug_snapshot() -> None:
    global ready_debug_snapshot
    ready_debug_snapshot = None
    if overlay_controller is not None:
        overlay_controller.update_snapshot(build_overlay_snapshot())


def set_fishing_search_roi_from_screen(
    roi: tuple[int, int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    rect = refresh_diablo_window_rect(log_missing=True)
    local_roi = roi
    if rect is not None:
        local_roi = clamp_roi_to_window(screen_roi_to_local_roi(roi, rect), rect)
    else:
        log_warn("[fishing] Diablo IV 창을 찾지 못해 선택 ROI를 screen 좌표 fallback으로 저장합니다")

    x, y, width, height = local_roi
    if width < config.CONFIG.fishing_roi_min_width or height < config.CONFIG.fishing_roi_min_height:
        log_warn("[fishing] selected ROI too small, ignored")
        add_user_log("탐색 영역이 너무 작음", "warning")
        return None

    set_current_fishing_search_roi(local_roi)
    log_success(f"[fishing] current search ROI selected local x={x} y={y} w={width} h={height}")
    add_user_log("탐색 영역 지정 완료", "start")
    return local_roi


def start_fishing_after_roi_selection(roi: Optional[tuple[int, int, int, int]]) -> None:
    set_roi_selection_in_progress(False)

    if is_roi_selection_cancel_requested():
        request_roi_selection_cancel(False)
        log_warn("[fishing] ROI selection canceled")
        add_user_log("탐색 영역 지정 취소", "warning")
        return

    if roi is None:
        log_warn("[fishing] ROI selection canceled")
        add_user_log("탐색 영역 지정 취소", "warning")
        return

    selected_roi = set_fishing_search_roi_from_screen(roi)
    if selected_roi is None:
        return

    if state.running:
        return

    log_success("[START] 자동 낚시 시작")
    start_session_stats()
    state.running = True
    state.stop_requested = False
    state.idle_announced = False
    add_user_log("낚시 시작", "start")


def request_fishing_stop(*, clear_roi: bool = True) -> None:
    was_running = state.running
    if state.running:
        log_warn("[STOP] 자동 낚시 중단")
        stop_session_stats()
    state.running = False
    state.stop_requested = True
    state.idle_announced = False
    request_roi_selection_cancel(True)
    if overlay_controller is not None:
        overlay_controller.cancel_roi_selection()
    if clear_roi:
        clear_current_fishing_search_roi()
    set_roi_selection_in_progress(False)
    if was_running:
        add_user_log("낚시 중단", "stop")
    clear_ready_debug_snapshot()


def is_fishing_running() -> bool:
    return state.running


def begin_fishing_roi_selection() -> None:
    if state.running:
        log_warn("[fishing] already running. Stop before selecting a new fishing area.")
        add_user_log("중단 후 영역 재지정 가능", "warning")
        return

    if is_roi_selection_in_progress():
        return

    clear_current_fishing_search_roi()
    request_roi_selection_cancel(False)
    set_roi_selection_in_progress(True)
    log_info("[fishing] ROI selection started")
    add_user_log("탐색 영역 드래그 지정 중", "wait")

    if overlay_controller is None:
        set_roi_selection_in_progress(False)
        log_error("[fishing] overlay controller is not ready")
        add_user_log("오버레이 준비 실패", "error")
        return

    overlay_controller.request_roi_selection(start_fishing_after_roi_selection)


def format_duration(seconds: float) -> str:
    total_seconds = int(max(0, seconds))

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds_part = divmod(remainder, 60)

    if days > 0:
        return f"{days}일 {hours}시간 {minutes}분"
    if hours > 0:
        return f"{hours}시간 {minutes}분"
    if minutes > 0:
        return f"{minutes}분 {seconds_part}초"
    return f"{seconds_part}초"


def start_session_stats() -> None:
    now = datetime.now()
    with session_stats_lock:
        session_stats.started_at = now
        session_stats.ended_at = None
        session_stats.cast_count = 0
        session_stats.catch_count = 0
        session_stats.is_running = True

    global run_started_at
    run_started_at = time.time()


def _ensure_column_exists(conn: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row[1]) for row in rows}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _init_stats_db() -> None:
    STATS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(STATS_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_fishing_stats (
                day TEXT PRIMARY KEY,
                cast_count INTEGER NOT NULL DEFAULT 0,
                catch_count INTEGER NOT NULL DEFAULT 0,
                run_seconds INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS total_fishing_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cast_count INTEGER NOT NULL DEFAULT 0,
                catch_count INTEGER NOT NULL DEFAULT 0,
                run_seconds INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _ensure_column_exists(conn, "daily_fishing_stats", "run_seconds", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column_exists(conn, "total_fishing_stats", "run_seconds", "INTEGER NOT NULL DEFAULT 0")

        conn.execute(
            """
            INSERT INTO total_fishing_stats (id, cast_count, catch_count, run_seconds)
            VALUES (1, 0, 0, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        conn.commit()


def _load_today_stats(day_key: str) -> tuple[int, int, int]:
    with sqlite3.connect(STATS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT cast_count, catch_count, run_seconds FROM daily_fishing_stats WHERE day = ?",
            (day_key,),
        ).fetchone()

    if row is None:
        return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])


def _load_total_stats() -> tuple[int, int, int]:
    with sqlite3.connect(STATS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT cast_count, catch_count, run_seconds FROM total_fishing_stats WHERE id = 1"
        ).fetchone()

    if row is None:
        return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])


def _refresh_today_cache_if_needed() -> None:
    global today_key, today_cast_count, today_catch_count, today_run_seconds
    current_day = date.today().isoformat()
    if current_day == today_key:
        return

    today_key = current_day
    today_cast_count, today_catch_count, today_run_seconds = _load_today_stats(today_key)


def _apply_stats_delta(cast_delta: int, catch_delta: int, run_seconds_delta: int, day_key: str) -> None:
    if cast_delta < 0 or catch_delta < 0 or run_seconds_delta < 0:
        return

    with sqlite3.connect(STATS_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_fishing_stats (day, cast_count, catch_count, run_seconds)
            VALUES (?, 0, 0, 0)
            ON CONFLICT(day) DO NOTHING
            """,
            (day_key,),
        )
        conn.execute(
            """
            UPDATE daily_fishing_stats
            SET cast_count = cast_count + ?,
                catch_count = catch_count + ?,
                run_seconds = run_seconds + ?
            WHERE day = ?
            """,
            (cast_delta, catch_delta, run_seconds_delta, day_key),
        )
        conn.execute(
            """
            UPDATE total_fishing_stats
            SET cast_count = cast_count + ?,
                catch_count = catch_count + ?,
                run_seconds = run_seconds + ?
            WHERE id = 1
            """,
            (cast_delta, catch_delta, run_seconds_delta),
        )
        conn.commit()


def _flush_run_time_once() -> None:
    global run_started_at, today_cast_count, today_catch_count, today_run_seconds

    with session_stats_lock:
        if run_started_at is None:
            return

        elapsed_seconds = int(time.time() - run_started_at)
        session_cast_count = max(0, int(session_stats.cast_count))
        session_catch_count = max(0, int(session_stats.catch_count))
        day_key = date.today().isoformat()
        run_started_at = None

    if elapsed_seconds <= 0 and session_cast_count <= 0 and session_catch_count <= 0:
        return

    _apply_stats_delta(
        cast_delta=session_cast_count,
        catch_delta=session_catch_count,
        run_seconds_delta=max(0, elapsed_seconds),
        day_key=day_key,
    )

    if day_key == today_key:
        today_cast_count += session_cast_count
        today_catch_count += session_catch_count
        today_run_seconds += max(0, elapsed_seconds)

    total_stats.total_cast_count += session_cast_count
    total_stats.total_catch_count += session_catch_count
    total_stats.total_run_seconds += max(0, elapsed_seconds)


def stop_session_stats() -> None:
    now = datetime.now()
    with session_stats_lock:
        if not session_stats.is_running or session_stats.started_at is None:
            return

        session_stats.ended_at = now
        duration_seconds = int(max(0, (now - session_stats.started_at).total_seconds()))

        total_stats.sessions.append(
            {
                "started_at": session_stats.started_at.isoformat(timespec="seconds"),
                "ended_at": session_stats.ended_at.isoformat(timespec="seconds"),
                "duration_seconds": duration_seconds,
                "cast_count": session_stats.cast_count,
                "catch_count": session_stats.catch_count,
            }
        )
        session_stats.is_running = False

    _flush_run_time_once()


def increment_cast_count() -> None:
    with session_stats_lock:
        session_stats.cast_count += 1


def increment_catch_count() -> None:
    with session_stats_lock:
        session_stats.catch_count += 1


def load_fishing_stats() -> None:
    try:
        _init_stats_db()

        global today_key, today_cast_count, today_catch_count, today_run_seconds
        today_key = date.today().isoformat()
        today_cast_count, today_catch_count, today_run_seconds = _load_today_stats(today_key)

        total_cast_count, total_catch_count, total_run_seconds = _load_total_stats()
        with session_stats_lock:
            total_stats.total_cast_count = total_cast_count
            total_stats.total_catch_count = total_catch_count
            total_stats.total_run_seconds = total_run_seconds
            total_stats.sessions = []
    except Exception:
        log_warn("[STATS] 저장된 통계를 읽지 못해 기본값으로 시작합니다")
        add_user_log("상태 확인 필요", "error")


def save_fishing_stats() -> None:
    try:
        _refresh_today_cache_if_needed()
        _init_stats_db()
    except Exception:
        log_warn("[STATS] 통계를 저장하지 못했습니다")
        add_user_log("상태 확인 필요", "error")


def build_overlay_snapshot() -> OverlaySnapshot:
    _refresh_today_cache_if_needed()

    with session_stats_lock:
        started_at = session_stats.started_at
        ended_at = session_stats.ended_at
        session_cast_count = session_stats.cast_count
        session_catch_count = session_stats.catch_count
        stats_running = session_stats.is_running
        base_total_cast_count = total_stats.total_cast_count
        base_total_catch_count = total_stats.total_catch_count
        base_total_run_seconds = total_stats.total_run_seconds
        active_run_started_at = run_started_at

    session_elapsed_seconds = 0
    if stats_running and active_run_started_at is not None:
        session_elapsed_seconds = max(0, int(time.time() - active_run_started_at))

    if stats_running:
        today_cast_display = today_cast_count + session_cast_count
        today_catch_display = today_catch_count + session_catch_count
        today_run_display = today_run_seconds + session_elapsed_seconds
    else:
        today_cast_display = today_cast_count
        today_catch_display = today_catch_count
        today_run_display = today_run_seconds

    if stats_running:
        total_cast_count = base_total_cast_count + session_cast_count
        total_catch_count = base_total_catch_count + session_catch_count
        total_run_seconds = base_total_run_seconds + session_elapsed_seconds
    else:
        total_cast_count = base_total_cast_count
        total_catch_count = base_total_catch_count
        total_run_seconds = base_total_run_seconds

    if state.running:
        status = "실행 중"
    elif started_at is not None and not stats_running:
        status = "중단됨"
    else:
        status = "대기 중"

    if started_at is None:
        started_at_text = "-"
    else:
        started_at_text = started_at.strftime("%H:%M:%S")

    duration_text = format_duration(today_run_display)
    total_run_text = format_duration(total_run_seconds)

    anchor_rect = None
    if is_target_window_active(config.CONFIG.window_keywords):
        anchor_rect = get_foreground_rect()

    return OverlaySnapshot(
        title="낚시",
        status=status,
        started_at_text=started_at_text,
        duration_text=duration_text,
        cast_count=today_cast_display,
        catch_count=today_catch_display,
        total_cast_count=total_cast_count,
        total_catch_count=total_catch_count,
        total_run_text=total_run_text,
        logs=user_logs.get_lines(),
        is_running=stats_running,
        anchor_rect=anchor_rect,
        ready_debug=ready_debug_snapshot,
    )


@dataclass(frozen=True)
class ReadyDetectionResult:
    found: bool
    score: float
    score_passed: bool
    ignored: bool
    roi_top_left: tuple[int, int] | None
    screen_center: tuple[int, int] | None
    screen_bgr: np.ndarray
    scale: float = 1.0
    roi_type: str = "unknown"


@dataclass(frozen=True)
class ReadyWaitResult:
    status: str
    detection: Optional[ReadyDetectionResult] = None
    elapsed: float = 0.0


@dataclass
class ReadyCandidate:
    """Candidate Ready detection awaiting confirmation."""
    center: tuple[int, int]
    score: float
    scale: float
    roi_type: str
    seen_at: float


@dataclass
class ReadyColorTrackingState:
    hit_frames: int = 0
    last_blob: Optional[ReadyColorBlob] = None
    was_detecting: bool = False
    accepted_logged: bool = False


@dataclass(frozen=True)
class BobberCandidate:
    center: tuple[int, int]
    area: int
    score: float


@dataclass
class BobberTrackingState:
    active_bobber_roi: Optional[tuple[int, int, int, int]] = None
    active_bobber_center: Optional[tuple[int, int]] = None
    bobber_track_fail_count: int = 0
    bobber_reacquire_fail_count: int = 0
    last_bobber_seen_at: Optional[float] = None
    fishing_cast_started_at: Optional[float] = None
    search_status: str = "wide search"
    top_candidates: List[BobberCandidate] = field(default_factory=list)


def reset_bobber_tracking_state() -> BobberTrackingState:
    return BobberTrackingState(fishing_cast_started_at=time.monotonic())


def get_ready_scale_threshold(base_threshold: float, scale: float) -> float:
    """Get threshold adjusted for template scale."""
    if scale <= 0.75:
        return min(0.98, base_threshold + 0.06)
    if scale == 0.85:
        return min(0.98, base_threshold + 0.02)
    if scale >= 1.15:
        return min(0.98, base_threshold + 0.01)
    return base_threshold


def is_ready_instant_confirm(score: float, scale: float, roi_type: str) -> bool:
    """Check if Ready can be confirmed instantly (high score + good scale)."""
    # Only instant confirm for 1.0 scale or larger, and not wide ROI
    return (
        score >= config.CONFIG.ready_instant_confirm_score
        and scale >= 1.0
        and roi_type != "wide"
    )


def is_ready_distance_acceptable(
    pos1: tuple[int, int],
    pos2: tuple[int, int],
    max_distance: int,
) -> bool:
    """Check if two positions are close enough to be same Ready icon."""
    dx = pos1[0] - pos2[0]
    dy = pos1[1] - pos2[1]
    distance = (dx * dx + dy * dy) ** 0.5
    return distance <= max_distance


def get_ready_quick_confirm_failure_reason(
    first: ReadyDetectionResult,
    second: ReadyDetectionResult,
) -> Optional[str]:
    if not second.found:
        if second.score < config.CONFIG.ready_threshold:
            return f"score score={second.score:.3f}"
        return "not_found"

    if second.ignored:
        return "ignored"

    first_center = first.screen_center
    second_center = second.screen_center
    if first_center is None or second_center is None:
        return "no_screen_center"

    if first.roi_type not in config.CONFIG.ready_single_confirm_rois:
        return f"roi first={first.roi_type}"

    if second.roi_type not in config.CONFIG.ready_single_confirm_rois:
        return f"roi second={second.roi_type}"

    if second.score < config.CONFIG.ready_threshold:
        return f"score score={second.score:.3f}"

    if not is_ready_distance_acceptable(
        first_center,
        second_center,
        config.CONFIG.ready_quick_confirm_max_distance,
    ):
        return f"distance first={first_center} second={second_center}"

    return None


def normalize_key(key) -> Optional[str]:
    keyboard = _get_keyboard_module()
    if key == keyboard.Key.page_up:
        return "page_up"
    if key == keyboard.Key.page_down:
        return "page_down"
    if key == keyboard.Key.esc:
        return "esc"
    if key == keyboard.Key.f12:
        return "f12"
    return None


def on_key_press(key) -> None:
    normalized = normalize_key(key)

    if normalized == config.CONFIG.fishing_roi_select_key:
        if start_hotkey_enabled:
            begin_fishing_roi_selection()
    elif normalized in (config.CONFIG.stop_hotkey, "esc", "f12"):
        request_fishing_stop(clear_roi=clear_roi_on_stop)


def start_hotkey_listener() -> HotkeyListener:
    global hotkey_listener
    if hotkey_listener is not None:
        return hotkey_listener

    keyboard = _get_keyboard_module()
    listener = keyboard.Listener(on_press=on_key_press)
    listener.daemon = True
    listener.start()
    hotkey_listener = listener
    return listener


def stop_hotkey_listener() -> None:
    global hotkey_listener
    listener = hotkey_listener
    if listener is None:
        return
    listener.stop()
    hotkey_listener = None


def wait_until_started() -> None:
    if not state.idle_announced:
        log_dim("[IDLE] PageUp 영역 지정 후 시작 / PageDown 중단 / ESC,F12 중단 / Ctrl+C 종료")
        state.idle_announced = True

    while not state.running:
        time.sleep(0.2)


def should_stop() -> bool:
    return state.stop_requested or not state.running


def ensure_template_file(source_path: Path, target_path: Path, label: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        log_dim(f"[templates] {label} template exists: {target_path}")
        return

    if source_path.exists():
        shutil.copy2(source_path, target_path)
        log_info(f"[templates] {label} template copied: {source_path} -> {target_path}")
        return

    raise FileNotFoundError(
        f"{label} template not found. Expected target={target_path} "
        f"or fallback source={source_path}. Put the final template file in templates first."
    )


def ensure_templates() -> None:
    ensure_template_file(
        config.resolve_root_icon_path(config.CONFIG.root_start_icon_source),
        config.resolve_path(config.CONFIG.start_template_path),
        "START",
    )
    ensure_template_file(
        config.resolve_root_icon_path(config.CONFIG.root_ready_icon_source),
        config.resolve_path(config.CONFIG.ready_template_path),
        "READY",
    )


def get_detection_screen_center(
    result: DetectionResult | ReadyDetectionResult,
    capture_region: Optional[CaptureRegion] = None,
) -> Optional[tuple[int, int]]:
    screen_center = getattr(result, "screen_center", None)
    if screen_center is not None:
        return int(screen_center[0]), int(screen_center[1])

    center = getattr(result, "center", None)
    if center is None:
        return None

    roi_top_left = getattr(result, "roi_top_left", None)
    if roi_top_left is not None:
        return int(roi_top_left[0] + center[0]), int(roi_top_left[1] + center[1])

    if capture_region is not None:
        top_left = getattr(result, "top_left", None)
        if top_left is not None and (top_left[0] < capture_region.left or top_left[1] < capture_region.top):
            return capture_region.left + int(center[0]), capture_region.top + int(center[1])

    return int(center[0]), int(center[1])


def get_detection_roi_top_left(
    result: DetectionResult,
    capture_region: CaptureRegion,
) -> Optional[tuple[int, int]]:
    if result.top_left is None:
        return None
    return (
        int(result.top_left[0] - capture_region.left),
        int(result.top_left[1] - capture_region.top),
    )


def save_ready_debug_crop(
    screen_bgr: np.ndarray,
    roi_top_left: tuple[int, int] | None,
    template: TemplateImage,
    score: float,
    prefix: str = "ready_match",
) -> None:
    if not config.CONFIG.debug_save_ready_match:
        return
    if roi_top_left is None:
        return

    debug_dir = Path(config.CONFIG.debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    x, y = roi_top_left
    pad = 30

    left = max(x - pad, 0)
    top = max(y - pad, 0)
    right = min(x + template.width + pad, screen_bgr.shape[1])
    bottom = min(y + template.height + pad, screen_bgr.shape[0])

    crop = screen_bgr[top:bottom, left:right]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = debug_dir / f"{prefix}_{timestamp}_score_{score:.3f}.png"

    cv2.imwrite(str(filename), crop)
    log_dim(f"[debug] ready match crop saved: {filename}")


def save_ready_roi_debug_image(screen_bgr: np.ndarray, score: float) -> None:
    if not config.CONFIG.debug_save_ready_roi:
        return

    debug_dir = Path(config.CONFIG.debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = debug_dir / f"ready_roi_{timestamp}_score_{score:.3f}.png"

    cv2.imwrite(str(filename), screen_bgr)
    log_dim(f"[debug] ready ROI image saved: {filename}")


def detect_ready_icon(
    template: TemplateImage,
    threshold: float,
    roi: tuple[int, int, int, int] | None,
) -> ReadyDetectionResult:
    screen_bgr, capture_region = capture_screen(roi)
    result = find_best_match(screen_bgr, capture_region, template, 0.0)

    if result.top_left is None or result.center is None:
        roi_top_left = None
        screen_center = None
    else:
        roi_top_left = get_detection_roi_top_left(result, capture_region)
        screen_center = get_detection_screen_center(result, capture_region)

    score_passed = result.score >= threshold
    ignored = is_ready_ignored(screen_center)
    found = score_passed and not ignored

    return ReadyDetectionResult(
        found=found,
        score=result.score,
        score_passed=score_passed,
        ignored=ignored,
        roi_top_left=roi_top_left,
        screen_center=screen_center,
        screen_bgr=screen_bgr,
        scale=1.0,
        roi_type="default",
    )


def convert_detection_result_to_ready(
    det_result: DetectionResult,
    capture_region: CaptureRegion,
    screen_bgr: np.ndarray,
    threshold: float,
    scale: float = 1.0,
) -> ReadyDetectionResult:
    """Convert DetectionResult (from detector.py) to ReadyDetectionResult."""
    if det_result.top_left is None or det_result.center is None:
        roi_top_left = None
        screen_center = None
    else:
        roi_top_left = get_detection_roi_top_left(det_result, capture_region)
        screen_center = get_detection_screen_center(det_result, capture_region)

    score_passed = det_result.score >= threshold
    ignored = is_ready_ignored(screen_center)
    found = score_passed and not ignored

    return ReadyDetectionResult(
        found=found,
        score=det_result.score,
        score_passed=score_passed,
        ignored=ignored,
        roi_top_left=roi_top_left,
        screen_center=screen_center,
        screen_bgr=screen_bgr,
        scale=scale,
        roi_type="default",
    )


def detect_ready_icon_multi_scale(
    template: TemplateImage,
    threshold: float,
    roi: tuple[int, int, int, int] | None,
) -> ReadyDetectionResult:
    screen_bgr, capture_region = capture_screen(roi)
    result, scale = find_best_match_multi_scale(
        screen_bgr,
        capture_region,
        template,
        0.0,
        config.CONFIG.ready_template_scales,
    )
    effective_threshold = get_ready_scale_threshold(threshold, scale)
    return convert_detection_result_to_ready(
        result,
        capture_region,
        screen_bgr,
        effective_threshold,
        scale=scale,
    )


def _build_center_roi(
    center_x: int,
    center_y: int,
    radius_x: int,
    radius_y: int,
    bounds: Optional[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    left = int(center_x - radius_x)
    top = int(center_y - radius_y)
    width = int(radius_x * 2)
    height = int(radius_y * 2)

    if bounds is None:
        width = max(1, width)
        height = max(1, height)
        return left, top, width, height

    b_left, b_top, b_width, b_height = bounds
    b_right = b_left + max(0, b_width)
    b_bottom = b_top + max(0, b_height)

    clamped_left = max(b_left, left)
    clamped_top = max(b_top, top)
    clamped_right = min(b_right, left + width)
    clamped_bottom = min(b_bottom, top + height)

    final_width = max(1, clamped_right - clamped_left)
    final_height = max(1, clamped_bottom - clamped_top)
    return clamped_left, clamped_top, final_width, final_height


def expand_roi(
    roi: tuple[int, int, int, int],
    padding: int,
    bounds: Optional[tuple[int, int, int, int]] = None,
) -> tuple[int, int, int, int]:
    left, top, width, height = roi
    center_x = left + width // 2
    center_y = top + height // 2
    return _build_center_roi(
        center_x,
        center_y,
        (width // 2) + padding,
        (height // 2) + padding,
        bounds,
    )


def build_bobber_tracking_roi(candidate: BobberCandidate) -> tuple[int, int, int, int]:
    bounds = get_local_window_bounds()
    return _build_center_roi(
        candidate.center[0],
        candidate.center[1],
        config.CONFIG.bobber_track_padding,
        config.CONFIG.bobber_track_padding,
        bounds,
    )


def _format_roi(roi: Optional[tuple[int, int, int, int]]) -> str:
    if roi is None:
        return "-"
    left, top, width, height = roi
    return f"{left},{top},{width}x{height}"


def _format_bobber_candidates(candidates: List[BobberCandidate]) -> str:
    if not candidates:
        return "-"
    return " | ".join(
        f"{idx + 1}:{candidate.score:.2f}@{candidate.center}"
        for idx, candidate in enumerate(candidates[:3])
    )


def _build_start_window_fallback_roi(
    bounds: Optional[tuple[int, int, int, int]],
) -> Optional[tuple[int, int, int, int]]:
    if bounds is None:
        return None

    left, top, width, height = bounds
    roi_left = left + int(width * 0.22)
    roi_top = top + int(height * 0.38)
    roi_right = left + int(width * 0.78)
    roi_bottom = top + int(height * 0.90)

    if roi_right <= roi_left or roi_bottom <= roi_top:
        return None
    return roi_left, roi_top, roi_right - roi_left, roi_bottom - roi_top


def build_character_ready_roi(base_x: int, base_y: int) -> tuple[int, int, int, int]:
    bounds = get_local_window_bounds()
    center_x = base_x + config.CONFIG.character_ready_center_offset_x
    center_y = base_y + config.CONFIG.character_ready_center_offset_y
    return _build_center_roi(
        center_x,
        center_y,
        config.CONFIG.character_ready_search_radius_x,
        config.CONFIG.character_ready_search_radius_y,
        bounds,
    )


def build_character_fast_ready_roi(base_x: int, base_y: int) -> tuple[int, int, int, int]:
    bounds = get_local_window_bounds()
    center_x = base_x + config.CONFIG.character_ready_center_offset_x
    center_y = base_y + config.CONFIG.character_ready_center_offset_y
    return _build_center_roi(
        center_x,
        center_y,
        config.CONFIG.character_ready_fast_radius_x,
        config.CONFIG.character_ready_fast_radius_y,
        bounds,
    )


def _dedupe_rois(
    rois: List[tuple[str, Optional[tuple[int, int, int, int]]]],
) -> List[tuple[str, Optional[tuple[int, int, int, int]]]]:
    seen: set[Optional[tuple[int, int, int, int]]] = set()
    unique_rois: List[tuple[str, Optional[tuple[int, int, int, int]]]] = []
    for source, roi in rois:
        if roi in seen:
            continue
        seen.add(roi)
        unique_rois.append((source, roi))
    return unique_rois


def _build_start_search_rois() -> List[tuple[str, Optional[tuple[int, int, int, int]]]]:
    bounds = get_local_window_bounds()
    rois: List[tuple[str, Optional[tuple[int, int, int, int]]]] = [
        ("configured", config.CONFIG.start_roi)
    ]

    window_fallback_roi = _build_start_window_fallback_roi(bounds)
    if window_fallback_roi is not None:
        rois.append(("window_center_bottom", window_fallback_roi))

    base_x, base_y = get_character_reference_point()
    rois.append(
        (
            "character",
            _build_center_roi(
                base_x,
                base_y,
                config.CONFIG.start_character_search_radius_x,
                config.CONFIG.start_character_search_radius_y,
                bounds,
            ),
        )
    )

    if last_start_icon_pos is not None:
        rois.append(
            (
                "recent",
                _build_center_roi(
                    last_start_icon_pos[0],
                    last_start_icon_pos[1],
                    config.CONFIG.start_recent_roi_radius_x,
                    config.CONFIG.start_recent_roi_radius_y,
                    bounds,
                ),
            )
        )
    return _dedupe_rois(rois)


def detect_start_icon(template: TemplateImage) -> DetectionResult:
    log_info("[CAST] start icon search")

    best_result: Optional[DetectionResult] = None
    best_source = "none"
    best_scale = 1.0
    for source, roi in _build_start_search_rois():
        frame_bgr, capture_region = capture_screen(roi)
        result, scale = find_best_match_multi_scale(
            frame_bgr,
            capture_region,
            template,
            config.CONFIG.start_threshold,
            config.CONFIG.start_template_scales,
        )
        center = get_detection_screen_center(result, capture_region)
        log_dim(
            f"[START] candidate roi={source} scale={scale:.2f} score={result.score:.3f} center={center}"
        )

        if best_result is None or result.score > best_result.score:
            best_result = result
            best_source = source
            best_scale = scale

        if result.found:
            log_success(
                f"[START] found roi={source} scale={scale:.2f} score={result.score:.3f} center={center}"
            )
            return result

    if best_result is not None:
        log_dim(
            f"[CAST] start icon not found best_roi={best_source} scale={best_scale:.2f} score={best_result.score:.3f}"
        )
        return best_result

    return DetectionResult(
        found=False,
        score=-1.0,
        top_left=None,
        center=None,
        width=template.width,
        height=template.height,
    )


def _save_ready_miss_crop(frame_bgr: np.ndarray, roi: tuple[int, int, int, int], source: str) -> None:
    if not config.CONFIG.ready_debug_save_misses:
        return

    global last_ready_miss_saved_at
    now = time.monotonic()
    if now - last_ready_miss_saved_at < config.CONFIG.ready_debug_save_interval_seconds:
        return

    last_ready_miss_saved_at = now
    out_dir = Path(config.CONFIG.ready_debug_save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = out_dir / f"ready_miss_{source}_{timestamp}.png"
    cv2.imwrite(str(filename), frame_bgr)
    if config.CONFIG.ready_debug_log:
        log_dim(f"[READY] miss crop 저장: {filename} roi={roi}")


def detect_ready_icon_adaptive(
    template: TemplateImage,
    threshold: float,
    default_ready_roi: tuple[int, int, int, int] | None,
    active_bobber_roi: tuple[int, int, int, int] | None,
    base_x: int,
    base_y: int,
    elapsed_text: Optional[str] = None,
) -> ReadyDetectionResult:
    candidate_rois: List[tuple[str, tuple[int, int, int, int], bool]] = []
    bounds = get_local_window_bounds()

    character_full_roi: Optional[tuple[int, int, int, int]] = None
    if config.CONFIG.ready_search_around_character:
        character_full_roi = build_character_ready_roi(base_x, base_y)

    if config.CONFIG.ready_search_around_character and config.CONFIG.ready_fast_roi_enabled:
        candidate_rois.append(("character_fast", build_character_fast_ready_roi(base_x, base_y), True))

    if config.CONFIG.ready_use_last_roi and last_ready_icon_pos is not None:
        last_roi = _build_center_roi(
            last_ready_icon_pos[0],
            last_ready_icon_pos[1],
            config.CONFIG.ready_last_pos_radius_x,
            config.CONFIG.ready_last_pos_radius_y,
            bounds,
        )
        candidate_rois.append(("last", last_roi, True))

    if config.CONFIG.ready_search_around_character and character_full_roi is not None:
        candidate_rois.append(("character_full", character_full_roi, True))

    if config.CONFIG.ready_fallback_to_default_roi and default_ready_roi is not None:
        candidate_rois.append(("default", default_ready_roi, True))

    if config.CONFIG.ready_use_bobber_roi and active_bobber_roi is not None:
        candidate_rois.append(("bobber", active_bobber_roi, False))

    if not candidate_rois and default_ready_roi is not None:
        candidate_rois.append(("default", default_ready_roi, True))

    miss_frame: Optional[np.ndarray] = None
    miss_roi: Optional[tuple[int, int, int, int]] = None
    miss_source = "none"
    last_result: Optional[ReadyDetectionResult] = None
    best_scan_result: Optional[ReadyDetectionResult] = None
    best_scan_source = "none"
    best_scan_threshold = threshold

    for source, roi, use_multi_scale in candidate_rois:
        effective_threshold = threshold
        
        # Apply character_fast threshold bonus
        if source == "character_fast":
            effective_threshold = threshold + config.CONFIG.ready_character_fast_threshold_bonus

        if use_multi_scale and config.CONFIG.ready_multi_scale_enabled:
            result = detect_ready_icon_multi_scale(template, effective_threshold, roi)
        else:
            result = detect_ready_icon(template, effective_threshold, roi)
        candidate_threshold = (
            get_ready_scale_threshold(effective_threshold, result.scale)
            if use_multi_scale and config.CONFIG.ready_multi_scale_enabled
            else effective_threshold
        )

        if best_scan_result is None or result.score > best_scan_result.score:
            best_scan_result = result
            best_scan_source = source
            best_scan_threshold = candidate_threshold

        last_result = result
        if result.found and result.screen_center is not None:
            skip_reason = get_ready_candidate_skip_reason(
                result.screen_center,
                source,
                roi,
                character_full_roi,
                character_center=(base_x, base_y),
            )
            if skip_reason is not None:
                log_ready_candidate_skipped(source, skip_reason, result.score, result.screen_center)
                update_ready_debug_snapshot(
                    status="skipped",
                    roi_type=source,
                    scale=result.scale,
                    score=result.score,
                    threshold=candidate_threshold,
                    pos=result.screen_center,
                    skip_reason=skip_reason,
                    roi=roi,
                    crop_bgr=get_ready_candidate_crop(
                        result.screen_bgr,
                        result.roi_top_left,
                        template,
                        result.scale,
                    ),
                    elapsed_text=elapsed_text,
                )
                last_result = ReadyDetectionResult(
                    found=False,
                    score=result.score,
                    score_passed=result.score_passed,
                    ignored=result.ignored,
                    roi_top_left=result.roi_top_left,
                    screen_center=result.screen_center,
                    screen_bgr=result.screen_bgr,
                    scale=result.scale,
                    roi_type=source,
                )
                continue

            candidate_crop = get_ready_candidate_crop(
                result.screen_bgr,
                result.roi_top_left,
                template,
                result.scale,
            )
            if is_green_bar_like_region(candidate_crop):
                log_dim(
                    f"[READY] candidate skipped reason=green_bar_like_ui roi={source} scale={result.scale:.2f} score={result.score:.3f} pos={result.screen_center}"
                )
                update_ready_debug_snapshot(
                    status="skipped",
                    roi_type=source,
                    scale=result.scale,
                    score=result.score,
                    threshold=candidate_threshold,
                    pos=result.screen_center,
                    skip_reason="green_bar_like_ui",
                    roi=roi,
                    crop_bgr=candidate_crop,
                    elapsed_text=elapsed_text,
                )
                last_result = ReadyDetectionResult(
                    found=False,
                    score=result.score,
                    score_passed=result.score_passed,
                    ignored=result.ignored,
                    roi_top_left=result.roi_top_left,
                    screen_center=result.screen_center,
                    screen_bgr=result.screen_bgr,
                    scale=result.scale,
                    roi_type=source,
                )
                continue

            if config.CONFIG.ready_debug_log:
                log_dim(
                    f"[READY] candidate roi={source} scale={result.scale:.2f} score={result.score:.3f} pos={result.screen_center}"
                )
            update_ready_debug_snapshot(
                status="candidate",
                roi_type=source,
                scale=result.scale,
                score=result.score,
                threshold=candidate_threshold,
                pos=result.screen_center,
                roi=roi,
                crop_bgr=candidate_crop,
                elapsed_text=elapsed_text,
            )
            # Create new result with scale and roi_type information
            return ReadyDetectionResult(
                found=result.found,
                score=result.score,
                score_passed=result.score_passed,
                ignored=result.ignored,
                roi_top_left=result.roi_top_left,
                screen_center=result.screen_center,
                screen_bgr=result.screen_bgr,
                scale=result.scale,
                roi_type=source,
            )

        miss_frame = result.screen_bgr
        miss_roi = roi
        miss_source = source

    if best_scan_result is not None:
        update_ready_debug_snapshot(
            status="scanning",
            roi_type=best_scan_source,
            scale=best_scan_result.scale,
            score=best_scan_result.score,
            threshold=best_scan_threshold,
            pos=best_scan_result.screen_center,
            skip_reason="low_score",
            crop_bgr=best_scan_result.screen_bgr,
            elapsed_text=elapsed_text,
        )

    if miss_frame is not None and miss_roi is not None:
        _save_ready_miss_crop(miss_frame, miss_roi, miss_source)

    if config.CONFIG.ready_debug_log:
        log_dim("[READY] 탐색 실패: fallback 계속 사용")

    if last_result is not None:
        return last_result

    return ReadyDetectionResult(
        found=False,
        score=0.0,
        score_passed=False,
        ignored=False,
        roi_top_left=None,
        screen_center=None,
        screen_bgr=np.zeros((8, 8, 3), dtype=np.uint8),
        scale=1.0,
        roi_type="none",
    )


def score_bobber_candidate(
    candidate: BobberCandidate,
    previous_position: Optional[tuple[int, int]] = None,
) -> float:
    if previous_position is None:
        return float(candidate.area)

    dist = math.hypot(
        candidate.center[0] - previous_position[0],
        candidate.center[1] - previous_position[1],
    )
    return float(candidate.area) + max(0.0, 120.0 - dist)


def find_bobber_candidates(
    frame_bgr: np.ndarray,
    search_roi: tuple[int, int, int, int],
    previous_position: Optional[tuple[int, int]] = None,
) -> List[BobberCandidate]:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    lower = np.array(config.CONFIG.bobber_indicator_hsv_lower, dtype=np.uint8)
    upper = np.array(config.CONFIG.bobber_indicator_hsv_upper, dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)

    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates: List[BobberCandidate] = []
    capture_left, capture_top = search_roi[0], search_roi[1]

    for idx in range(1, count):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < config.CONFIG.bobber_indicator_min_area:
            continue

        cx_local, cy_local = centroids[idx]
        cx = capture_left + int(cx_local)
        cy = capture_top + int(cy_local)
        candidate = BobberCandidate(center=(cx, cy), area=area, score=0.0)
        candidates.append(
            BobberCandidate(
                center=candidate.center,
                area=candidate.area,
                score=score_bobber_candidate(candidate, previous_position),
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def select_best_bobber_candidate(candidates: List[BobberCandidate]) -> Optional[BobberCandidate]:
    if not candidates:
        return None
    return candidates[0]


def locate_active_bobber_roi_in_current_search_area() -> Optional[tuple[int, int, int, int]]:
    search_roi = get_current_fishing_search_roi()
    if search_roi is None:
        log_warn("[fishing] search ROI is not selected. Press PageUp and drag fishing area first.")
        return None

    if config.CONFIG.bobber_debug_log:
        log_info(f"[bobber] wide search start search_roi={search_roi}")

    if config.CONFIG.bobber_locate_delay > 0:
        time.sleep(config.CONFIG.bobber_locate_delay)

    deadline = time.monotonic() + config.CONFIG.bobber_locate_timeout
    while time.monotonic() < deadline:
        selected, candidates, _ = search_bobber_in_roi(search_roi, None)
        if selected is not None:
            roi = build_bobber_tracking_roi(selected)
            log_info(
                f"[bobber] candidate selected score={selected.score:.2f} center={selected.center} roi={roi}"
            )
            return roi

        time.sleep(0.05)

    log_warn("[bobber] wide search failed in selected search ROI")
    return None


def set_bobber_search_status(tracking: BobberTrackingState, status: str, message: str) -> None:
    if tracking.search_status == status:
        return
    tracking.search_status = status
    if config.CONFIG.bobber_debug_log:
        log_info(message)


def build_bobber_wide_search_roi() -> Optional[tuple[int, int, int, int]]:
    return get_current_fishing_search_roi()


def search_bobber_in_roi(
    search_roi: tuple[int, int, int, int],
    previous_position: Optional[tuple[int, int]],
) -> tuple[Optional[BobberCandidate], List[BobberCandidate], Optional[np.ndarray]]:
    frame_bgr, _ = capture_screen(search_roi)
    candidates = find_bobber_candidates(frame_bgr, search_roi, previous_position)
    return select_best_bobber_candidate(candidates), candidates[:3], frame_bgr


def update_bobber_tracking_from_candidate(
    tracking: BobberTrackingState,
    candidate: BobberCandidate,
    *,
    status: str = "tracking",
) -> None:
    tracking.active_bobber_center = candidate.center
    tracking.active_bobber_roi = build_bobber_tracking_roi(candidate)
    tracking.bobber_track_fail_count = 0
    tracking.bobber_reacquire_fail_count = 0
    tracking.last_bobber_seen_at = time.monotonic()
    tracking.search_status = status


def build_bobber_debug_candidates(candidates: List[BobberCandidate]) -> List[Dict[str, Any]]:
    return [
        {
            "center": candidate.center,
            "score": candidate.score,
            "area": candidate.area,
        }
        for candidate in candidates[:3]
    ]


def build_ready_color_debug(
    color_state: ReadyColorTrackingState,
) -> Dict[str, Any]:
    blob = color_state.last_blob
    return {
        "enabled": config.CONFIG.ready_color_detection_enabled,
        "hits": color_state.hit_frames,
        "required": config.CONFIG.ready_color_min_frames,
        "blob": blob.bbox if blob is not None else None,
        "area": blob.area if blob is not None else None,
        "hsv_lower": config.CONFIG.ready_color_hsv_lower,
        "hsv_upper": config.CONFIG.ready_color_hsv_upper,
    }


def update_ready_color_state(
    color_state: ReadyColorTrackingState,
    blobs: List[ReadyColorBlob],
) -> Optional[ReadyColorBlob]:
    blob = blobs[0] if blobs else None

    if blob is None:
        if color_state.hit_frames > 0 or color_state.was_detecting:
            log_dim("[ready-color] blob lost, reset")
        color_state.hit_frames = 0
        color_state.last_blob = None
        color_state.was_detecting = False
        color_state.accepted_logged = False
        return None

    if not color_state.was_detecting:
        log_dim(f"[ready-color] blob detected area={blob.area:.1f}")

    color_state.hit_frames += 1
    color_state.last_blob = blob
    color_state.was_detecting = True

    required = config.CONFIG.ready_color_min_frames
    if color_state.hit_frames <= required:
        log_dim(f"[ready-color] consecutive hit {color_state.hit_frames}/{required}")

    if color_state.hit_frames >= required and not color_state.accepted_logged:
        log_info("[ready-color] ready candidate accepted")
        color_state.accepted_logged = True

    return blob


def detect_ready_color_candidate(
    color_state: ReadyColorTrackingState,
) -> tuple[Optional[ReadyColorBlob], Optional[np.ndarray]]:
    if not config.CONFIG.ready_color_detection_enabled:
        return None, None

    search_roi = get_current_fishing_search_roi()
    if search_roi is None:
        return None, None

    frame_bgr, _ = capture_screen(search_roi)
    blobs = detect_ready_color_blobs(frame_bgr, search_roi)
    blob = update_ready_color_state(color_state, blobs)
    return blob, frame_bgr


def update_bobber_debug_overlay(
    tracking: BobberTrackingState,
    *,
    threshold: float,
    score: float = 0.0,
    pos: Optional[tuple[int, int]] = None,
    crop_bgr: Optional[np.ndarray] = None,
    elapsed_text: Optional[str] = None,
    reason: Optional[str] = None,
    color_state: Optional[ReadyColorTrackingState] = None,
) -> None:
    search_roi = get_current_fishing_search_roi()
    active_roi_text = _format_roi(tracking.active_bobber_roi)
    search_roi_text = _format_roi(search_roi)
    candidate_text = _format_bobber_candidates(tracking.top_candidates)
    detail_parts = [
        part
        for part in (
            reason,
            f"search_roi={search_roi_text}",
            f"active_bobber_roi={active_roi_text}",
            f"candidates={candidate_text}",
        )
        if part
    ]
    update_ready_debug_snapshot(
        status=tracking.search_status,
        roi_type=tracking.search_status,
        scale=1.0,
        score=score,
        threshold=threshold,
        pos=pos or tracking.active_bobber_center,
        skip_reason="; ".join(detail_parts) if detail_parts else None,
        roi=tracking.active_bobber_roi,
        search_roi=search_roi,
        active_bobber_roi=tracking.active_bobber_roi,
        crop_bgr=crop_bgr,
        elapsed_text=elapsed_text,
        candidates=build_bobber_debug_candidates(tracking.top_candidates),
        ready_color=build_ready_color_debug(color_state) if color_state is not None else None,
    )


def reacquire_or_fallback_bobber(
    tracking: BobberTrackingState,
    base_x: int,
    base_y: int,
) -> Optional[np.ndarray]:
    bounds = get_local_window_bounds()
    search_roi: tuple[int, int, int, int]
    active_bobber_roi = tracking.active_bobber_roi

    if active_bobber_roi is not None:
        set_bobber_search_status(
            tracking,
            "reacquire",
            "[bobber] tracking lost, reacquire",
        )
        search_roi = expand_roi(
            active_bobber_roi,
            config.CONFIG.bobber_reacquire_padding,
            bounds,
        )
    else:
        set_bobber_search_status(
            tracking,
            "wide search",
            "[bobber] wide search start",
        )
        wide_search_roi = build_bobber_wide_search_roi()
        if wide_search_roi is None:
            log_warn("[fishing] search ROI is not selected. Press PageUp and drag fishing area first.")
            return None
        search_roi = wide_search_roi

    selected, candidates, frame_bgr = search_bobber_in_roi(search_roi, tracking.active_bobber_center)
    tracking.top_candidates = candidates
    if selected is not None:
        update_bobber_tracking_from_candidate(tracking, selected)
        log_info(
            f"[bobber] candidate selected score={selected.score:.2f} center={selected.center} roi={tracking.active_bobber_roi}"
        )
        log_dim("[bobber] tracking roi updated")
        return frame_bgr

    tracking.bobber_reacquire_fail_count += 1
    if (
        tracking.active_bobber_roi is not None
        and tracking.bobber_reacquire_fail_count >= config.CONFIG.bobber_reacquire_fail_limit
    ):
        log_warn("[bobber] reacquire failed, fallback to wide search")
        tracking.active_bobber_roi = None
        tracking.active_bobber_center = None
        tracking.bobber_track_fail_count = 0
        tracking.bobber_reacquire_fail_count = 0
        tracking.search_status = "wide search"

    return frame_bgr


def loot_items(
    template: TemplateImage,
    threshold: float,
    roi: tuple[int, int, int, int] | None,
) -> bool:
    if should_stop():
        log_warn("[STOP] 현재 사이클 종료")
        return False

    if not wait_target_window():
        log_warn("[STOP] 현재 사이클 종료")
        return False

    character_x, character_y = get_character_reference_point()

    log_info(
        f"[LOOT] R 키 입력 시작: base=({character_x}, {character_y}), count={config.CONFIG.loot_key_press_count}"
    )

    for press_index in range(config.CONFIG.loot_key_press_count):
        if should_stop():
            log_warn("[STOP] 줍기 중단")
            return False

        if not wait_target_window():
            log_warn("[STOP] 줍기 중단")
            return False

        if config.CONFIG.ready_multi_scale_enabled:
            result = detect_ready_icon_multi_scale(template, threshold, roi)
        else:
            result = detect_ready_icon(template, threshold, roi)
        if result.found:
            log_success(
                f"[READY] 줍기 중 감지 scale={result.scale:.2f} score={result.score:.3f} center={result.screen_center} threshold={config.CONFIG.ready_threshold:.3f}"
            )
            add_user_log("입질 감지", "bite")
            return True

        press_key(
            config.CONFIG.loot_key,
            dry_run=config.CONFIG.dry_run,
        )
        log_dim(f"[LOOT] press {config.CONFIG.loot_key.upper()} ({press_index + 1}/{config.CONFIG.loot_key_press_count})")

        if config.CONFIG.loot_key_interval > 0:
            time.sleep(config.CONFIG.loot_key_interval)

    log_info("[LOOT] R 키 입력 완료")
    add_user_log("아이템 확인 완료", "loot")
    return False


def is_point_in_region(
    point: tuple[int, int],
    region: tuple[int, int, int, int],
) -> bool:
    x, y = point
    left, top, width, height = region
    return left <= x <= left + width and top <= y <= top + height


def point_in_roi(
    point: tuple[int, int],
    roi: tuple[int, int, int, int],
) -> bool:
    return is_point_in_region(point, roi)


def get_ready_candidate_crop(
    screen_bgr: np.ndarray,
    roi_top_left: Optional[tuple[int, int]],
    template: TemplateImage,
    scale: float,
) -> Optional[np.ndarray]:
    if roi_top_left is None:
        return None

    x, y = roi_top_left
    width = max(1, int(template.width * scale))
    height = max(1, int(template.height * scale))
    pad_x = max(10, width // 2)
    pad_y = max(8, height // 2)

    left = max(0, x - pad_x)
    top = max(0, y - pad_y)
    right = min(screen_bgr.shape[1], x + width + pad_x)
    bottom = min(screen_bgr.shape[0], y + height + pad_y)
    if right <= left or bottom <= top:
        return None

    return screen_bgr[top:bottom, left:right]


def is_green_bar_like_region(crop: Optional[np.ndarray]) -> bool:
    if crop is None or crop.size == 0:
        return False

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lower = np.array((65, 70, 80), dtype=np.uint8)
    upper = np.array((110, 255, 255), dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    green_ratio = float(cv2.countNonZero(mask)) / float(mask.size)
    if green_ratio < 0.08:
        return False

    row_ratios = np.count_nonzero(mask, axis=1) / max(1, mask.shape[1])
    max_row_ratio = float(np.max(row_ratios)) if row_ratios.size else 0.0
    if max_row_ratio < 0.35:
        return False

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, mask.shape[1] // 4), 2))
    connected = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, horizontal_kernel)
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        _, _, width, height = cv2.boundingRect(contour)
        if width >= max(24, int(mask.shape[1] * 0.45)) and width >= height * 3:
            return True

    return False


def get_ready_candidate_skip_reason(
    screen_center: tuple[int, int],
    roi_type: str,
    candidate_roi: tuple[int, int, int, int],
    character_full_roi: tuple[int, int, int, int] | None,
    character_center: Optional[tuple[int, int]] = None,
) -> Optional[str]:
    if screen_center[1] < config.CONFIG.ready_min_screen_y:
        return f"screen_y_below_min({screen_center[1]}<{config.CONFIG.ready_min_screen_y})"

    if (
        character_center is not None
        and screen_center[1] > character_center[1] + config.CONFIG.ready_max_below_character_y
    ):
        return f"below_character pos={screen_center} char={character_center}"

    if roi_type in ("character_fast", "character_full") and not point_in_roi(screen_center, candidate_roi):
        return "outside_candidate_roi"

    if roi_type not in ("default", "character_fast", "character_full"):
        return "non_character_roi_disabled"

    if (
        roi_type != "default"
        and character_full_roi is not None
        and not point_in_roi(screen_center, character_full_roi)
    ):
        return "outside_character_full"

    return None


def log_ready_candidate_skipped(
    roi_type: str,
    reason: str,
    score: float,
    screen_center: tuple[int, int],
) -> None:
    if config.CONFIG.ready_debug_log:
        log_dim(
            f"[READY] candidate skipped roi={roi_type} reason={reason} score={score:.3f} pos={screen_center}"
        )


def is_ready_ignored(screen_center: tuple[int, int] | None) -> bool:
    if screen_center is None:
        return False
    width, height = get_local_window_size()
    ratio_regions = [
        clamp_roi_to_window(
            (
                int(width * left_ratio),
                int(height * top_ratio),
                int(width * width_ratio),
                int(height * height_ratio),
            ),
            (width, height),
        )
        for left_ratio, top_ratio, width_ratio, height_ratio in config.CONFIG.ready_ignore_region_ratios
    ]
    configured_regions = [
        clamp_roi_to_window(region, (width, height))
        for region in config.CONFIG.ready_ignore_regions
    ]
    return any(
        is_point_in_region(screen_center, region)
        for region in configured_regions + ratio_regions
    )


def get_character_reference_point() -> tuple[int, int]:
    width, height = get_local_window_size()
    character_x = int(width * config.CONFIG.loot_center_ratio_x)
    character_y = int(height * config.CONFIG.loot_center_ratio_y)
    return character_x, character_y


def get_pickup_area_center() -> tuple[int, int]:
    character_x, character_y = get_character_reference_point()
    return (
        character_x + config.CONFIG.pickup_area_offset_x,
        character_y + config.CONFIG.pickup_area_offset_y,
    )


def get_random_point_in_pickup_area(center_x: int, center_y: int) -> tuple[int, int]:
    angle = random.uniform(0.0, math.tau)
    radius = math.sqrt(random.random())

    x = center_x + int(math.cos(angle) * config.CONFIG.pickup_random_radius_x * radius)
    y = center_y + int(math.sin(angle) * config.CONFIG.pickup_random_radius_y * radius)

    return x, y


def click_pickup_probe(base_x: int, base_y: int, is_first: bool) -> None:
    """Probe for items by moving cursor to random position and pressing R key."""
    pickup_center_x = base_x + config.CONFIG.pickup_area_offset_x
    pickup_center_y = base_y + config.CONFIG.pickup_area_offset_y

    if is_first and config.CONFIG.pickup_random_first_center:
        x, y = pickup_center_x, pickup_center_y
    else:
        x, y = get_random_point_in_pickup_area(pickup_center_x, pickup_center_y)

    refresh_diablo_window_rect(log_missing=False)
    move_point_screen = local_point_to_screen((x, y))
    move_point(
        move_point_screen[0],
        move_point_screen[1],
        move_duration=0.0,
        dry_run=config.CONFIG.dry_run,
    )
    
    # Press R key to attempt pickup
    press_key(
        config.CONFIG.loot_key,
        dry_run=config.CONFIG.dry_run,
    )
    
    if config.CONFIG.ready_debug_log:
        log_dim(
            f"[PICKUP] move local=({x}, {y}) screen={move_point_screen} press {config.CONFIG.loot_key.upper()}"
        )


def wait_target_window() -> bool:
    last_window_log_at = 0.0
    warned_once = False
    while True:
        if should_stop():
            log_warn("[STOP] 현재 사이클 종료")
            return False

        active_title = get_active_window_title()
        rect = refresh_diablo_window_rect(log_missing=False)
        if is_target_window_active(config.CONFIG.window_keywords) and rect is not None:
            return True

        now = time.time()
        if now - last_window_log_at >= config.CONFIG.window_wait_log_interval_seconds:
            if rect is None:
                log_warn("[WAIT] Diablo IV 창을 찾을 수 없거나 최소화/숨김 상태입니다")
            else:
                log_warn(f"[WAIT] Diablo IV 창 활성화 대기: 현재 창={active_title!r}")
            last_window_log_at = now
            if not warned_once:
                add_user_log("게임 화면 확인 필요", "warning")
                warned_once = True
        time.sleep(0.2)


def wait_ready_icon(
    template: TemplateImage,
    threshold: float,
    default_ready_roi: tuple[int, int, int, int] | None,
    active_bobber_roi: tuple[int, int, int, int] | None,
) -> ReadyWaitResult:
    wait_started_at = time.monotonic()
    bobber_tracking = reset_bobber_tracking_state()
    if active_bobber_roi is not None:
        left, top, width, height = active_bobber_roi
        bobber_tracking.active_bobber_roi = active_bobber_roi
        bobber_tracking.active_bobber_center = (left + width // 2, top + height // 2)
        bobber_tracking.last_bobber_seen_at = wait_started_at
        bobber_tracking.search_status = "tracking"
        log_info(f"[bobber] candidate selected score=initial center={bobber_tracking.active_bobber_center} roi={active_bobber_roi}")
    else:
        log_info("[bobber] wide search start")

    last_ready_wait_log_at = 0.0
    last_ready_notice_at = 0.0
    pickup_probe_started = False
    last_pickup_probe_at = 0.0
    pickup_base_x, pickup_base_y = get_character_reference_point()
    pending_candidate: Optional[ReadyCandidate] = None
    ready_color_state = ReadyColorTrackingState()

    if config.CONFIG.pickup_random_enabled:
        pickup_center_x, pickup_center_y = get_pickup_area_center()
        log_dim(f"[PICKUP] 탐색 중심=({pickup_center_x}, {pickup_center_y})")

    log_info("[READY] 감지 전용 내부 루프 진입")
    log_info(
        f"[READY] wait start timeout={config.CONFIG.bobber_search_timeout_sec:.1f}s fps={config.CONFIG.ready_scan_fps:.1f}"
    )
    log_dim(
        f"[READY] scan fps={config.CONFIG.ready_scan_fps:.1f} interval={config.CONFIG.ready_poll_interval:.3f}s"
    )
    log_dim(
        f"[READY DEBUG] overlay fps={config.CONFIG.ready_debug_overlay_fps:.1f} interval={config.CONFIG.ready_debug_overlay_interval:.3f}s"
    )

    while True:
        scan_started_at = time.monotonic()
        elapsed_wait = scan_started_at - wait_started_at
        wait_timeout = config.CONFIG.bobber_search_timeout_sec
        if elapsed_wait >= wait_timeout:
            log_warn(
                f"[READY] timeout after {elapsed_wait:.1f}s, no bite detected"
            )
            bobber_tracking.search_status = "timeout"
            update_ready_debug_snapshot(
                status="timeout",
                roi_type=_format_roi(bobber_tracking.active_bobber_roi),
                scale=0.0,
                score=0.0,
                threshold=threshold,
                pos=bobber_tracking.active_bobber_center,
                skip_reason=f"ready_wait_timeout elapsed={elapsed_wait:.1f}s",
                roi=bobber_tracking.active_bobber_roi,
                search_roi=get_current_fishing_search_roi(),
                active_bobber_roi=bobber_tracking.active_bobber_roi,
                elapsed_text=f"{elapsed_wait:.1f}s / {wait_timeout:.1f}s",
                ready_color=build_ready_color_debug(ready_color_state),
            )
            return ReadyWaitResult(status="timeout", elapsed=elapsed_wait)

        if should_stop():
            log_warn("[STOP] 현재 사이클 종료")
            return ReadyWaitResult(status="cancelled", elapsed=elapsed_wait)

        if not wait_target_window():
            return ReadyWaitResult(status="cancelled", elapsed=elapsed_wait)

        ready_base_x, ready_base_y = get_character_reference_point()
        elapsed_text = f"{time.monotonic() - wait_started_at:.1f}s / {wait_timeout:.1f}s"

        tracking_frame: Optional[np.ndarray] = None
        if bobber_tracking.active_bobber_roi is None:
            tracking_frame = reacquire_or_fallback_bobber(
                bobber_tracking,
                ready_base_x,
                ready_base_y,
            )

        active_bobber_roi = bobber_tracking.active_bobber_roi
        if active_bobber_roi is not None:
            if config.CONFIG.ready_multi_scale_enabled:
                result = detect_ready_icon_multi_scale(template, threshold, active_bobber_roi)
            else:
                result = detect_ready_icon(template, threshold, active_bobber_roi)
            result = ReadyDetectionResult(
                found=result.found,
                score=result.score,
                score_passed=result.score_passed,
                ignored=result.ignored,
                roi_top_left=result.roi_top_left,
                screen_center=result.screen_center,
                screen_bgr=result.screen_bgr,
                scale=result.scale,
                roi_type="bobber",
            )
            tracking_frame = result.screen_bgr

            selected, candidates, _ = search_bobber_in_roi(
                active_bobber_roi,
                bobber_tracking.active_bobber_center,
            )
            bobber_tracking.top_candidates = candidates
            if selected is not None:
                update_bobber_tracking_from_candidate(bobber_tracking, selected)
                if config.CONFIG.bobber_debug_log:
                    log_dim("[bobber] tracking roi updated")
            elif not result.found:
                bobber_tracking.bobber_track_fail_count += 1
                if bobber_tracking.bobber_track_fail_count >= config.CONFIG.bobber_track_fail_limit:
                    tracking_frame = reacquire_or_fallback_bobber(
                        bobber_tracking,
                        ready_base_x,
                        ready_base_y,
                    )

            if not result.found:
                update_bobber_debug_overlay(
                    bobber_tracking,
                    threshold=threshold,
                    score=result.score,
                    pos=result.screen_center,
                    crop_bgr=tracking_frame,
                    elapsed_text=elapsed_text,
                    reason="low_score",
                    color_state=ready_color_state,
                )
        elif config.CONFIG.ready_adaptive_search_enabled:
            result = detect_ready_icon_adaptive(
                template,
                threshold,
                default_ready_roi,
                None,
                ready_base_x,
                ready_base_y,
                elapsed_text=elapsed_text,
            )
        else:
            result = detect_ready_icon(
                template,
                threshold,
                default_ready_roi,
            )
            if not result.found:
                update_ready_debug_snapshot(
                    status="scanning",
                    roi_type=result.roi_type,
                    scale=result.scale,
                    score=result.score,
                    threshold=threshold,
                    pos=result.screen_center,
                    skip_reason="low_score",
                    crop_bgr=result.screen_bgr,
                    elapsed_text=elapsed_text,
                    ready_color=build_ready_color_debug(ready_color_state),
                )

        color_blob, color_frame = detect_ready_color_candidate(ready_color_state)
        if (
            not result.found
            and color_blob is not None
            and ready_color_state.hit_frames >= config.CONFIG.ready_color_min_frames
        ):
            search_roi = get_current_fishing_search_roi()
            roi_top_left = None
            if search_roi is not None:
                roi_top_left = (
                    color_blob.bbox[0] - search_roi[0],
                    color_blob.bbox[1] - search_roi[1],
                )
            result = ReadyDetectionResult(
                found=not is_ready_ignored(color_blob.center),
                score=color_blob.score,
                score_passed=True,
                ignored=is_ready_ignored(color_blob.center),
                roi_top_left=roi_top_left,
                screen_center=color_blob.center,
                screen_bgr=color_frame if color_frame is not None else np.zeros((8, 8, 3), dtype=np.uint8),
                scale=1.0,
                roi_type="ready_color",
            )
            update_ready_debug_snapshot(
                status="ready_color",
                roi_type="ready_color",
                scale=1.0,
                score=color_blob.score,
                threshold=threshold,
                pos=color_blob.center,
                skip_reason=f"ready_color_blob={color_blob.bbox} area={color_blob.area:.1f}",
                roi=color_blob.bbox,
                search_roi=get_current_fishing_search_roi(),
                active_bobber_roi=bobber_tracking.active_bobber_roi,
                crop_bgr=color_frame,
                elapsed_text=elapsed_text,
                ready_color=build_ready_color_debug(ready_color_state),
            )

        now = time.monotonic()
        
        if result.found:
            screen_center = result.screen_center
            bobber_tracking.search_status = "bite detected"
            if screen_center is None:
                log_dim("[READY] candidate skipped reason=no_screen_center")
                continue

            selected_candidate = BobberCandidate(center=screen_center, area=0, score=result.score)
            update_bobber_tracking_from_candidate(
                bobber_tracking,
                selected_candidate,
                status="bite detected",
            )
            update_bobber_debug_overlay(
                bobber_tracking,
                threshold=threshold,
                score=result.score,
                pos=screen_center,
                crop_bgr=get_ready_candidate_crop(
                    result.screen_bgr,
                    result.roi_top_left,
                    template,
                    result.scale,
                ),
                elapsed_text=elapsed_text,
                reason="bite detected",
                color_state=ready_color_state,
            )

            if not config.CONFIG.ready_confirm_enabled:
                roi_type = result.roi_type
                if roi_type not in ("bobber", "ready_color") and roi_type not in config.CONFIG.ready_single_confirm_rois:
                    log_dim(
                        f"[READY] candidate skipped roi={roi_type} reason={'last_roi_disabled' if roi_type == 'last' else 'non_confirm_roi'} score={result.score:.3f} pos={screen_center}"
                    )
                    continue

                if config.CONFIG.ready_quick_confirm_enabled:
                    time.sleep(config.CONFIG.ready_quick_confirm_delay)
                    confirm_base_x, confirm_base_y = get_character_reference_point()
                    confirm_result = detect_ready_icon_adaptive(
                        template,
                        threshold,
                        default_ready_roi,
                        bobber_tracking.active_bobber_roi,
                        confirm_base_x,
                        confirm_base_y,
                    )
                    failure_reason = get_ready_quick_confirm_failure_reason(result, confirm_result)
                    if failure_reason is not None:
                        log_dim(f"[READY] quick confirm failed reason={failure_reason}")
                        continue

                    log_success(
                        f"[READY] quick confirm success roi={confirm_result.roi_type} scale={confirm_result.scale:.2f} score={confirm_result.score:.3f} pos={confirm_result.screen_center}"
                    )
                    update_ready_debug_snapshot(
                        status="confirmed",
                        roi_type=confirm_result.roi_type,
                        scale=confirm_result.scale,
                        score=confirm_result.score,
                        threshold=config.CONFIG.ready_threshold,
                        pos=confirm_result.screen_center,
                        crop_bgr=get_ready_candidate_crop(
                            confirm_result.screen_bgr,
                            confirm_result.roi_top_left,
                            template,
                            confirm_result.scale,
                        ),
                        ready_color=build_ready_color_debug(ready_color_state),
                    )
                    add_user_log("입질 감지", "bite")
                    save_ready_debug_crop(
                        confirm_result.screen_bgr,
                        confirm_result.roi_top_left,
                        template,
                        confirm_result.score,
                        prefix=f"ready_match_roi_{confirm_result.roi_type}_scale_{confirm_result.scale:.2f}",
                    )
                    return ReadyWaitResult(
                        status="detected",
                        detection=confirm_result,
                        elapsed=time.monotonic() - wait_started_at,
                    )

                log_success(
                    f"[READY] immediate confirm roi={roi_type} scale={result.scale:.2f} score={result.score:.3f} pos={screen_center}"
                )
                update_ready_debug_snapshot(
                    status="confirmed",
                    roi_type=roi_type,
                    scale=result.scale,
                    score=result.score,
                    threshold=config.CONFIG.ready_threshold,
                    pos=screen_center,
                    crop_bgr=get_ready_candidate_crop(
                        result.screen_bgr,
                        result.roi_top_left,
                        template,
                        result.scale,
                    ),
                    ready_color=build_ready_color_debug(ready_color_state),
                )
                add_user_log("입질 감지", "bite")
                save_ready_debug_crop(
                    result.screen_bgr,
                    result.roi_top_left,
                    template,
                    result.score,
                    prefix=f"ready_match_roi_{roi_type}_scale_{result.scale:.2f}",
                )
                return ReadyWaitResult(
                    status="detected",
                    detection=result,
                    elapsed=time.monotonic() - wait_started_at,
                )

            # --- 2-frame confirmation mode (ready_confirm_enabled = True) ---
            
            # Check for instant confirmation (high score + good scale)
            if is_ready_instant_confirm(result.score, result.scale, result.roi_type):
                log_success(
                    f"[READY] 즉시 확정 roi={result.roi_type} scale={result.scale:.2f} score={result.score:.3f} pos={screen_center}"
                )
                add_user_log("입질 감지", "bite")
                save_ready_debug_crop(
                    result.screen_bgr,
                    result.roi_top_left,
                    template,
                    result.score,
                    prefix=f"ready_match_roi_{result.roi_type}_scale_{result.scale:.2f}",
                )
                return ReadyWaitResult(
                    status="detected",
                    detection=result,
                    elapsed=time.monotonic() - wait_started_at,
                )
            
            # Check pending candidate for 2-frame confirmation
            if pending_candidate is not None:
                time_since_pending = now - pending_candidate.seen_at
                if time_since_pending <= config.CONFIG.ready_confirm_timeout:
                    if is_ready_distance_acceptable(
                        pending_candidate.center,
                        screen_center,
                        config.CONFIG.ready_confirm_max_distance,
                    ):
                        log_success(
                            f"[READY] 확정(2회) roi={result.roi_type} scale={result.scale:.2f} score={result.score:.3f} pos={screen_center}"
                        )
                        add_user_log("입질 감지", "bite")
                        save_ready_debug_crop(
                            result.screen_bgr,
                            result.roi_top_left,
                            template,
                            result.score,
                            prefix=f"ready_match_roi_{result.roi_type}_scale_{result.scale:.2f}",
                        )
                        return ReadyWaitResult(
                            status="detected",
                            detection=result,
                            elapsed=time.monotonic() - wait_started_at,
                        )
            
            # No confirmation yet - save as pending
            log_dim(
                f"[READY] 후보(1/2) roi={result.roi_type} scale={result.scale:.2f} score={result.score:.3f} pos={screen_center}"
            )
            pending_candidate = ReadyCandidate(
                center=screen_center,
                score=result.score,
                scale=result.scale,
                roi_type=result.roi_type,
                seen_at=now,
            )
        else:
            # Not found - check if pending expired
            if pending_candidate is not None:
                time_since_pending = now - pending_candidate.seen_at
                if time_since_pending > config.CONFIG.ready_confirm_timeout:
                    log_dim(
                        f"[READY] 후보 타임아웃 ({time_since_pending:.2f}s > {config.CONFIG.ready_confirm_timeout}s)"
                    )
                    pending_candidate = None

        if result.ignored:
            log_dim(f"[READY-IGNORE] score={result.score:.3f} center={result.screen_center}")
            save_ready_debug_crop(
                result.screen_bgr,
                result.roi_top_left,
                template,
                result.score,
                prefix="ready_ignored",
            )

        if config.CONFIG.pickup_random_enabled and (
            not pickup_probe_started
            or now - last_pickup_probe_at >= config.CONFIG.pickup_random_click_interval
        ):
            click_pickup_probe(
                pickup_base_x,
                pickup_base_y,
                is_first=not pickup_probe_started,
            )
            pickup_probe_started = True
            last_pickup_probe_at = now

        if now - last_ready_wait_log_at >= config.CONFIG.ready_wait_log_interval_seconds:
            pending_status = f"pending={'있음' if pending_candidate else '없음'}"
            log_dim(
                f"[READY] 대기 중 state={bobber_tracking.search_status} score={result.score:.3f} threshold={config.CONFIG.ready_threshold:.3f} {pending_status}"
            )
            last_ready_wait_log_at = now

        if now - last_ready_notice_at >= config.CONFIG.max_wait_ready_seconds:
            save_ready_roi_debug_image(result.screen_bgr, result.score)
            last_ready_notice_at = now

        elapsed = time.monotonic() - scan_started_at
        sleep_for = max(0.0, config.CONFIG.ready_poll_interval - elapsed)
        time.sleep(sleep_for)


def run_fishing_cycle(session: FishingSession) -> FishingCycleResult:
    global last_start_icon_pos
    start_template = session.start_template
    ready_template = session.ready_template
    default_ready_roi_local = session.default_ready_roi_local

    if not wait_target_window():
        return FishingCycleResult.CANCELLED

    if should_stop():
        return FishingCycleResult.CANCELLED

    active_title = get_active_window_title()

    log_success(f"[WINDOW] Diablo IV active title={active_title!r}")
    log_info("[CAST] start key press")
    press_key(config.CONFIG.initial_key, dry_run=config.CONFIG.dry_run)
    start_result = detect_start_icon(start_template)
    if not start_result.found:
        log_dim(f"[CAST] 대기 score={start_result.score:.3f}")
        add_user_log("낚시 시작 지점 확인 중", "wait")
        time.sleep(config.CONFIG.detect_interval_seconds)
        return FishingCycleResult.RETRY

    if should_stop():
        return FishingCycleResult.CANCELLED

    if not is_target_window_active(config.CONFIG.window_keywords):
        log_warn(
            f"[WAIT] Diablo IV 창 활성화 대기: 현재 창={get_active_window_title()!r}"
        )
        return FishingCycleResult.CANCELLED if should_stop() else FishingCycleResult.RETRY

    icon_center_local = get_detection_screen_center(start_result)
    if icon_center_local is None:
        log_dim("[CAST] start icon skipped reason=no_local_center")
        time.sleep(config.CONFIG.detect_interval_seconds)
        return FishingCycleResult.RETRY

    refresh_diablo_window_rect(log_missing=False)
    click_point_screen = local_point_to_screen(icon_center_local)
    log_info(f"[CAST] click start local={icon_center_local} screen={click_point_screen}")
    click_point(
        click_point_screen[0],
        click_point_screen[1],
        move_duration=config.CONFIG.mouse_move_duration,
        dry_run=config.CONFIG.dry_run,
    )
    last_start_icon_pos = icon_center_local
    increment_cast_count()
    add_user_log("낚싯줄 던짐", "cast")
    active_bobber_roi: tuple[int, int, int, int] | None = None

    log_dim(
        f"[WAIT] START 클릭 후 줍기 전 대기 {config.CONFIG.after_start_click_before_loot_delay_seconds:.1f}초"
    )
    time.sleep(config.CONFIG.after_start_click_before_loot_delay_seconds)

    if should_stop():
        return FishingCycleResult.CANCELLED

    log_info("[LOOT] START 클릭 후 아이템 줍기")
    add_user_log("떨어진 아이템 확인 중", "loot")
    loot_ready_roi: tuple[int, int, int, int] | None = (
        active_bobber_roi if config.CONFIG.ready_use_bobber_roi else default_ready_roi_local
    )
    ready_found_during_loot = loot_items(
        ready_template,
        config.CONFIG.ready_threshold,
        loot_ready_roi,
    )

    if should_stop():
        return FishingCycleResult.CANCELLED

    if ready_found_during_loot:
        log_info(f"[REEL] {config.CONFIG.reel_key.upper()} 입력 (ready during loot)")
        increment_catch_count()
        add_user_log("낚아올리는 중", "catch")
        press_key(config.CONFIG.reel_key, dry_run=config.CONFIG.dry_run)
        log_dim(
            f"[WAIT] 물고기 잡힘/드롭 대기 {config.CONFIG.after_reel_delay_seconds:.1f}초"
        )
        time.sleep(config.CONFIG.after_reel_delay_seconds)

        if should_stop():
            return FishingCycleResult.CANCELLED

        add_user_log("다음 낚시 준비 중", "next")
        log_dim(
            f"[WAIT] 다음 낚시 전 안정화 대기 {config.CONFIG.after_scroll_delay_seconds:.1f}초"
        )
        time.sleep(config.CONFIG.after_scroll_delay_seconds)
        return FishingCycleResult.SUCCESS

    active_bobber_roi = locate_active_bobber_roi_in_current_search_area()

    log_dim(
        f"[WAIT] 줍기 후 ready 감지 전 대기 {config.CONFIG.after_loot_before_ready_delay_seconds:.1f}초"
    )
    time.sleep(config.CONFIG.after_loot_before_ready_delay_seconds)

    if should_stop():
        return FishingCycleResult.CANCELLED

    log_info("[READY] wait start")
    add_user_log("물고기 기다리는 중", "wait")
    ready_wait_result = wait_ready_icon(
        ready_template,
        config.CONFIG.ready_threshold,
        default_ready_roi_local,
        active_bobber_roi,
    )

    if ready_wait_result.status == "cancelled":
        log_warn("[STOP] 현재 사이클 종료")
        clear_ready_debug_snapshot()
        return FishingCycleResult.CANCELLED

    if ready_wait_result.status == "timeout":
        return FishingCycleResult.TIMEOUT

    ready_result = ready_wait_result.detection
    if ready_result is None:
        log_warn("[STOP] 현재 사이클 종료")
        clear_ready_debug_snapshot()
        return FishingCycleResult.FAILED

    if not wait_target_window():
        return FishingCycleResult.CANCELLED

    log_info(f"[REEL] {config.CONFIG.reel_key.upper()} 입력")
    increment_catch_count()
    add_user_log("낚아올리는 중", "catch")
    press_key(config.CONFIG.reel_key, dry_run=config.CONFIG.dry_run)
    clear_ready_debug_snapshot()

    log_dim(f"[WAIT] 물고기 잡힘/드롭 대기 {config.CONFIG.after_reel_delay_seconds:.1f}초")
    time.sleep(config.CONFIG.after_reel_delay_seconds)

    if should_stop():
        return FishingCycleResult.CANCELLED

    add_user_log("다음 낚시 준비 중", "next")
    log_dim(f"[WAIT] 다음 낚시 전 안정화 대기 {config.CONFIG.after_scroll_delay_seconds:.1f}초")
    time.sleep(config.CONFIG.after_scroll_delay_seconds)
    return FishingCycleResult.SUCCESS


def handle_fishing_cycle_timeout() -> None:
    if config.CONFIG.ready_timeout_reel_enabled:
        log_warn("[fishing] bite timeout 30s, pull and recast")
        log_info(f"[REEL] timeout recovery {config.CONFIG.reel_key.upper()} 입력")
        press_key(config.CONFIG.reel_key, dry_run=config.CONFIG.dry_run)
        log_dim(
            f"[WAIT] timeout recovery wait {config.CONFIG.ready_timeout_reel_wait:.1f}s"
        )
        time.sleep(config.CONFIG.ready_timeout_reel_wait)

    log_warn("[CAST] timeout recovered, next cast")
    add_user_log("입질 없음, 낚싯대 회수", "wait")
    log_dim(
        f"[WAIT] timeout 후 안정화 대기 {config.CONFIG.next_cast_stabilize_after_timeout:.1f}초"
    )
    time.sleep(config.CONFIG.next_cast_stabilize_after_timeout)


def start_fishing_runtime(
    *,
    enable_overlay: bool = True,
    wait_for_start_hotkey: bool = True,
) -> None:
    global overlay_controller, start_hotkey_enabled, clear_roi_on_stop
    start_hotkey_enabled = wait_for_start_hotkey
    clear_roi_on_stop = wait_for_start_hotkey
    state.running = False
    state.stop_requested = False
    state.idle_announced = False
    load_fishing_stats()
    if enable_overlay:
        overlay_controller = OverlayController()
        overlay_controller.start()
        overlay_controller.update_snapshot(build_overlay_snapshot())
    else:
        overlay_controller = None
    start_hotkey_listener()
    if not wait_for_start_hotkey:
        log_success("[START] 자동 낚시 시작")
        start_session_stats()
        state.running = True
        state.stop_requested = False
        state.idle_announced = False
        add_user_log("낚시 시작", "start")


def create_fishing_session() -> FishingSession:
    ensure_templates()

    start_template = load_template(config.resolve_path(config.CONFIG.start_template_path))
    ready_template = load_template(config.resolve_path(config.CONFIG.ready_template_path))
    refresh_diablo_window_rect(log_missing=False)
    default_ready_roi_local = get_default_ready_roi_local()

    return FishingSession(
        start_template=start_template,
        ready_template=ready_template,
        default_ready_roi_local=default_ready_roi_local,
    )


def shutdown_fishing_runtime(*, clear_roi: bool = True) -> None:
    global overlay_controller, start_hotkey_enabled, clear_roi_on_stop
    request_roi_selection_cancel(True)
    if overlay_controller is not None:
        overlay_controller.cancel_roi_selection()
    if clear_roi:
        clear_current_fishing_search_roi()
    stop_session_stats()
    stop_hotkey_listener()
    state.running = False
    state.stop_requested = True
    state.idle_announced = False
    start_hotkey_enabled = True
    clear_roi_on_stop = True
    _flush_run_time_once()
    save_fishing_stats()
    if overlay_controller is not None:
        overlay_controller.stop()
        overlay_controller = None


def run() -> None:
    start_fishing_runtime()
    try:
        session = create_fishing_session()

        log_info("[BOOT] d4-fishing-watcher")
        log_info("[BOOT] PageUp 시작 / PageDown 중단 / Ctrl+C 종료")
        log_info(
            f"[BOOT] fallback base: {config.CONFIG.fallback_base_width}x{config.CONFIG.fallback_base_height}"
        )
        log_info(
            f"[BOOT] loot center ratio: ({config.CONFIG.loot_center_ratio_x:.2f}, {config.CONFIG.loot_center_ratio_y:.2f})"
        )
        log_dim(f"[BOOT] window keywords: {config.CONFIG.window_keywords}")
        log_dim(f"[BOOT] dry_run: {config.CONFIG.dry_run}")
        log_dim("[BOOT] ready/loot templates and hotkeys loaded")

        while True:
            wait_until_started()

            if should_stop():
                continue

            if get_current_fishing_search_roi() is None:
                log_warn("[fishing] search ROI is not selected. Press PageUp and drag fishing area first.")
                add_user_log("PageUp으로 탐색 영역 먼저 지정", "warning")
                stop_session_stats()
                state.running = False
                state.stop_requested = True
                state.idle_announced = False
                clear_ready_debug_snapshot()
                continue

            clear_ready_debug_snapshot()

            refresh_diablo_window_rect(log_missing=True)
            session.default_ready_roi_local = get_default_ready_roi_local()

            cycle_result = run_fishing_cycle(session)

            if cycle_result is FishingCycleResult.TIMEOUT:
                handle_fishing_cycle_timeout()
                continue

            if cycle_result is FishingCycleResult.CANCELLED:
                continue

            if cycle_result is FishingCycleResult.FAILED:
                continue

            if cycle_result is FishingCycleResult.RETRY:
                continue

            if cycle_result is FishingCycleResult.SUCCESS:
                continue
    except KeyboardInterrupt:
        print("\n[EXIT] interrupted by user")
    except Exception:
        add_user_log("상태 확인 필요", "error")
        raise
    finally:
        shutdown_fishing_runtime()


if __name__ == "__main__":
    run()
