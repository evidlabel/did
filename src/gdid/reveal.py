"""Reveal a path in the desktop file manager, bypassing a hijacked default.

A desktop can register something other than a file browser as the handler for
``inode/directory`` (this machine's default is git-cola), so ``xdg-open`` — and
therefore ``QDesktopServices`` — opens the wrong application. Prefer an
installed file manager when there is one, and fall back to the desktop opener.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Ordered by likelihood on a normal desktop; the first installed one wins.
_FILE_MANAGERS = (
    ("nautilus", lambda folder: ["nautilus", str(folder)]),
    ("dolphin", lambda folder: ["dolphin", str(folder)]),
    ("nemo", lambda folder: ["nemo", str(folder)]),
    ("caja", lambda folder: ["caja", str(folder)]),
    ("thunar", lambda folder: ["thunar", str(folder)]),
    ("pcmanfm", lambda folder: ["pcmanfm", str(folder)]),
    ("pcmanfm-qt", lambda folder: ["pcmanfm-qt", str(folder)]),
    ("konqueror", lambda folder: ["konqueror", str(folder)]),
)


def file_manager_command(path, *, which=shutil.which):
    """The argv that reveals *path* in an installed file manager, or ``None``."""
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    for name, build in _FILE_MANAGERS:
        if which(name):
            return build(folder)
    return None


def open_in_file_manager(path, *, which=shutil.which, popen=subprocess.Popen) -> bool:
    """Open *path*'s folder in a file manager. False when none is installed."""
    command = file_manager_command(path, which=which)
    if command is None:
        return False
    popen(command)
    return True
