"""The gui extra is empty: the page server is in the base install."""

from __future__ import annotations


def test_gui_is_always_available():
    from did import extras

    assert extras.has_gui() is True
    extras.require_gui()


def test_gui_callback_serves_without_a_qt_check(monkeypatch):
    from did.cli import gui as gui_mod

    seen = {}
    monkeypatch.setattr(gui_mod, "serve_gui", lambda **kwargs: seen.update(kwargs))
    gui_mod.gui(port=8800, browser=True, headless=False)
    assert seen == {"port": 8800, "browser": True, "headless": False, "agent": None}
