from __future__ import annotations

import sys
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return get_project_root()


def get_resource_path(*parts: str) -> Path:
    return get_base_dir().joinpath(*parts)


def get_template_path(filename: str) -> Path:
    return get_resource_path("templates", filename)


def get_data_dir() -> Path:
    data_dir = get_project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
