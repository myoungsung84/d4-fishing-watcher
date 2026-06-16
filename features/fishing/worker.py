from __future__ import annotations

import threading
from typing import Callable, Optional

from app.state import AppStage, RunStatus
from features.fishing import engine

LogCallback = Callable[[str], None]
StateCallback = Callable[[RunStatus], None]
StepCallback = Callable[[AppStage], None]


class FishingWorker:
    """Run the fishing workflow outside the tkinter UI thread.

    The worker owns only lifecycle coordination: start the engine runtime, create
    one session, repeat fishing cycles, forward status, and guarantee cleanup.
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
                target=self._run_engine,
                name="FishingWorker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        engine.request_fishing_stop(clear_roi=False)
        self._state(RunStatus.STOPPING)
        self._log("[WORKER] 중지 요청을 받았습니다.")

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> None:
        with self._lock:
            thread = self._thread

        if thread is not None:
            thread.join(timeout=timeout)

    def _run_engine(self) -> None:
        runtime_started = False
        had_error = False

        self._state(RunStatus.RUNNING)
        self._step(AppStage.READY_TO_RUN)
        self._log("[WORKER] 낚시 worker thread 시작")

        try:
            runtime_started = True
            engine.start_fishing_runtime(
                enable_overlay=False,
                wait_for_start_hotkey=False,
            )
            session = engine.create_fishing_session()
            self._log("[WORKER] engine session 준비 완료")

            while not self._is_stop_requested():
                if not engine.is_fishing_running():
                    self._step(AppStage.READY_TO_RUN)
                    if self._stop_event.wait(0.2):
                        break
                    continue

                if engine.get_current_fishing_search_roi() is None:
                    self._log("[WORKER] 탐색 영역이 없어 실행을 중단합니다.")
                    engine.request_fishing_stop(clear_roi=False)
                    break

                engine.clear_ready_debug_snapshot()
                engine.refresh_diablo_window_rect(log_missing=True)
                session.default_ready_roi_local = engine.get_default_ready_roi_local()

                result = engine.run_fishing_cycle(session)

                if result is engine.FishingCycleResult.CANCELLED:
                    self._step(AppStage.STOPPING)
                    break

                if result is engine.FishingCycleResult.TIMEOUT:
                    self._step(AppStage.RECAST)
                    engine.handle_fishing_cycle_timeout()
                    continue

                if result is engine.FishingCycleResult.RETRY:
                    self._step(AppStage.READY_TO_RUN)
                    continue

                if result is engine.FishingCycleResult.FAILED:
                    self._log("[WORKER] cycle 실패, 기존 정책에 따라 다음 cycle을 대기합니다.")
                    self._step(AppStage.READY_TO_RUN)
                    continue

                if result is engine.FishingCycleResult.SUCCESS:
                    self._step(AppStage.RUNNING)
                    continue
        except Exception as exc:
            had_error = True
            self._state(RunStatus.ERROR)
            self._step(AppStage.ERROR)
            self._log(f"[WORKER] 오류: {exc}")
            return
        finally:
            if runtime_started:
                engine.shutdown_fishing_runtime(clear_roi=False)

            if not had_error:
                self._state(RunStatus.STOPPED)
                self._step(AppStage.IDLE)
                self._log("[WORKER] 중지 완료")

    def _is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _log(self, message: str) -> None:
        if self._on_log is not None:
            self._on_log(message)

    def _state(self, status: RunStatus) -> None:
        if self._on_state is not None:
            self._on_state(status)

    def _step(self, stage: AppStage) -> None:
        if self._on_step is not None:
            self._on_step(stage)
