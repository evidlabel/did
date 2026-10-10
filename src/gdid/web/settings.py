"""Recent projects and directory memory for the web GUI.

Qt stored these in QSettings (``evidlabel`` / ``DID Pseudonymizer``). The first
run copies that list once into ``$XDG_CONFIG_HOME/did/gui.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MAX_RECENT_PROJECTS = 10


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "did"


def config_path() -> Path:
    return config_dir() / "gui.json"


def _qt_settings_path() -> Path | None:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    path = root / "evidlabel" / "DID Pseudonymizer.conf"
    return path if path.is_file() else None


def _split_qt_list(value: str) -> list[str]:
    value = value.strip().strip('"')
    if not value:
        return []
    return [part.strip().strip('"') for part in value.split(",") if part.strip()]


def import_qt_settings(path: Path) -> dict:
    """Read recent projects and directories from a QSettings ini file."""
    recent: list[str] = []
    directories: dict[str, str] = {}
    section = ""
    numbered: dict[int, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"recent": [], "directories": {}}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if section in {"", "General"} and key == "recentProjects":
            recent = _split_qt_list(value)
        elif section == "recentProjects" and key.endswith("\\path"):
            index = key.split("\\", 1)[0]
            if index.isdigit():
                numbered[int(index)] = value
        elif section == "directories" and value:
            directories[key] = value
    if not recent and numbered:
        recent = [numbered[index] for index in sorted(numbered)]
    return {"recent": recent, "directories": directories}


class GuiSettings:
    def __init__(self, path: Path | None = None):
        self.path = path or config_path()
        self.recent: list[str] = []
        self.directories: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            self.recent = [str(item) for item in data.get("recent") or []]
            self.directories = {
                str(key): str(value)
                for key, value in (data.get("directories") or {}).items()
            }
            return
        qt = _qt_settings_path()
        if qt is not None:
            imported = import_qt_settings(qt)
            self.recent = imported["recent"][:MAX_RECENT_PROJECTS]
            self.directories = imported["directories"]
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"recent": self.recent, "directories": self.directories}
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def recent_projects(self) -> list[Path]:
        return [Path(item) for item in self.recent]

    def add_recent_project(self, path) -> None:
        normalized = str(Path(path).expanduser().resolve())
        self.recent = [normalized, *[p for p in self.recent if p != normalized]]
        self.recent = self.recent[:MAX_RECENT_PROJECTS]
        self._save()

    def remove_recent_project(self, path) -> None:
        normalized = str(Path(path).expanduser().resolve())
        self.recent = [item for item in self.recent if item != normalized]
        self._save()

    def directory(self, key: str) -> str:
        return self.directories.get(key, "")

    def set_directory(self, key: str, path) -> None:
        self.directories[key] = str(path)
        self._save()
