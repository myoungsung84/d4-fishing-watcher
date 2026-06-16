from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.paths import get_resource_path

LOGGER = logging.getLogger(__name__)

ICON_DIR = ("assets", "icons")
ICON_ORIGINAL_SVG = (*ICON_DIR, "app-icon.svg")
ICON_ORIGINAL_PNG = (*ICON_DIR, "app-icon-original.png")
ICON_CIRCLE_128 = (*ICON_DIR, "app-icon-circle.png")
ICON_CIRCLE_32 = (*ICON_DIR, "app-icon-circle-32.png")
ICON_CIRCLE_16 = (*ICON_DIR, "app-icon-circle-16.png")
ICON_ICO = (*ICON_DIR, "app-icon.ico")


def get_app_icon_path(*parts: str) -> Path:
    return get_resource_path(*ICON_DIR, *parts)


@dataclass
class TkAppIcons:
    small: Optional[tk.PhotoImage] = None
    medium: Optional[tk.PhotoImage] = None
    large: Optional[tk.PhotoImage] = None
    ico_path: Optional[Path] = None

    @property
    def header(self) -> Optional[tk.PhotoImage]:
        return self.medium or self.small or self.large

    def apply_to(self, window: tk.Misc) -> None:
        images = [image for image in (self.large, self.medium, self.small) if image is not None]
        try:
            if self.ico_path is not None and self.ico_path.exists():
                window.iconbitmap(default=str(self.ico_path))
        except tk.TclError:
            LOGGER.debug("Failed to apply ICO app icon for Tk window", exc_info=True)

        if not images:
            return
        try:
            window.iconphoto(True, *images)
        except tk.TclError:
            LOGGER.debug("Failed to apply app icon for Tk window", exc_info=True)


def load_tk_app_icons() -> TkAppIcons:
    icons = TkAppIcons()
    try:
        icons.small = tk.PhotoImage(file=str(get_resource_path(*ICON_CIRCLE_16)))
        icons.medium = tk.PhotoImage(file=str(get_resource_path(*ICON_CIRCLE_32)))
        icons.large = tk.PhotoImage(file=str(get_resource_path(*ICON_CIRCLE_128)))
        icons.ico_path = get_resource_path(*ICON_ICO)
    except tk.TclError:
        LOGGER.debug("Failed to load app icon assets", exc_info=True)
    return icons
