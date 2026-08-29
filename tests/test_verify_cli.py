"""Tests for the `did verify` command.

The verification *logic* is covered in ``test_verification.py``. This file
covers the CLI wrapper around it: how paths are expanded, which failures exit
non-zero, and — the property that matters for pipelines — that findings warn
without failing the command.
"""

import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout

CONFIG_YAML = """\
PERSON:
  - id: PERSON_1
    variants:
      - John Doe
      - Doe
LOCATION:
  - id: LOCATION_1
    variants:
      - Storetorv 10
"""

CLEAN_DOC = "#(P1V1) drove #(P1V2) home to #(A1V1) on #(DT1V1).\n"
LEAKY_DOC = "John Doe was here.\n"


def _run(argv):
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


def _setup(tmp_path, doc_text=CLEAN_DOC, name="doc.typ"):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    doc = tmp_path / name
    doc.write_text(doc_text)
    return config, doc


def test_cli_registers_verify_command():
    from did.cli import app

    assert "verify" in {command.name for command in app.commands}


# ------------------------------------------------------------- happy path ---
def test_verify_clean_document_reports_clean(tmp_path):
    config, doc = _setup(tmp_path)
    out, code = _run(["did", "verify", str(doc), "--config", str(config)])
    assert code == 0
    assert "clean" in _flat(out)


def test_verify_reports_a_surviving_identifier(tmp_path):
    config, doc = _setup(tmp_path, LEAKY_DOC)
    out, _ = _run(["did", "verify", str(doc), "--config", str(config)])
    assert "surviving identifier" in _flat(out)


def test_findings_do_not_fail_the_command(tmp_path):
    """DID over-detects by design; a non-zero exit would break pipelines."""
    config, doc = _setup(tmp_path, LEAKY_DOC)
    _, code = _run(["did", "verify", str(doc), "--config", str(config)])
    assert code == 0


# --------------------------------------------------------- path collection ---
def test_directory_input_is_expanded_recursively(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    docs = tmp_path / "anon"
    (docs / "nested").mkdir(parents=True)
    (docs / "a.typ").write_text(CLEAN_DOC)
    (docs / "nested" / "b.md").write_text(CLEAN_DOC)
    (docs / "c.txt").write_text(CLEAN_DOC)
    (docs / "ignored.pdf").write_text("not a supported suffix")

    out, code = _run(["did", "verify", str(docs), "--config", str(config)])
    assert code == 0
    assert "Checking 3 document(s)" in _flat(out)


def test_repeated_paths_are_verified_once(tmp_path):
    config, doc = _setup(tmp_path)
    out, _ = _run(["did", "verify", str(doc), str(doc), "--config", str(config)])
    assert "Checking 1 document(s)" in _flat(out)


def test_missing_input_path_exits_nonzero(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    out, code = _run(
        ["did", "verify", str(tmp_path / "nope.typ"), "--config", str(config)]
    )
    assert code == 1
    assert "not found" in _flat(out)


def test_directory_without_supported_documents_exits_nonzero(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML)
    empty = tmp_path / "empty"
    empty.mkdir()
    out, code = _run(["did", "verify", str(empty), "--config", str(config)])
    assert code == 1
    assert "No documents found" in _flat(out)


def test_missing_config_exits_nonzero(tmp_path):
    _, doc = _setup(tmp_path)
    out, code = _run(
        ["did", "verify", str(doc), "--config", str(tmp_path / "nope.yaml")]
    )
    assert code == 1
    assert "not found" in _flat(out)


# ------------------------------------------------------------- exclusions ---
def test_not_names_exclusion_is_retained_not_leaked(tmp_path):
    config, doc = _setup(tmp_path, "Signed by Mette Sagsbehandler for #(P1V1).\n")
    not_names = tmp_path / "not_names.json"
    not_names.write_text(json.dumps(["Mette Sagsbehandler"]))
    report = tmp_path / "report.json"

    _, code = _run(
        [
            "did",
            "verify",
            str(doc),
            "--config",
            str(config),
            "--not-names",
            str(not_names),
            "--report",
            str(report),
        ]
    )
    assert code == 0
    data = json.loads(report.read_text())
    assert data["leaks"] == 0
    assert data["retained"] == 1


def test_not_names_must_contain_a_json_list(tmp_path):
    config, doc = _setup(tmp_path)
    not_names = tmp_path / "not_names.json"
    not_names.write_text(json.dumps({"not": "a list"}))
    out, code = _run(
        [
            "did",
            "verify",
            str(doc),
            "--config",
            str(config),
            "--not-names",
            str(not_names),
        ]
    )
    assert code == 1
    assert "must contain a JSON list" in _flat(out)


def test_unreadable_config_exits_nonzero(tmp_path):
    """A malformed YAML config must fail the command, not crash it."""
    config, doc = _setup(tmp_path)
    config.write_text("PERSON: [unclosed\n")
    out, code = _run(["did", "verify", str(doc), "--config", str(config)])
    assert code == 1
    assert "Error:" in _flat(out)


# ----------------------------------------------------------------- report ---
def test_report_carries_the_full_finding(tmp_path):
    config, doc = _setup(tmp_path, LEAKY_DOC)
    report = tmp_path / "report.json"
    out, code = _run(
        ["did", "verify", str(doc), "--config", str(config), "--report", str(report)]
    )
    assert code == 0
    assert "Report written to" in _flat(out)

    data = json.loads(report.read_text())
    assert data["schema_version"] == 1
    assert data["leaks"] == 1
    assert data["clean"] is False
    finding = data["findings"][0]
    assert finding["text"] == "John Doe"
    assert finding["document"] == "doc.typ"
    assert finding["entity_id"] == "PERSON_1"


def test_quiet_suppresses_console_output_but_still_writes_the_report(tmp_path):
    config, doc = _setup(tmp_path, LEAKY_DOC)
    report = tmp_path / "report.json"
    out, code = _run(
        [
            "did",
            "verify",
            str(doc),
            "--config",
            str(config),
            "--report",
            str(report),
            "--quiet",
        ]
    )
    assert code == 0
    assert out.strip() == ""
    assert json.loads(report.read_text())["leaks"] == 1


def test_unwritable_report_path_exits_nonzero(tmp_path):
    config, doc = _setup(tmp_path)
    out, code = _run(
        [
            "did",
            "verify",
            str(doc),
            "--config",
            str(config),
            "--report",
            str(tmp_path / "missing_dir" / "report.json"),
        ]
    )
    assert code == 1
    assert "Error:" in _flat(out)
