"""Optional install extras: gui (PySide6). Language models are extra `models`."""

from __future__ import annotations

import importlib.util

GUI_INSTALL = (
    "GUI requires the gui extra. "
    'Install with: uv tool install "did[gui] @ git+https://github.com/evidlabel/did.git"'
)


def has_gui() -> bool:
    return importlib.util.find_spec("PySide6") is not None


def require_gui() -> None:
    if not has_gui():
        raise ImportError(GUI_INSTALL)
