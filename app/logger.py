from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from app.paths import get_project_root

LogCallback = Callable[[str], None]
LOG_DIR = get_project_root() / "logs"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class DateFileHandler(logging.Handler):
    """Append records to logs/YYYY-MM-DD.log and reopen when the date changes."""

    def __init__(self, log_dir: Path) -> None:
        super().__init__(logging.DEBUG)
        self.log_dir = log_dir
        self._current_date: date | None = None
        self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_stream(date.fromtimestamp(record.created))
            if self._stream is None:
                return
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()
        super().close()

    def _ensure_stream(self, record_date: date) -> None:
        if self._stream is not None and self._current_date == record_date:
            return

        if self._stream is not None:
            self._stream.close()
            self._stream = None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = record_date
        log_path = self.log_dir / f"{record_date.isoformat()}.log"
        self._stream = log_path.open("a", encoding="utf-8")


def configure_logging() -> Path:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    existing = next(
        (handler for handler in root_logger.handlers if isinstance(handler, DateFileHandler)),
        None,
    )
    if existing is None:
        handler = DateFileHandler(LOG_DIR)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root_logger.addHandler(handler)

    logging.getLogger("PIL").setLevel(logging.WARNING)
    return LOG_DIR / f"{date.today().isoformat()}.log"


def get_today_log_path() -> Path:
    return LOG_DIR / f"{date.today().isoformat()}.log"


class AppLogger:
    def __init__(self, callback: Optional[LogCallback] = None) -> None:
        self._callback = callback
        self._logger = logging.getLogger(__name__)

    def set_callback(self, callback: Optional[LogCallback]) -> None:
        self._callback = callback

    def log(self, message: str, level: int = logging.INFO) -> None:
        level_name = logging.getLevelName(level)
        display_level = level_name if isinstance(level_name, str) else "INFO"
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {display_level:<7} {message}"
        self._logger.log(level, message)
        if self._callback is not None:
            self._callback(line)

    def info(self, message: str) -> None:
        self.log(message, logging.INFO)

    def warning(self, message: str) -> None:
        self.log(message, logging.WARNING)

    def error(self, message: str) -> None:
        self.log(message, logging.ERROR)
