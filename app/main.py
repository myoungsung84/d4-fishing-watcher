from __future__ import annotations

import logging

from ui.main_window import D4FishingWatcherWindow


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = D4FishingWatcherWindow()
    app.run()


if __name__ == "__main__":
    main()
