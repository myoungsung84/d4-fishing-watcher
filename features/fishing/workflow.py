from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from app.state import AppStage, RunStatus

LogCallback = Callable[[str], None]
StateCallback = Callable[[RunStatus], None]
StepCallback = Callable[[AppStage], None]
ShouldStopCallback = Callable[[], bool]


@dataclass
class FishingWorkflowContext:
    on_log: Optional[LogCallback] = None
    on_state: Optional[StateCallback] = None
    on_step: Optional[StepCallback] = None
    should_stop: Optional[ShouldStopCallback] = None
    stop_event: Optional[threading.Event] = None

    def stop_requested(self) -> bool:
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        if self.should_stop is not None:
            return self.should_stop()
        return False

    def log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def state(self, status: RunStatus) -> None:
        if self.on_state is not None:
            self.on_state(status)

    def step(self, stage: AppStage) -> None:
        if self.on_step is not None:
            self.on_step(stage)


class FishingEngine:
    """Boundary for the future UI/worker-driven fishing workflow.

    The existing implementation in main.py still owns hotkeys, overlay lifetime,
    global RuntimeState, and the concrete fishing loop. This class is intentionally
    thin for now: it defines the callbacks and stop contract that the loop will use
    when the cycle is moved out of main.py.
    """

    def __init__(
        self,
        *,
        on_log: Optional[LogCallback] = None,
        on_state: Optional[StateCallback] = None,
        on_step: Optional[StepCallback] = None,
        should_stop: Optional[ShouldStopCallback] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.context = FishingWorkflowContext(
            on_log=on_log,
            on_state=on_state,
            on_step=on_step,
            should_stop=should_stop,
            stop_event=stop_event,
        )

    def run(self) -> None:
        self.context.state(RunStatus.RUNNING)
        self.context.step(AppStage.READY_TO_RUN)
        self.context.log("[ENGINE] workflow boundary ready")
        self.context.log("[ENGINE] 실제 낚시 사이클은 아직 main.py에서 분리되지 않았습니다.")

        if self.context.stop_requested():
            self.context.step(AppStage.STOPPING)
            self.context.state(RunStatus.STOPPING)
            self.context.log("[ENGINE] 시작 전 중지 요청 감지")
            return

        self.context.step(AppStage.IDLE)


def run_fishing_workflow(context: FishingWorkflowContext) -> None:
    engine = FishingEngine(
        on_log=context.on_log,
        on_state=context.on_state,
        on_step=context.on_step,
        should_stop=context.should_stop,
        stop_event=context.stop_event,
    )
    engine.run()
