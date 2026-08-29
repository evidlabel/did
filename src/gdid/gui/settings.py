"""Small QSettings wrapper for recent project and directory preferences."""

from pathlib import Path

from PySide6.QtCore import QSettings

MAX_RECENT_PROJECTS = 10


class AppSettings:
    def __init__(self, settings=None):
        self._settings = settings or QSettings("evidlabel", "DID Pseudonymizer")

    def recent_projects(self) -> list[Path]:
        values = self._settings.value("recentProjects", [])
        if values is None:
            values = []
        if isinstance(values, str):
            values = [values]
        return [Path(value) for value in values]

    def add_recent_project(self, path) -> None:
        normalized = str(Path(path).expanduser().resolve())
        recent = [
            str(item) for item in self.recent_projects() if str(item) != normalized
        ]
        self._settings.setValue(
            "recentProjects", [normalized, *recent][:MAX_RECENT_PROJECTS]
        )

    def remove_recent_project(self, path) -> None:
        normalized = str(Path(path).expanduser().resolve())
        self._settings.setValue(
            "recentProjects",
            [str(item) for item in self.recent_projects() if str(item) != normalized],
        )

    def clear_recent_projects(self) -> None:
        self._settings.remove("recentProjects")

    def directory(self, key: str) -> str:
        return str(self._settings.value(f"directories/{key}", ""))

    def set_directory(self, key: str, path) -> None:
        self._settings.setValue(f"directories/{key}", str(path))
