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


def test_cli_anonymize(tmp_path):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    output_file = tmp_path / "output.md"
    original_text = "Hello John Doe and Jon Doe, CPR: 123456-1234"
    input_file.write_text(original_text)

    old_argv = sys.argv
    sys.argv = ["did", "extract", str(input_file), "--config", str(config_file)]
    from did.cli import main

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            main()
        except SystemExit as e:
            if e.code != 0:
                raise
    sys.argv = old_argv

    modified_text = (
        original_text
        + " and John Doe again, and new person Alice, new CPR: 987654-4321"
    )
    input_file.write_text(modified_text)

    sys.argv = [
        "did",
        "pseudo",
        "plain",
        str(input_file),
        "--config",
        str(config_file),
        "--output",
        str(output_file),
    ]
    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
        try:
            main()
        except SystemExit as e:
            if e.code != 0:
                raise
    sys.argv = old_argv
    output = out.getvalue()

    assert "PERSON" in output
    assert output_file.exists()
    with open(output_file) as f:
        content = f.read()
        assert "#(P1V" in content
        assert "Alice" in content
        assert "987654-4321" in content
        assert content.count("#(P1V") == 3


# --------------------------------------------- `did full` and `did pseudo typst` ---
CONFIG_YAML = """\
PERSON:
  - id: PERSON_1
    variants:
      - John Doe
"""


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


def test_full_writes_config_and_the_three_typst_files(tmp_path):
    """`did full` chains extract + typst export in one step."""
    doc = tmp_path / "case.md"
    doc.write_text("John Doe met Jane Roe on 2024-01-02.")

    _, code = _run_cli(["did", "full", str(doc)])
    assert code == 0

    main_typ = tmp_path / "case.typ"
    vars_typ = tmp_path / "case_vars.typ"
    fake_typ = tmp_path / "case_fakevars.typ"
    config = tmp_path / "case_config.yaml"
    assert main_typ.exists() and vars_typ.exists() and fake_typ.exists()
    assert config.exists()

    body = main_typ.read_text()
    assert "#(P1V" in body
    assert "John Doe" not in body
    # Real values live only in the vars file; the fake file must not leak them.
    assert "John Doe" in vars_typ.read_text()
    assert "John Doe" not in fake_typ.read_text()


def test_full_missing_input_exits_nonzero(tmp_path):
    out, code = _run_cli(["did", "full", str(tmp_path / "nope.md")])
    assert code == 1
    assert "not found" in _flat(out)


def test_full_rejects_a_non_typst_output(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("John Doe was here.")
    out, code = _run_cli(["did", "full", str(doc), "-o", str(tmp_path / "out.md")])
    assert code == 1
    assert "must end with .typ" in _flat(out)


def test_pseudo_typst_writes_main_vars_and_fakevars(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("John Doe was here.")
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)

    _, code = _run_cli(["did", "pseudo", "typst", str(doc), "-c", str(config)])
    assert code == 0

    body = (tmp_path / "case.typ").read_text()
    assert "#(P1V1)" in body
    assert "John Doe" not in body
    assert "John Doe" in (tmp_path / "case_vars.typ").read_text()
    assert "John Doe" not in (tmp_path / "case_fakevars.typ").read_text()


def test_pseudo_typst_rejects_a_non_typst_output(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("John Doe was here.")
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    out, code = _run_cli(
        [
            "did",
            "pseudo",
            "typst",
            str(doc),
            "-c",
            str(config),
            "-o",
            str(tmp_path / "out.md"),
        ]
    )
    assert code == 1
    assert "must end with .typ" in _flat(out)


def test_pseudo_typst_missing_config_exits_nonzero(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("John Doe was here.")
    out, code = _run_cli(
        ["did", "pseudo", "typst", str(doc), "-c", str(tmp_path / "nope.yaml")]
    )
    assert code == 1
    assert "File not found" in _flat(out)


def test_pseudo_plain_missing_input_exits_nonzero(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    out, code = _run_cli(
        ["did", "pseudo", "plain", str(tmp_path / "nope.md"), "-c", str(config)]
    )
    assert code == 1
    assert "Error:" in _flat(out)


def test_pseudo_plain_invalid_yaml_exits_nonzero(tmp_path):
    doc = tmp_path / "case.md"
    doc.write_text("John Doe was here.")
    config = tmp_path / "config.yaml"
    config.write_text("PERSON: [unclosed\n")
    out, code = _run_cli(["did", "pseudo", "plain", str(doc), "-c", str(config)])
    assert code == 1
    assert "Error:" in _flat(out)
