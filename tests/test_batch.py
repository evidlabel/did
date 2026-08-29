"""Tests for the `did batch` multi-document command."""

import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout

PERSON_TOKEN_RE = re.compile(r"#\((P\d+V\d+)\)")


def _run(argv):
    """Invoke the did CLI with the given argv, swallowing output."""
    old_argv = sys.argv
    sys.argv = argv
    from did.cli import main

    try:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            try:
                main()
            except SystemExit as e:
                if e.code not in (None, 0):
                    raise
        return out.getvalue()
    finally:
        sys.argv = old_argv


def _make_docs(tmp_path):
    doc1 = tmp_path / "doc1.md"
    doc2 = tmp_path / "doc2.md"
    doc1.write_text("Meeting: John Doe met Jane Roe on the case.")
    doc2.write_text("Follow-up: John Doe confirmed the agreement.")
    return doc1, doc2


def test_batch_shared_tokens_and_no_pii_leak(tmp_path):
    doc1, doc2 = _make_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    anon = out_dir / "anon"
    keys = out_dir / "keys"

    body1 = (anon / "D1.typ").read_text()
    body2 = (anon / "D2.typ").read_text()

    # John Doe must be tokenized to the SAME token in both documents.
    tokens1 = set(PERSON_TOKEN_RE.findall(body1))
    tokens2 = set(PERSON_TOKEN_RE.findall(body2))
    assert tokens1 & tokens2, "shared person not consistently tokenized across docs"

    # No real PII may appear in the agent-safe anon/ tree.
    for f in anon.glob("*.typ"):
        text = f.read_text()
        assert "John Doe" not in text
        assert "Jane Roe" not in text
    assert "John Doe" not in (anon / "manifest.json").read_text()

    # Secret key set carries the real mapping.
    assert (keys / "config.yaml").exists()
    assert "John Doe" in (keys / "shared_vars.typ").read_text()
    # Fake vars exist and do NOT contain the real value.
    fake = (keys / "shared_fakevars.typ").read_text()
    assert "#let" in fake
    assert "John Doe" not in fake

    # Manifest lists the docs and the token inventory (ids only).
    manifest = json.loads((anon / "manifest.json").read_text())
    assert set(manifest["documents"]) == {"D1.typ", "D2.typ"}
    assert manifest["token_count"] == len(manifest["tokens"])
    assert manifest["tokens"]


def test_batch_render_files_are_self_contained(tmp_path):
    doc1, doc2 = _make_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    keys = out_dir / "keys"
    real = (keys / "render_real.typ").read_text()
    fake = (keys / "render_fake.typ").read_text()
    # Each render file imports its own var set in the same scope as the bodies.
    assert real.startswith('#import "shared_vars.typ"')
    assert fake.startswith('#import "shared_fakevars.typ"')
    # Bodies are inlined (so tokens resolve against the import, no #include scoping).
    assert "John Doe" not in real  # body still tokenized; value comes from import
    assert PERSON_TOKEN_RE.search(real)


def test_batch_combine_single_doc(tmp_path):
    doc1, doc2 = _make_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir), "--combine"])

    anon = out_dir / "anon"
    assert (anon / "combined.typ").exists()
    assert not (anon / "D1.typ").exists()
    assert not (anon / "D2.typ").exists()
    combined = (anon / "combined.typ").read_text()
    assert "= #(DOC1V1)" in combined
    assert "= #(DOC2V1)" in combined
    assert "John Doe" not in combined

    manifest = json.loads((anon / "manifest.json").read_text())
    assert manifest["documents"] == ["combined.typ"]


def test_batch_accepts_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("Report by John Doe.")
    (src / "b.txt").write_text("Reviewed by John Doe.")
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(src), "-o", str(out_dir)])

    anon = out_dir / "anon"
    assert (anon / "D1.typ").exists()
    assert (anon / "D2.typ").exists()


