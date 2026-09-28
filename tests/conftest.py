"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_gui_settings(tmp_path_factory, monkeypatch):
    """Keep tests out of the user's real DID settings (recent projects, dirs).

    Without this, any window built with default settings records its pytest
    temp projects in the user's recent-project list.
    """
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return
    from gdid.gui import settings

    path = tmp_path_factory.mktemp("settings") / "did.ini"
    original = settings.AppSettings.__init__

    def isolated(self, qsettings=None):
        original(self, qsettings or QSettings(str(path), QSettings.Format.IniFormat))

    monkeypatch.setattr(settings.AppSettings, "__init__", isolated)
