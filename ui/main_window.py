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
from app.state import AppStage, RunStatus, WindowStatus
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

        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()
        self.ui_callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._fishing_worker: Optional[FishingWorker] = None
        self._roi_window: Optional[tk.Toplevel] = None
        self._worker_status = RunStatus.IDLE
        self._detected_window_rect: Optional[WindowRect] = None
        self._closing = False

        self.logger = AppLogger(self._append_log_threadsafe)

        self._build_layout()
        self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
        self._refresh_roi_status()
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

        self._create_info_row(control_frame, 1, "현재 상태", self.run_status_var)
        self._create_info_row(control_frame, 2, "현재 단계", self.stage_var)
        self._create_info_row(control_frame, 3, "최근 안내", self.last_message_var)

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

    def _on_start(self) -> None:
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
