from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from tkinter import ttk
from typing import Callable, Optional

from app.logger import AppLogger, get_today_log_path
from app.settings import (
    AppSettings,
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    validate_hotkey_pair,
)
from app.state import AppStage, RunStatus, WindowStatus
from core.hotkeys import GlobalHotkeyManager, normalize_tk_key
from core.screen import WindowRect
from features.fishing import engine
from features.fishing.worker import FishingWorker

LOGGER = logging.getLogger(__name__)


class WorkerEventType(Enum):
    LOG = auto()
    STATE = auto()
    STEP = auto()
    WINDOW = auto()


@dataclass(frozen=True)
class WorkerEvent:
    event_type: WorkerEventType
    payload: str | RunStatus | AppStage | WindowStatus


class D4FishingWatcherWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("D4 Fishing Watcher")
        self.root.geometry("920x640")
        self.root.minsize(760, 520)

        self.window_status_var = tk.StringVar(value=WindowStatus.NOT_FOUND.value)
        self.window_detail_var = tk.StringVar(value="창 정보 없음")
        self.run_status_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.stage_var = tk.StringVar(value=AppStage.IDLE.value)
        self.roi_status_var = tk.StringVar(value="설정 안 됨")
        self.status_badge_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.detail_var = tk.StringVar(value="시작을 누르면 창 확인 후 감지 영역을 새로 선택합니다.")
        self.last_message_var = tk.StringVar(value="최근 안내 없음")
        self.log_path_var = tk.StringVar(value=str(get_today_log_path()))
        self.hotkey_summary_var = tk.StringVar(value="")

        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()
        self.ui_callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._fishing_worker: Optional[FishingWorker] = None
        self._roi_window: Optional[tk.Toplevel] = None
        self._hotkey_window: Optional[tk.Toplevel] = None
        self._worker_status = RunStatus.IDLE
        self._detected_window_rect: Optional[WindowRect] = None
        self._settings: AppSettings = load_settings()
        self._hotkey_manager = GlobalHotkeyManager(
            on_start=lambda: self._ui_call(self._on_start_hotkey),
            on_stop=lambda: self._ui_call(self._on_stop_hotkey),
        )
        self._closing = False

        self.logger = AppLogger(self._append_log_threadsafe)

        self._build_layout()
        self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
        self._refresh_roi_status()
        self._refresh_hotkey_summary()
        self._register_hotkeys_on_startup()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_worker_events)
        self.logger.info("[BOOT] GUI ready. 메인 윈도우 실행 흐름 준비 완료.")

    def run(self) -> None:
        self.root.mainloop()

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self._configure_styles()

        header = ttk.Frame(self.root, padding=(18, 16, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(header, text="D4 Fishing Watcher", style="Title.TLabel")
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(header, textvariable=self.detail_var, style="Muted.TLabel")
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.status_badge = ttk.Label(
            header,
            textvariable=self.status_badge_var,
            style="Badge.TLabel",
            anchor="center",
            width=18,
        )
        self.status_badge.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))

        dashboard = ttk.Frame(self.root, padding=(18, 6, 18, 8))
        dashboard.grid(row=1, column=0, sticky="ew")
        dashboard.columnconfigure(0, weight=1)
        dashboard.columnconfigure(1, weight=2)

        control_frame = ttk.LabelFrame(dashboard, text="실행 제어", padding=(14, 12))
        control_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(control_frame, text="시작", command=self._on_start)
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.stop_button = ttk.Button(control_frame, text="중지", command=self._on_stop)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.hotkey_button = ttk.Button(
            control_frame,
            text="단축키 설정",
            command=self._open_hotkey_settings_window,
        )
        self.hotkey_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self._create_info_row(control_frame, 2, "현재 상태", self.run_status_var)
        self._create_info_row(control_frame, 3, "현재 단계", self.stage_var)
        self._create_info_row(control_frame, 4, "단축키", self.hotkey_summary_var)
        self._create_info_row(control_frame, 5, "최근 안내", self.last_message_var)

        summary_frame = ttk.LabelFrame(dashboard, text="상태 요약", padding=(14, 12))
        summary_frame.grid(row=0, column=1, sticky="nsew")
        summary_frame.columnconfigure(1, weight=1)

        self._create_info_row(summary_frame, 0, "Diablo IV 창", self.window_status_var)
        self._create_info_row(summary_frame, 1, "창 위치/크기", self.window_detail_var)
        self._create_info_row(summary_frame, 2, "감지 영역", self.roi_status_var)
        self._create_info_row(summary_frame, 3, "로그 파일", self.log_path_var)

        log_frame = ttk.LabelFrame(self.root, text="실시간 로그", padding=(12, 10))
        log_frame.grid(row=2, column=0, sticky="nsew", padx=18, pady=(4, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=15,
            wrap="word",
            state=tk.DISABLED,
            font=("Consolas", 10),
            relief="solid",
            borderwidth=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(self.root, padding=(18, 0, 18, 12))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text="시작할 때마다 Diablo IV 창을 확인하고 감지 영역을 새로 선택합니다.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", foreground="#5f6368")
        style.configure("Badge.TLabel", padding=(10, 5), font=("Segoe UI", 10, "bold"))
        style.configure("InfoLabel.TLabel", foreground="#5f6368")
        style.configure("InfoValue.TLabel", font=("Segoe UI", 10, "bold"))

    def _create_info_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_text: str,
        value_var: tk.StringVar,
    ) -> None:
        label = ttk.Label(parent, text=label_text, style="InfoLabel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=(10 if row else 0, 0))
        value = ttk.Label(parent, textvariable=value_var, style="InfoValue.TLabel", wraplength=430)
        value.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(10 if row else 0, 0))

    def _register_hotkeys_on_startup(self) -> None:
        try:
            self._hotkey_manager.start(self._settings.hotkeys)
            self.logger.info(
                "[HOTKEY] 전역 단축키 등록 완료 "
                f"시작={self._settings.hotkeys.start_fishing}, 중지={self._settings.hotkeys.stop_fishing}"
            )
        except Exception as exc:
            LOGGER.exception("Failed to register global hotkeys on startup")
            self.logger.error(f"[HOTKEY] 전역 단축키 등록 실패: {exc}")
            self._set_message("단축키 등록에 실패했습니다. 버튼으로 조작할 수 있습니다.")

    def _refresh_hotkey_summary(self) -> None:
        hotkeys = self._settings.hotkeys
        self.hotkey_summary_var.set(f"시작: {hotkeys.start_fishing} / 중지: {hotkeys.stop_fishing}")

    def _open_hotkey_settings_window(self) -> None:
        if self._hotkey_window is not None:
            try:
                if self._hotkey_window.winfo_exists():
                    self._hotkey_window.lift()
                    self._hotkey_window.focus_force()
                    return
            except tk.TclError:
                self._hotkey_window = None

        window = tk.Toplevel(self.root)
        self._hotkey_window = window
        window.title("단축키 설정")
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()

        start_var = tk.StringVar(value=self._settings.hotkeys.start_fishing)
        stop_var = tk.StringVar(value=self._settings.hotkeys.stop_fishing)
        message_var = tk.StringVar(value="변경 버튼을 누른 뒤 사용할 키 하나를 누르세요.")
        capture_target: dict[str, Optional[str]] = {"name": None}

        frame = ttk.Frame(window, padding=(18, 16))
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="낚시 시작").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=start_var, style="InfoValue.TLabel", width=10).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(16, 8),
        )
        ttk.Button(
            frame,
            text="변경",
            command=lambda: begin_capture("start"),
        ).grid(row=0, column=2, sticky="ew")

        ttk.Label(frame, text="낚시 중지").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(frame, textvariable=stop_var, style="InfoValue.TLabel", width=10).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(16, 8),
            pady=(10, 0),
        )
        ttk.Button(
            frame,
            text="변경",
            command=lambda: begin_capture("stop"),
        ).grid(row=1, column=2, sticky="ew", pady=(10, 0))

        ttk.Label(frame, textvariable=message_var, foreground="#5f6368", wraplength=340).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(14, 0),
        )

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        button_frame.columnconfigure(1, weight=1)
        ttk.Button(button_frame, text="기본값 복원", command=lambda: restore_defaults()).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(button_frame, text="취소", command=lambda: close_window()).grid(
            row=0,
            column=2,
            sticky="e",
        )
        ttk.Button(button_frame, text="저장", command=lambda: save_hotkeys()).grid(
            row=0,
            column=3,
            sticky="e",
            padx=(8, 0),
        )

        def begin_capture(target: str) -> None:
            capture_target["name"] = target
            label = "낚시 시작" if target == "start" else "낚시 중지"
            message_var.set(f"{label} 단축키로 사용할 키를 눌러주세요. ESC는 변경 취소입니다.")
            window.focus_force()

        def restore_defaults() -> None:
            capture_target["name"] = None
            start_var.set(DEFAULT_SETTINGS.hotkeys.start_fishing)
            stop_var.set(DEFAULT_SETTINGS.hotkeys.stop_fishing)
            message_var.set("기본값으로 복원했습니다. 저장을 누르면 반영됩니다.")
            self.logger.info("[HOTKEY] 기본값 복원 선택")

        def close_window() -> None:
            self._hotkey_window = None
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()

        def save_hotkeys() -> None:
            hotkeys, error = validate_hotkey_pair(start_var.get(), stop_var.get())
            if hotkeys is None:
                message_var.set(error or "단축키 설정을 확인하세요.")
                return

            previous_settings = self._settings
            next_settings = AppSettings(hotkeys=hotkeys)
            try:
                self._hotkey_manager.register(hotkeys)
            except Exception as exc:
                LOGGER.exception("Failed to register updated global hotkeys")
                message_var.set("전역 단축키 등록에 실패했습니다. 기존 설정을 유지합니다.")
                self.logger.error(f"[HOTKEY] 전역 단축키 등록 실패: {exc}")
                return

            if not save_settings(next_settings):
                try:
                    self._hotkey_manager.register(previous_settings.hotkeys)
                except Exception:
                    LOGGER.exception("Failed to restore previous hotkeys after save failure")
                message_var.set("설정 파일 저장에 실패했습니다. 기존 설정을 유지합니다.")
                self.logger.error("[HOTKEY] 설정 파일 저장 실패")
                return

            self._settings = next_settings
            self._refresh_hotkey_summary()
            self.logger.info(
                f"[HOTKEY] 단축키 저장 완료: 시작={hotkeys.start_fishing}, 중지={hotkeys.stop_fishing}"
            )
            close_window()

        def on_key_press(event) -> str | None:
            target = capture_target["name"]
            if target is None:
                return None

            if event.keysym == "Escape":
                capture_target["name"] = None
                message_var.set("단축키 변경을 취소했습니다.")
                return "break"

            key_name, error = normalize_tk_key(event.keysym, int(event.keycode), int(event.state))
            if key_name is None:
                message_var.set(error or "지원하지 않는 키입니다.")
                return "break"

            if target == "start":
                start_var.set(key_name)
            else:
                stop_var.set(key_name)
            capture_target["name"] = None
            message_var.set(f"{key_name} 키를 선택했습니다. 저장을 누르면 반영됩니다.")
            return "break"

        window.bind("<KeyPress>", on_key_press)
        window.protocol("WM_DELETE_WINDOW", close_window)
        self._center_child_window(window, width=420, height=210)
        window.focus_force()

    def _center_child_window(self, window: tk.Toplevel, *, width: int, height: int) -> None:
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = max(1, self.root.winfo_width())
        root_height = max(1, self.root.winfo_height())
        x = root_x + max(0, (root_width - width) // 2)
        y = root_y + max(0, (root_height - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _on_start(self) -> None:
        if self._worker_status in (
            RunStatus.CHECKING_WINDOW,
            RunStatus.SELECTING_ROI,
            RunStatus.RUNNING,
            RunStatus.STOPPING,
        ):
            LOGGER.debug("Start request ignored in state: %s", self._worker_status.value)
            return

        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self.logger.warning("[START] 이미 실행 중입니다.")
            return

        if self._roi_window is not None:
            self.logger.warning("[ROI] 영역 설정이 이미 진행 중입니다.")
            return

        if worker is not None:
            self._handle_worker_finished()

        engine.clear_current_fishing_search_roi()
        self._refresh_roi_status("설정 안 됨")
        self._set_window_status(WindowStatus.NOT_FOUND, "창 확인 중")
        self._set_runtime_state(RunStatus.CHECKING_WINDOW, AppStage.WINDOW_DETECTION)
        self._set_message("시작 요청: Diablo IV 창을 확인합니다.")
        self.logger.info("[START] 시작 요청")

        thread = threading.Thread(target=self._detect_window_for_start_worker, daemon=True)
        thread.start()

    def _detect_window_for_start_worker(self) -> None:
        try:
            rect = engine.refresh_diablo_window_rect(log_missing=False)
        except Exception as exc:
            LOGGER.exception("Diablo IV window detection failed")
            self._ui_call(lambda exc=exc: self._handle_window_detection_error(exc))
            return

        self._ui_call(lambda rect=rect: self._handle_window_detection_result(rect))

    def _handle_window_detection_result(self, rect: Optional[WindowRect]) -> None:
        if self._closing:
            return

        if rect is None:
            self._detected_window_rect = None
            self._set_window_status(WindowStatus.NOT_FOUND, "창 정보 없음")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._set_message("Diablo IV 창을 찾지 못했습니다.")
            self.logger.warning("[WINDOW] Diablo IV 창을 찾지 못했습니다. 게임 실행 후 다시 시작하세요.")
            return

        self._detected_window_rect = rect
        engine.set_current_window_rect(rect)
        self._set_window_status(WindowStatus.FOUND, self._format_window_rect(rect))
        self._set_runtime_state(RunStatus.SELECTING_ROI, AppStage.ROI_SELECTION)
        self._set_message("감지 영역을 드래그해 선택하세요.")
        self.logger.info(
            "[WINDOW] Diablo IV 창 감지 성공 "
            f"left={rect.left}, top={rect.top}, width={rect.width}, height={rect.height}"
        )
        self._open_roi_selection_window(rect)

    def _handle_window_detection_error(self, exc: Exception) -> None:
        self._detected_window_rect = None
        self._set_window_status(WindowStatus.NOT_FOUND, "창 감지 오류")
        self._set_runtime_state(RunStatus.ERROR, AppStage.ERROR)
        self._set_message("창 감지 중 오류가 발생했습니다.")
        self.logger.error(f"[ERROR] 창 감지 실패: {exc}")

    def _open_roi_selection_window(self, rect: WindowRect) -> None:
        if self._roi_window is not None:
            return

        window = tk.Toplevel(self.root)
        self._roi_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.34)
        window.configure(bg="#111827")
        window.geometry(f"{rect.width}x{rect.height}+{rect.left}+{rect.top}")

        canvas = tk.Canvas(window, bg="#111827", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            rect.width // 2,
            34,
            text="낚시 감지에 사용할 영역을 드래그해 선택하세요.  ESC: 취소",
            fill="#ffffff",
            font=("Malgun Gothic", 15, "bold"),
        )

        selection_rect: dict[str, Optional[int]] = {"id": None}
        drag_start: dict[str, Optional[int]] = {"x": None, "y": None}

        def clamp_point(x: int, y: int) -> tuple[int, int]:
            return max(0, min(rect.width, x)), max(0, min(rect.height, y))

        def normalize_roi(
            start_x: int,
            start_y: int,
            end_x: int,
            end_y: int,
        ) -> tuple[int, int, int, int]:
            start_x, start_y = clamp_point(start_x, start_y)
            end_x, end_y = clamp_point(end_x, end_y)
            left = min(start_x, end_x)
            top = min(start_y, end_y)
            right = max(start_x, end_x)
            bottom = max(start_y, end_y)
            return left, top, right - left, bottom - top

        def on_press(event) -> None:
            start_x, start_y = clamp_point(int(event.x), int(event.y))
            drag_start["x"] = start_x
            drag_start["y"] = start_y
            rect_id = selection_rect["id"]
            if rect_id is not None:
                canvas.delete(rect_id)
            selection_rect["id"] = canvas.create_rectangle(
                start_x,
                start_y,
                start_x,
                start_y,
                outline="#38bdf8",
                width=3,
            )

        def on_drag(event) -> None:
            rect_id = selection_rect["id"]
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if rect_id is None or start_x is None or start_y is None:
                return
            end_x, end_y = clamp_point(int(event.x), int(event.y))
            canvas.coords(rect_id, start_x, start_y, end_x, end_y)

        def on_release(event) -> None:
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if start_x is None or start_y is None:
                self._finish_roi_selection(None)
                return
            end_x, end_y = clamp_point(int(event.x), int(event.y))
            self._finish_roi_selection(normalize_roi(start_x, start_y, end_x, end_y))

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        window.bind("<Escape>", lambda _event=None: self._finish_roi_selection(None))
        window.bind("<ButtonPress-3>", lambda _event=None: self._finish_roi_selection(None))
        window.focus_force()

    def _finish_roi_selection(self, roi: Optional[tuple[int, int, int, int]]) -> None:
        window = self._roi_window
        self._roi_window = None
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError as exc:
                LOGGER.debug("ROI selection window already closed: %s", exc)

        if self._closing:
            return

        if roi is None:
            engine.clear_current_fishing_search_roi()
            self._refresh_roi_status("설정 취소")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._set_message("감지 영역 설정을 취소했습니다.")
            self.logger.warning("[ROI] 감지 영역 설정을 취소했습니다.")
            return

        rect = self._detected_window_rect
        selected_roi = engine.set_fishing_search_roi_local(roi, rect)
        if selected_roi is None:
            engine.clear_current_fishing_search_roi()
            self._refresh_roi_status("잘못된 영역")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._set_message("감지 영역이 너무 작거나 유효하지 않습니다.")
            self.logger.warning("[ROI] 감지 영역 설정에 실패했습니다.")
            return

        x, y, width, height = selected_roi
        self._refresh_roi_status(f"설정 완료: {width} x {height} (x={x}, y={y})")
        self._set_message("감지 영역 설정 완료. Worker를 시작합니다.")
        self.logger.info(f"[ROI] 감지 영역 설정 완료: local x={x} y={y} w={width} h={height}")
        self._start_worker_after_roi()

    def _start_worker_after_roi(self) -> None:
        self._set_runtime_state(RunStatus.RUNNING, AppStage.READY_TO_RUN)
        self.logger.info("[WORKER] 낚시 worker 시작 요청")

        try:
            worker = FishingWorker(
                on_log=self._enqueue_worker_log,
                on_state=self._enqueue_worker_state,
                on_step=self._enqueue_worker_step,
                on_window=self._enqueue_worker_window,
            )
            self._fishing_worker = worker
            started = worker.start()
        except Exception as exc:
            LOGGER.exception("Fishing worker start failed")
            self._fishing_worker = None
            self._set_runtime_state(RunStatus.ERROR, AppStage.ERROR)
            self._set_message("Worker 시작에 실패했습니다.")
            self.logger.error(f"[ERROR] worker 시작 실패: {exc}")
            return

        if not started:
            self._fishing_worker = None
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._set_message("Worker 시작이 거부되었습니다.")
            self.logger.warning("[START] worker 시작이 거부되었습니다.")

    def _on_stop(self) -> None:
        if self._worker_status is RunStatus.SELECTING_ROI and self._roi_window is not None:
            self.logger.info("[HOTKEY] 영역 설정 중지 요청: 선택을 취소합니다.")
            self._finish_roi_selection(None)
            return

        if self._worker_status is RunStatus.CHECKING_WINDOW:
            LOGGER.debug("Stop request ignored during window detection")
            return

        worker = self._fishing_worker
        if worker is None:
            self._set_runtime_state(RunStatus.STOPPED, AppStage.IDLE)
            self._set_message("실행 중인 worker가 없습니다.")
            self.logger.info("[STOP] 실행 중인 worker가 없습니다.")
            return

        if not worker.is_running():
            self._handle_worker_finished()
            return

        if self._worker_status is RunStatus.STOPPING:
            self.logger.warning("[STOP] 이미 중지 요청 중입니다.")
            return

        self._set_runtime_state(RunStatus.STOPPING, AppStage.STOPPING)
        self._set_message("중지 요청을 전달했습니다.")
        self.logger.info("[STOP] 낚시 worker 중지 요청")
        worker.stop()

    def _on_start_hotkey(self) -> None:
        if self._closing:
            return
        if self._hotkey_window is not None:
            LOGGER.debug("Start hotkey ignored while settings window is open")
            return
        if self._worker_status not in (RunStatus.IDLE, RunStatus.STOPPED, RunStatus.ERROR):
            LOGGER.debug("Start hotkey ignored in state: %s", self._worker_status.value)
            return
        self._show_main_window_for_hotkey()
        self.logger.info(f"[HOTKEY] 시작 단축키 입력: {self._settings.hotkeys.start_fishing}")
        self._on_start()

    def _on_stop_hotkey(self) -> None:
        if self._closing:
            return
        if self._hotkey_window is not None:
            LOGGER.debug("Stop hotkey ignored while settings window is open")
            return
        if self._worker_status not in (RunStatus.RUNNING, RunStatus.SELECTING_ROI, RunStatus.CHECKING_WINDOW):
            LOGGER.debug("Stop hotkey ignored in state: %s", self._worker_status.value)
            return
        self.logger.info(f"[HOTKEY] 중지 단축키 입력: {self._settings.hotkeys.stop_fishing}")
        self._on_stop()

    def _show_main_window_for_hotkey(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            LOGGER.debug("Failed to raise main window for hotkey", exc_info=True)

    def _enqueue_worker_log(self, message: str) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.LOG, message))

    def _enqueue_worker_state(self, status: RunStatus) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.STATE, status))

    def _enqueue_worker_step(self, stage: AppStage) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.STEP, stage))

    def _enqueue_worker_window(self, status: WindowStatus) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.WINDOW, status))

    def _poll_worker_events(self) -> None:
        while True:
            try:
                callback = self.ui_callbacks.get_nowait()
            except queue.Empty:
                break
            callback()

        while True:
            try:
                event = self.worker_events.get_nowait()
            except queue.Empty:
                break

            self._handle_worker_event(event)

        self._cleanup_finished_worker()

        if not self._closing:
            self.root.after(100, self._poll_worker_events)

    def _handle_worker_event(self, event: WorkerEvent) -> None:
        payload = event.payload
        if event.event_type is WorkerEventType.LOG and isinstance(payload, str):
            if "[ERROR]" in payload or "오류" in payload:
                self.logger.error(payload)
            elif "[WARN]" in payload:
                self.logger.warning(payload)
            else:
                self.logger.info(payload)
            self._set_message(payload)
            return

        if event.event_type is WorkerEventType.STATE and isinstance(payload, RunStatus):
            self._set_runtime_state(payload)
            return

        if event.event_type is WorkerEventType.STEP and isinstance(payload, AppStage):
            self._set_runtime_state(self._worker_status, payload)
            return

        if event.event_type is WorkerEventType.WINDOW and isinstance(payload, WindowStatus):
            self._set_window_status(payload, self.window_detail_var.get())

    def _cleanup_finished_worker(self) -> None:
        worker = self._fishing_worker
        if worker is None or worker.is_running():
            return

        self._handle_worker_finished()

    def _handle_worker_finished(self) -> None:
        self._fishing_worker = None
        if self._worker_status in (RunStatus.ERROR, RunStatus.IDLE):
            self._update_controls_for_state()
            return

        self._set_runtime_state(RunStatus.STOPPED, AppStage.IDLE)
        self._set_message("Worker가 종료되었습니다.")

    def _set_runtime_state(
        self,
        status: RunStatus,
        stage: Optional[AppStage] = None,
    ) -> None:
        self._worker_status = status
        self.run_status_var.set(status.value)
        self.status_badge_var.set(status.value)
        if stage is not None:
            self.stage_var.set(stage.value)
        self._update_controls_for_state()

    def _update_controls_for_state(self) -> None:
        status = self._worker_status
        start_state = tk.NORMAL
        stop_state = tk.DISABLED

        if status in (RunStatus.CHECKING_WINDOW, RunStatus.SELECTING_ROI, RunStatus.RUNNING):
            start_state = tk.DISABLED
        if status is RunStatus.RUNNING:
            stop_state = tk.NORMAL
        if status is RunStatus.STOPPING:
            start_state = tk.DISABLED
            stop_state = tk.DISABLED

        self.start_button.configure(state=start_state)
        self.stop_button.configure(state=stop_state)

    def _set_window_status(self, status: WindowStatus, detail: str) -> None:
        self.window_status_var.set(status.value)
        self.window_detail_var.set(detail)

    def _refresh_roi_status(self, override: Optional[str] = None) -> None:
        if override is not None:
            self.roi_status_var.set(override)
            return

        roi = engine.get_current_fishing_search_roi()
        if roi is None:
            self.roi_status_var.set("설정 안 됨")
            return
        _, _, width, height = roi
        self.roi_status_var.set(f"설정 완료: {width} x {height}")

    def _set_message(self, message: str) -> None:
        self.last_message_var.set(message)
        self.detail_var.set(message)

    def _append_log_threadsafe(self, line: str) -> None:
        self._ui_call(lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _on_close(self) -> None:
        self._closing = True
        if self._roi_window is not None:
            self._finish_roi_selection(None)
        if self._hotkey_window is not None:
            try:
                if self._hotkey_window.winfo_exists():
                    self._hotkey_window.destroy()
            except tk.TclError:
                pass
            self._hotkey_window = None

        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self.logger.info("[EXIT] 실행 중인 worker 중지 요청")
            try:
                worker.stop()
                worker.join(timeout=1.0)
            except Exception as exc:
                LOGGER.exception("Worker stop during close failed")
                self.logger.error(f"[EXIT] worker 종료 처리 중 오류: {exc}")
            if worker.is_running():
                self.logger.warning("[EXIT] worker 종료 대기 timeout, daemon thread로 종료를 이어갑니다.")

        try:
            self._hotkey_manager.stop()
        except Exception as exc:
            LOGGER.exception("Global hotkey listener stop failed")
            self.logger.error(f"[EXIT] 단축키 listener 종료 실패: {exc}")

        self.root.destroy()

    def _ui_call(self, callback: Callable[[], None]) -> None:
        if self._closing:
            return
        if threading.current_thread() is threading.main_thread():
            callback()
        else:
            self.ui_callbacks.put(callback)
            try:
                self.root.after(0, self._poll_worker_events)
            except RuntimeError:
                LOGGER.debug("UI callback queued while mainloop is inactive")

    @staticmethod
    def _format_window_rect(rect: WindowRect) -> str:
        return f"left={rect.left}, top={rect.top}, {rect.width} x {rect.height}"
