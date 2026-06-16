from __future__ import annotations

from enum import Enum


class WindowStatus(str, Enum):
    NOT_FOUND = "감지 안 됨"
    FOUND = "감지됨"


class RunStatus(str, Enum):
    IDLE = "대기"
    RUNNING = "실행 중"
    STOPPING = "중지 중"
    STOPPED = "중지됨"
    ERROR = "오류"


class AppStage(str, Enum):
    IDLE = "대기"
    WINDOW_DETECTION = "창 감지"
    READY_TO_RUN = "낚시 실행 준비"
    FIND_WINDOW = "창 감지"
    CAST = "캐스팅"
    FIND_BOBBER = "찌 탐색"
    WAIT_READY = "입질 대기"
    REEL = "낚아올림"
    LOOT = "줍기"
    RECAST = "재시전"
    STOPPING = "중지 중"
    ERROR = "오류"
    RUNNING = "실행 중"
