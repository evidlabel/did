"""Tests for CLI functionality."""

import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout

from ruamel import yaml


def test_cli_registers_gui_command():
    from did.cli import app

    assert "gui" in {command.name for command in app.commands}


def test_cli_extract(tmp_path):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    input_file.write_text("Hello John Doe and Jon Doe, CPR: 123456-1234")

    old_argv = sys.argv
    sys.argv = ["did", "extract", str(input_file), "--config", str(config_file)]

    from did.cli import main

    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
        try:
            main()
        except SystemExit as e:
            if e.code != 0:
                raise

    sys.argv = old_argv
    output = out.getvalue()

    assert "PERSON" in output
    assert config_file.exists()
    yaml_obj = yaml.YAML()
    with open(config_file) as f:
        config = yaml_obj.load(f)
        assert len(config["PERSON"]) >= 1
        assert any(
            "123456-1234" in entry["variants"]
            for entry in config.get("GENERAL_NUMBER", [])
            + config.get("PHONE_NUMBER", [])
            + config.get("ID_NUMBER", [])
        )


def _run_cli(argv):
    """Invoke the did CLI with the given argv; return (stdout, exit_code)."""
    old_argv = sys.argv
    sys.argv = argv
    from did.cli import main

    code = 0
    try:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            try:
                main()
            except SystemExit as e:
                code = e.code or 0
        return out.getvalue(), code
    finally:
        sys.argv = old_argv


def _flat(text):
    """Collapse whitespace — rich hard-wraps console output at 80 columns."""
    return re.sub(r"\s+", " ", text)


# --------------------------------------------- command tree and definitions ---
def test_cli_builds_without_definition_errors():
    """Building the parser validates every callback against its args/options.

    A treeparse definition mismatch (arg name or type) raises here rather than
    crashing the first time a user runs the command.
    """
    from did.cli import app

    assert app.build_parser() is not None


def test_cli_lists_every_registered_command():
    from did.cli import app

    names = {command.name for command in app.commands}
    names |= {group.name for group in app.subgroups}
    assert {"extract", "anon", "gui", "models"} <= names


def test_models_command_reports_when_nothing_is_missing(monkeypatch):
    """`did models` exits 0 and names no download when the env is complete."""
    from did.cli import models as models_module

    monkeypatch.setattr(models_module, "ensure_spacy_models", lambda *a, **k: [])
    out, code = _run_cli(["did", "models", "da"])
    assert code == 0
    assert "already installed" in _flat(out)
