"""Display discovery for the GUI entry point (no Qt needed)."""

from gdid.display import configure_display


def test_finds_the_wayland_session_when_the_terminal_lost_it(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir()
    (runtime / "wayland-0").touch()
    (runtime / "wayland-0.lock").touch()
    env = {"XDG_RUNTIME_DIR": str(runtime)}

    assert configure_display(env, x11_dir=tmp_path / "none")
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["QT_QPA_PLATFORM"] == "wayland"


def test_prefers_wayland_and_keeps_x_as_fallback(tmp_path):
    x11 = tmp_path / "x11"
    x11.mkdir()
    (x11 / "X0").touch()
    env = {"WAYLAND_DISPLAY": "wayland-1"}

    assert configure_display(env, x11_dir=x11)
    assert env["DISPLAY"] == ":0"
    assert env["QT_QPA_PLATFORM"] == "wayland;xcb"


def test_explicit_platform_is_respected(tmp_path):
    env = {"QT_QPA_PLATFORM": "offscreen"}
    assert configure_display(env, x11_dir=tmp_path)
    assert env == {"QT_QPA_PLATFORM": "offscreen"}


def test_no_display_is_reported_not_aborted(tmp_path):
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    assert not configure_display(env, x11_dir=tmp_path / "none")
    assert "QT_QPA_PLATFORM" not in env
