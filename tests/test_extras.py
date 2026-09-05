"""Optional extras: gui (PySide6). Models extra already exists."""

from __future__ import annotations

import pytest


def test_require_gui_names_the_extra_when_missing(monkeypatch):
    from did import extras

    monkeypatch.setattr(extras, "has_gui", lambda: False)
    with pytest.raises(ImportError, match=r"did\[gui\]"):
        extras.require_gui()


def test_gui_callback_exits_with_extra_hint(monkeypatch, capsys):
    from did import extras
    from did.cli.gui import gui

    monkeypatch.setattr(extras, "has_gui", lambda: False)
    with pytest.raises(SystemExit) as ei:
        gui()
    assert ei.value.code == 1
    assert "did[gui]" in capsys.readouterr().out
