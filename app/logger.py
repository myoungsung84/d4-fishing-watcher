from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

LogCallback = Callable[[str], None]


class AppLogger:
    def __init__(self, callback: Optional[LogCallback] = None) -> None:
        self._callback = callback

    def set_callback(self, callback: Optional[LogCallback]) -> None:
        self._callback = callback

    def log(self, message: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
        print(line)
        if self._callback is not None:
            self._callback(line)
