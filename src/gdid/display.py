"""Pick a Qt display backend before QApplication exists (Qt-free, testable).

A terminal started outside the desktop session (a service, a multiplexer, a
resumed shell) often lacks ``WAYLAND_DISPLAY`` and ``DISPLAY`` even though the
session's sockets are there. Qt then falls back to xcb and aborts the process.
This finds the running session instead, preferring Wayland.
"""

from __future__ import annotations

import os
from pathlib import Path

NO_DISPLAY_MESSAGE = (
    "DID's desktop app needs a graphical session, but no Wayland or X display "
    "was found. Start it from a desktop terminal, or set WAYLAND_DISPLAY / DISPLAY."
)


def _wayland_socket(runtime_dir):
    if not runtime_dir:
        return None
    sockets = sorted(Path(runtime_dir).glob("wayland-[0-9]*"))
    sockets = [path for path in sockets if not path.name.endswith(".lock")]
    return sockets[0].name if sockets else None


def _x_display(x11_dir):
    sockets = sorted(Path(x11_dir).glob("X[0-9]*"))
    return f":{sockets[0].name[1:]}" if sockets else None


def configure_display(environ=None, *, x11_dir="/tmp/.X11-unix") -> bool:
    """Fill in the display environment for Qt; return False if none exists.

    An explicit ``QT_QPA_PLATFORM`` (e.g. ``offscreen``) is always respected.
    """
    env = os.environ if environ is None else environ
    if env.get("QT_QPA_PLATFORM"):
        return True
    if not env.get("WAYLAND_DISPLAY"):
        socket = _wayland_socket(env.get("XDG_RUNTIME_DIR"))
        if socket:
            env["WAYLAND_DISPLAY"] = socket
    if not env.get("DISPLAY"):
        display = _x_display(x11_dir)
        if display:
            env["DISPLAY"] = display
    backends = [
        name
        for name, key in (("wayland", "WAYLAND_DISPLAY"), ("xcb", "DISPLAY"))
        if env.get(key)
    ]
    if not backends:
        return False
    # Qt tries each in turn, so a broken xcb setup no longer aborts on Wayland.
    env["QT_QPA_PLATFORM"] = ";".join(backends)
    return True
