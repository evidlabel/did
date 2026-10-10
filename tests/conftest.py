"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_gui_settings(tmp_path_factory, monkeypatch):
    """Keep tests out of the user's real GUI settings and the raise lock."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("run")))
