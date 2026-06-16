from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from app.state import AppStage, RunStatus
from features.fishing.workflow import FishingEngine

LogCallback = Callable[[str], None]
StateCallback = Callable[[RunStatus], None]
StepCallback = Callable[[AppStage], None]


class FishingWorker:
    """Run the fishing workflow outside the tkinter UI thread.

    The current implementation intentionally does not call main.run(). That function
    still owns console hotkeys, overlay lifetime, and an infinite wait/start loop.
    This worker establishes the UI-safe lifecycle first, so the fishing cycle can be
    moved behind this boundary without changing detection or coordinate contracts.
    """

    def __init__(
        self,
        *,
        on_log: Optional[LogCallback] = None,
        on_state: Optional[StateCallback] = None,
        on_step: Optional[StepCallback] = None,
    ) -> None:
        self._on_log = on_log
        self._on_state = on_state
        self._on_step = on_step
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._log("[WORKER] 이미 실행 중입니다. 중복 시작을 무시합니다.")
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_placeholder,
                name="FishingWorker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        self._state(RunStatus.STOPPING)
        self._log("[WORKER] 중지 요청을 받았습니다.")

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run_placeholder(self) -> None:
        self._state(RunStatus.RUNNING)
        self._step(AppStage.READY_TO_RUN)
        self._log("[WORKER] 낚시 worker thread 시작")
        self._log("[WORKER] 실제 자동 낚시 루프 연결은 다음 단계에서 수행합니다.")

        try:
            while not self._stop_event.wait(0.2):
                pass
        except Exception as exc:
            self._state(RunStatus.ERROR)
            self._step(AppStage.IDLE)
            self._log(f"[WORKER] 오류: {exc}")
            return

        self._step(AppStage.IDLE)
        self._state(RunStatus.STOPPED)
        self._log("[WORKER] 중지 완료")

    def _run_engine(self) -> None:
        engine = FishingEngine(
            on_log=self._log,
            on_state=self._state,
            on_step=self._step,
            stop_event=self._stop_event,
            should_stop=self._stop_event.is_set,
        )
        try:
            engine.run()
        except Exception as exc:
            self._state(RunStatus.ERROR)
            self._step(AppStage.ERROR)
            self._log(f"[WORKER] engine 오류: {exc}")
            return

        if self._stop_event.is_set():
            self._state(RunStatus.STOPPED)
            self._step(AppStage.IDLE)

    def _log(self, message: str) -> None:
        if self._on_log is not None:
            self._on_log(message)

    def _state(self, status: RunStatus) -> None:
        if self._on_state is not None:
            self._on_state(status)

    def _step(self, stage: AppStage) -> None:
        if self._on_step is not None:
            self._on_step(stage)