def test_batch_no_inputs_exits_nonzero(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    old_argv = sys.argv
    sys.argv = ["did", "batch", str(empty), "-o", str(tmp_path / "out")]
    from did.cli import main

    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            exited = False
            try:
                main()
            except SystemExit as e:
                exited = e.code not in (None, 0)
        assert exited, "batch with no input documents should exit non-zero"
    finally:
        sys.argv = old_argv


def test_batch_writes_verification_beside_the_keys_not_the_tokens(tmp_path):
    """The full report quotes the PII it found, so it belongs with the keys.

    Placing it in ``anon/`` would make the report itself the leak it warns
    about — the tree exists precisely so an agent can read it safely.
    """
    doc1, doc2 = _make_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    anon = out_dir / "anon"
    keys = out_dir / "keys"

    report = json.loads((keys / "verification.json").read_text())
    assert report["schema_version"] == 1
    assert report["deep"] is True
    assert set(report) >= {"leaks", "suspected", "retained", "clean", "findings"}

    # anon/ carries counts and locations only — never the offending text.
    manifest = json.loads((anon / "manifest.json").read_text())
    summary = manifest["verification"]
    assert summary["leaks"] == report["leaks"]
    assert summary["clean"] == report["clean"]
    for finding in summary["findings"]:
        assert "text" not in finding
        assert "context" not in finding
    raw = (anon / "manifest.json").read_text()
    assert "John Doe" not in raw
    assert "Jane Roe" not in raw


def test_batch_reports_a_surviving_identifier_without_failing(tmp_path):
    """A leak is reported loudly, but never turns into a non-zero exit.

    DID over-detects by design, so failing the command would turn expected
    false positives into broken pipelines.
    """
    doc = tmp_path / "case.md"
    # The surname survives replacement inside the Danish compound "Doesagen":
    # the replacer anchors single-token names with \b.
    doc.write_text("John Doe underskrev. Doesagen blev afgjort i dag.")
    out_dir = tmp_path / "out"
    output = _run(["did", "batch", str(doc), "-o", str(out_dir), "-l", "da"])

    body = (out_dir / "anon" / "D1.typ").read_text()
    assert "Doesagen" in body, "replacement was expected to leave this behind"

    report = json.loads((out_dir / "keys" / "verification.json").read_text())
    assert report["clean"] is False
    assert any(
        f["kind"] == "surviving_variant" and f["text"] == "Doe"
        for f in report["findings"]
    )
    assert "surviving identifier" in output


# ------------------------------------------------------- document titles ---
def _make_identifying_docs(tmp_path):
    """Documents whose *filenames* carry the PII, not just their bodies."""
    doc1 = tmp_path / "John_Doe_kontrakt_2024.md"
    doc2 = tmp_path / "Jane_Roe_brev.md"
    doc1.write_text("Meeting: John Doe met Jane Roe on the case.")
    doc2.write_text("Follow-up: John Doe confirmed the agreement.")
    return doc1, doc2


def test_anon_filenames_do_not_carry_the_source_name(tmp_path):
    doc1, doc2 = _make_identifying_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    anon = out_dir / "anon"
    assert (anon / "D1.typ").exists()
    assert (anon / "D2.typ").exists()
    assert not (anon / "John_Doe_kontrakt_2024.typ").exists()


def test_no_source_filename_appears_anywhere_under_anon(tmp_path):
    """The agent-safe tree must not leak a title any more than a body."""
    doc1, doc2 = _make_identifying_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    for path in (out_dir / "anon").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert doc1.name not in text, f"{doc1.name} leaked into {path.name}"
            assert doc2.name not in text, f"{doc2.name} leaked into {path.name}"
            assert doc1.stem not in text
    assert not any(doc1.stem in p.name for p in (out_dir / "anon").rglob("*"))


def test_combined_document_is_chaptered_by_title_token(tmp_path):
    doc1, doc2 = _make_identifying_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir), "--combine"])

    combined = (out_dir / "anon" / "combined.typ").read_text()
    assert "= #(DOC1V1)" in combined
    assert "= #(DOC2V1)" in combined
    assert doc1.name not in combined


def test_title_tokens_resolve_through_the_shared_vars(tmp_path):
    """Titles are first-class tokens: render_real.typ compiles to real names."""
    doc1, doc2 = _make_identifying_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    keys = out_dir / "keys"
    real = (keys / "shared_vars.typ").read_text()
    fake = (keys / "shared_fakevars.typ").read_text()

    assert f'#let DOC1V1 = "{doc1.name}"' in real
    assert f'#let DOC2V1 = "{doc2.name}"' in real
    # The fake set must define the same variables without revealing the names.
    assert "#let DOC1V1" in fake
    assert doc1.name not in fake
    assert doc2.name not in fake


def test_manifest_lists_the_safe_document_names(tmp_path):
    doc1, doc2 = _make_identifying_docs(tmp_path)
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc1), str(doc2), "-o", str(out_dir)])

    manifest = json.loads((out_dir / "anon" / "manifest.json").read_text())
    assert manifest["documents"] == ["D1.typ", "D2.typ"]
    assert any(token.startswith("DOC") for token in manifest["tokens"])


def test_title_mentioned_inside_a_body_is_tokenized_too(tmp_path):
    """Titles are ordinary config entries, so a self-reference is caught."""
    doc = tmp_path / "John_Doe_kontrakt_2024.md"
    doc.write_text("See John_Doe_kontrakt_2024.md for the signed agreement.")
    out_dir = tmp_path / "out"
    _run(["did", "batch", str(doc), "-o", str(out_dir)])

    body = (out_dir / "anon" / "D1.typ").read_text()
    assert "John_Doe_kontrakt_2024.md" not in body
    assert "#(DOC1V1)" in body
