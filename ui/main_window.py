from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from tkinter import ttk
from typing import Callable, Optional

from app.logger import AppLogger
from app.state import AppStage, RunStatus, WindowStatus
from features.fishing import engine
from features.fishing.worker import FishingWorker


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
        self.root.geometry("760x560")
        self.root.minsize(640, 460)

        self.window_status_var = tk.StringVar(value=WindowStatus.NOT_FOUND.value)
        self.run_status_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.stage_var = tk.StringVar(value=AppStage.IDLE.value)
        self.roi_status_var = tk.StringVar(value="미설정")
        self.detail_var = tk.StringVar(value="메인 윈도우에서 낚시 실행 상태를 제어합니다")
        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()
        self._fishing_worker: Optional[FishingWorker] = None
        self._roi_window: Optional[tk.Toplevel] = None
        self._worker_status = RunStatus.IDLE
        self._closing = False

        self.logger = AppLogger(self._append_log_threadsafe)

        self._build_layout()
        self._apply_run_status(RunStatus.IDLE)
        self._refresh_roi_status()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_worker_events)
        self.logger.log("[BOOT] GUI ready. 낚시 worker 생명주기 연결 완료.")

    def run(self) -> None:
        self.root.mainloop()

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(header, text="D4 Fishing Watcher", font=("Segoe UI", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(
            header,
            textvariable=self.detail_var,
            foreground="#555555",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        status_frame = ttk.Frame(self.root, padding=(16, 8))
        status_frame.grid(row=1, column=0, sticky="ew")
        status_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="status")

        self._create_status_card(status_frame, 0, "Diablo IV 창", self.window_status_var)
        self._create_status_card(status_frame, 1, "실행 상태", self.run_status_var)
        self._create_status_card(status_frame, 2, "현재 단계", self.stage_var)
        self._create_status_card(status_frame, 3, "낚시 영역", self.roi_status_var)

        button_frame = ttk.Frame(self.root, padding=(16, 8))
        button_frame.grid(row=2, column=0, sticky="ew")

        self.start_button = ttk.Button(button_frame, text="시작", command=self._on_start)
        self.start_button.grid(row=0, column=0, sticky="w")

        self.stop_button = ttk.Button(button_frame, text="중지", command=self._on_stop)
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.roi_button = ttk.Button(
            button_frame,
            text="낚시 영역 설정",
            command=self._on_select_roi,
        )
        self.roi_button.grid(row=0, column=2, sticky="w", padx=(8, 0))

        log_frame = ttk.LabelFrame(self.root, text="로그", padding=(12, 10))
        log_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(8, 16))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=14,
            wrap="word",
            state=tk.DISABLED,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _create_status_card(
        self,
        parent: ttk.Frame,
        column: int,
        label_text: str,
        value_var: tk.StringVar,
    ) -> None:
        frame = ttk.LabelFrame(parent, text=label_text, padding=(12, 10))
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        value = ttk.Label(frame, textvariable=value_var, font=("Segoe UI", 13, "bold"))
        value.grid(row=0, column=0, sticky="w")

    def _on_start(self) -> None:
        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self.logger.log("[START] worker가 이미 실행 중입니다.")
            return

        if worker is not None:
            self._handle_worker_finished()

        self._set_stage(AppStage.WINDOW_DETECTION)
        self._apply_run_status(RunStatus.CHECKING_WINDOW)
        self.logger.log("[START] 시작 요청")

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
            self._fishing_worker = None
            self.logger.log(f"[ERROR] worker 시작 실패: {exc}")
            self._apply_run_status(RunStatus.ERROR)
            self._set_stage(AppStage.ERROR)
            return

        if not started:
            self._fishing_worker = None
            self.logger.log("[START] worker 시작이 거부되었습니다.")
            self._apply_run_status(RunStatus.IDLE)
            self._set_stage(AppStage.IDLE)

    def _on_select_roi(self) -> None:
        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self.logger.log("[ROI] 실행 중에는 낚시 영역을 변경할 수 없습니다.")
            return

        if self._roi_window is not None:
            self.logger.log("[ROI] 낚시 영역 설정이 이미 진행 중입니다.")
            return

        self.logger.log("[ROI] 낚시 영역을 드래그해 설정하세요. ESC 또는 우클릭으로 취소할 수 있습니다.")
        self._open_roi_selection_window()

    def _open_roi_selection_window(self) -> None:
        window = tk.Toplevel(self.root)
        self._roi_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.30)
        window.configure(bg="black")

        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        window.geometry(f"{screen_width}x{screen_height}+0+0")

        canvas = tk.Canvas(window, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            screen_width // 2,
            42,
            text="Drag fishing search area",
            fill="#ffffff",
            font=("Malgun Gothic", 18, "bold"),
        )

        selection_rect: dict[str, Optional[int]] = {"id": None}
        drag_start: dict[str, Optional[int]] = {"x": None, "y": None}

        def normalize_roi(
            start_x: int,
            start_y: int,
            end_x: int,
            end_y: int,
        ) -> tuple[int, int, int, int]:
            left = min(start_x, end_x)
            top = min(start_y, end_y)
            right = max(start_x, end_x)
            bottom = max(start_y, end_y)
            return left, top, right - left, bottom - top

        def on_press(event) -> None:
            drag_start["x"] = int(event.x_root)
            drag_start["y"] = int(event.y_root)
            rect_id = selection_rect["id"]
            if rect_id is not None:
                canvas.delete(rect_id)
            selection_rect["id"] = canvas.create_rectangle(
                event.x,
                event.y,
                event.x,
                event.y,
                outline="#2dd4bf",
                width=3,
            )

        def on_drag(event) -> None:
            rect_id = selection_rect["id"]
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if rect_id is None or start_x is None or start_y is None:
                return
            local_start_x = start_x - window.winfo_rootx()
            local_start_y = start_y - window.winfo_rooty()
            canvas.coords(rect_id, local_start_x, local_start_y, event.x, event.y)

        def on_release(event) -> None:
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if start_x is None or start_y is None:
                self._finish_roi_selection(None)
                return
            roi = normalize_roi(start_x, start_y, int(event.x_root), int(event.y_root))
            self._finish_roi_selection(roi)

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
            except tk.TclError:
                pass

        if roi is None:
            self._refresh_roi_status()
            self.logger.log("[ROI] 낚시 영역 설정을 취소했습니다.")
            return

        selected_roi = engine.set_fishing_search_roi_from_screen(roi)
        if selected_roi is None:
            self._refresh_roi_status()
            self.logger.log("[ROI] 낚시 영역 설정에 실패했습니다.")
            return

        x, y, width, height = selected_roi
        self._refresh_roi_status()
        self.logger.log(f"[ROI] 낚시 영역 설정 완료 local x={x} y={y} w={width} h={height}")

    def _on_stop(self) -> None:
        worker = self._fishing_worker
        if worker is None:
            self._apply_run_status(RunStatus.STOPPED)
            self.logger.log("[STOP] 실행 중인 worker가 없습니다.")
            return

        if not worker.is_running():
            self._handle_worker_finished()
            return

        if self._worker_status is RunStatus.STOPPING:
            self.logger.log("[STOP] 이미 중지 요청 중입니다.")
            return

        self._apply_run_status(RunStatus.STOPPING)
        self.logger.log("[STOP] 낚시 worker 중지 요청")
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
            self.logger.log(payload)
            return

        if event.event_type is WorkerEventType.STATE and isinstance(payload, RunStatus):
            self._apply_run_status(payload)
            return

        if event.event_type is WorkerEventType.STEP and isinstance(payload, AppStage):
            self._set_stage(payload)
            return

        if event.event_type is WorkerEventType.WINDOW and isinstance(payload, WindowStatus):
            self.window_status_var.set(payload.value)

    def _cleanup_finished_worker(self) -> None:
        worker = self._fishing_worker
        if worker is None or worker.is_running():
            return

        self._handle_worker_finished()

    def _handle_worker_finished(self) -> None:
        self._fishing_worker = None
        if self._worker_status in (RunStatus.ERROR, RunStatus.IDLE):
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.roi_button.configure(state=tk.NORMAL)
            return

        self._apply_run_status(RunStatus.STOPPED)
        self._set_stage(AppStage.IDLE)

    def _apply_run_status(self, status: RunStatus) -> None:
        self._worker_status = status
        self.run_status_var.set(status.value)

        if status in (RunStatus.CHECKING_WINDOW, RunStatus.RUNNING):
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.roi_button.configure(state=tk.DISABLED)
            return

        if status is RunStatus.STOPPING:
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.DISABLED)
            self.roi_button.configure(state=tk.DISABLED)
            return

        if status is RunStatus.ERROR:
            worker = self._fishing_worker
            self.start_button.configure(
                state=tk.DISABLED if worker is not None and worker.is_running() else tk.NORMAL
            )
            self.stop_button.configure(state=tk.DISABLED)
            self.roi_button.configure(state=tk.NORMAL)
            return

        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.roi_button.configure(state=tk.NORMAL)

    def _refresh_roi_status(self) -> None:
        roi = engine.get_current_fishing_search_roi()
        if roi is None:
            self.roi_status_var.set("미설정")
            return
        self.roi_status_var.set("설정됨")

    def _set_stage(self, stage: AppStage) -> None:
        self.stage_var.set(stage.value)

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
            self.logger.log("[EXIT] 실행 중인 worker 중지 요청")
            worker.stop()
            worker.join(timeout=1.0)
            if worker.is_running():
                self.logger.log("[EXIT] worker 종료 대기 timeout, daemon thread로 종료를 이어갑니다.")

        self.root.destroy()

    def _ui_call(self, callback: Callable[[], None]) -> None:
        if threading.current_thread() is threading.main_thread():
            callback()
        else:
            self.root.after(0, callback)
