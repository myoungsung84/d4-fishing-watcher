from __future__ import annotations

import logging

from app.logger import configure_logging
from ui.main_window import D4FishingWatcherWindow


def main() -> None:
    configure_logging()
    app = D4FishingWatcherWindow()
    try:
        app.run()
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()
