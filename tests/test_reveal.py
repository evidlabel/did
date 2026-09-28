"""File-manager reveal: prefer a real browser over a hijacked MIME default."""

from gdid.gui.reveal import file_manager_command, open_in_file_manager


def _which(*installed):
    return lambda name: f"/usr/bin/{name}" if name in installed else None


def test_prefers_nautilus_over_the_desktop_default(tmp_path):
    folder = tmp_path / "case"
    folder.mkdir()
    assert file_manager_command(folder, which=_which("nautilus")) == [
        "nautilus",
        str(folder),
    ]


def test_falls_back_to_the_next_installed_manager(tmp_path):
    folder = tmp_path / "case"
    folder.mkdir()
    assert file_manager_command(folder, which=_which("dolphin", "thunar")) == [
        "dolphin",
        str(folder),
    ]


def test_reveals_a_file_by_its_folder(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("x", encoding="utf-8")
    assert file_manager_command(f, which=_which("nautilus")) == [
        "nautilus",
        str(tmp_path),
    ]


def test_no_file_manager_returns_none(tmp_path):
    assert file_manager_command(tmp_path, which=_which()) is None


def test_open_invokes_the_manager(tmp_path):
    calls = []
    assert open_in_file_manager(tmp_path, which=_which("nautilus"), popen=calls.append)
    assert calls == [["nautilus", str(tmp_path)]]


def test_open_reports_failure_without_a_manager(tmp_path):
    calls = []
    assert not open_in_file_manager(tmp_path, which=_which(), popen=calls.append)
    assert calls == []
