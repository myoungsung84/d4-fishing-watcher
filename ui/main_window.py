from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.logger import AppLogger
from app.state import AppStage, RunStatus, WindowStatus
from features.fishing.worker import FishingWorker

WorkerEvent = tuple[str, object]


class D4FishingWatcherWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("D4 Fishing Watcher")
        self.root.geometry("760x560")
        self.root.minsize(640, 460)

        self.window_status_var = tk.StringVar(value=WindowStatus.NOT_FOUND.value)
        self.run_status_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.stage_var = tk.StringVar(value=AppStage.IDLE.value)
        self.detail_var = tk.StringVar(value="메인 윈도우 UX 1차 MVP")
        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()

        self.logger = AppLogger(self._append_log_threadsafe)
        self.worker = FishingWorker(
            on_log=self._on_worker_log,
            on_state=self._on_worker_state,
            on_step=self._on_worker_step,
        )

        self._build_layout()
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
        status_frame.columnconfigure((0, 1, 2), weight=1, uniform="status")

        self._create_status_card(status_frame, 0, "Diablo IV 창", self.window_status_var)
        self._create_status_card(status_frame, 1, "실행 상태", self.run_status_var)
        self._create_status_card(status_frame, 2, "현재 단계", self.stage_var)

        button_frame = ttk.Frame(self.root, padding=(16, 8))
        button_frame.grid(row=2, column=0, sticky="ew")

        self.detect_button = ttk.Button(
            button_frame,
            text="창 감지 테스트",
            command=self._on_detect_window,
        )
        self.detect_button.grid(row=0, column=0, sticky="w")

        self.start_button = ttk.Button(button_frame, text="시작", command=self._on_start)
        self.start_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.stop_button = ttk.Button(button_frame, text="중지", command=self._on_stop)
        self.stop_button.grid(row=0, column=2, sticky="w", padx=(8, 0))

        self.capture_button = ttk.Button(button_frame, text="캡처 저장", state=tk.DISABLED)
        self.capture_button.grid(row=0, column=3, sticky="w", padx=(8, 0))

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

    def _on_detect_window(self) -> None:
        self._set_stage(AppStage.WINDOW_DETECTION)
        self.detect_button.configure(state=tk.DISABLED)
        self.logger.log("[WINDOW] Diablo IV 창 감지 테스트 시작")
        thread = threading.Thread(target=self._detect_window_worker, daemon=True)
        thread.start()

    def _detect_window_worker(self) -> None:
        try:
            from screen import find_diablo_window_rect

            rect = find_diablo_window_rect()
        except Exception as exc:
            self._ui_call(lambda exc=exc: self._handle_detect_error(exc))
            return

        self._ui_call(lambda: self._handle_detect_result(rect))

    def _handle_detect_result(self, rect: object) -> None:
        self.detect_button.configure(state=tk.NORMAL)
        if rect is None:
            self.window_status_var.set(WindowStatus.NOT_FOUND.value)
            self._set_stage(AppStage.IDLE)
            self.logger.log("[WINDOW] Diablo IV 창을 찾지 못했습니다.")
            return

        self.window_status_var.set(WindowStatus.FOUND.value)
        self._set_stage(AppStage.READY_TO_RUN)
        self.logger.log(
            "[WINDOW] 감지됨 "
            f"left={rect.left}, top={rect.top}, width={rect.width}, height={rect.height}"
        )

    def _handle_detect_error(self, exc: Exception) -> None:
        self.detect_button.configure(state=tk.NORMAL)
        self.window_status_var.set(WindowStatus.NOT_FOUND.value)
        self.run_status_var.set(RunStatus.ERROR.value)
        self._set_stage(AppStage.IDLE)
        self.logger.log(f"[ERROR] 창 감지 실패: {exc}")

    def _on_start(self) -> None:
        if self.worker.is_running():
            self.logger.log("[START] worker가 이미 실행 중입니다.")
            return

        self._set_stage(AppStage.READY_TO_RUN)
        self.run_status_var.set(RunStatus.RUNNING.value)
        self.logger.log("[START] 낚시 worker 시작 요청")
        started = self.worker.start()
        if not started:
            self.run_status_var.set(RunStatus.RUNNING.value)

    def _on_stop(self) -> None:
        if not self.worker.is_running():
            self.run_status_var.set(RunStatus.STOPPED.value)
            self._set_stage(AppStage.IDLE)
            self.logger.log("[STOP] 실행 중인 worker가 없습니다.")
            return

        self.run_status_var.set(RunStatus.STOPPING.value)
        self.logger.log("[STOP] 낚시 worker 중지 요청")
        self.worker.stop()

    def _on_worker_log(self, message: str) -> None:
        self.worker_events.put(("log", message))

    def _on_worker_state(self, status: RunStatus) -> None:
        self.worker_events.put(("state", status))

    def _on_worker_step(self, stage: AppStage) -> None:
        self.worker_events.put(("step", stage))

    def _poll_worker_events(self) -> None:
        while True:
            try:
                event_type, payload = self.worker_events.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self.logger.log(str(payload))
            elif event_type == "state" and isinstance(payload, RunStatus):
                self.run_status_var.set(payload.value)
            elif event_type == "step" and isinstance(payload, AppStage):
                self._set_stage(payload)

        self.root.after(100, self._poll_worker_events)

    def _set_stage(self, stage: AppStage) -> None:
        self.stage_var.set(stage.value)

    def _append_log_threadsafe(self, line: str) -> None:
        self._ui_call(lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _ui_call(self, callback: Callable[[], None]) -> None:
        if threading.current_thread() is threading.main_thread():
            callback()
        else:
            self.root.after(0, callback)
