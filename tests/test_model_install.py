"""Model installer: only missing wheels, into this interpreter, never via spaCy."""

import sys

import pytest

from did.core import model_install


def test_install_command_targets_this_interpreter_with_pinned_wheels(monkeypatch):
    monkeypatch.setattr(model_install.shutil, "which", lambda _name: "/bin/uv")
    command = model_install.install_command(["da_core_news_lg"])
    assert command[:5] == ["/bin/uv", "pip", "install", "--python", sys.executable]
    assert command[5].endswith("da_core_news_lg-3.8.0-py3-none-any.whl")


def test_ensure_installs_only_what_the_language_is_missing(monkeypatch):
    installed = {"en_core_web_md"}
    monkeypatch.setattr("spacy.util.is_package", lambda name: name in installed)
    monkeypatch.setattr(model_install.shutil, "which", lambda _name: "/bin/uv")
    calls = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    def fake_run(command, **_kwargs):
        calls.append(command)
        installed.add("da_core_news_lg")
        return Done()

    monkeypatch.setattr(model_install.subprocess, "run", fake_run)
    messages = []

    assert model_install.ensure_spacy_models(
        "thorough", ["da"], progress=messages.append
    ) == ["da_core_news_lg"]
    assert len(calls) == 1 and not any("sv_core" in part for part in calls[0])
    assert messages[0].startswith("Downloading language model(s) da_core_news_lg")
    # Nothing missing → nothing run.
    assert model_install.ensure_spacy_models("thorough", ["da"]) == []
    assert len(calls) == 1


def test_failed_install_raises_with_the_tool_output(monkeypatch):
    monkeypatch.setattr("spacy.util.is_package", lambda _name: False)
    monkeypatch.setattr(model_install.shutil, "which", lambda _name: "/bin/uv")

    class Failed:
        returncode = 2
        stdout = ""
        stderr = "network unreachable"

    monkeypatch.setattr(model_install.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(model_install.ModelInstallError, match="network unreachable"):
        model_install.ensure_spacy_models("thorough", ["en"])
