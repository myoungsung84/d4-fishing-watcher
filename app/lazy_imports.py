from __future__ import annotations

import importlib
from types import ModuleType


class LazyModule:
    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module: ModuleType | None = None

    def _load(self) -> ModuleType:
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


def lazy_import(module_name: str) -> LazyModule:
    return LazyModule(module_name)
