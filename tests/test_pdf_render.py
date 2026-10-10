"""Tests for the placeholder-PDF handoff renderer."""

import shutil

import pytest

from did.utils.pdf_render import render_pdfs, token_labels


def test_placeholder_labels_are_written_out_per_identity():
    labels = token_labels(
        ["P1V1", "P1V2", "P2V1", "O1V1", "DOC1V1"], mode="placeholder"
    )
    assert labels["P1V1"] == "[PERSON 1]"
    assert labels["P1V2"] == "[PERSON 1]"  # the variant index is dropped
    assert labels["P2V1"] == "[PERSON 2]"
    assert labels["O1V1"] == "[ORGANIZATION 1]"
    assert labels["DOC1V1"] == "[DOCUMENT 1]"


def test_redacted_labels_replace_everything():
    labels = token_labels(["P1V1", "E2V1"], mode="redacted")
    assert labels == {"P1V1": "[REDACTED]", "E2V1": "[REDACTED]"}


def test_synthetic_labels_are_stable_and_never_the_token():
    first = token_labels(["P1V1"], mode="synthetic", language="en")
    second = token_labels(["P1V1"], mode="synthetic", language="en")
    assert first == second
    assert first["P1V1"]
    assert "P1V1" not in first["P1V1"]


def test_unknown_render_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown render mode"):
        token_labels(["P1V1"], mode="weird")


def test_render_pdfs_writes_only_pdfs(tmp_path, monkeypatch):
    from did.utils import file_utils

    monkeypatch.setattr(
        file_utils,
        "compile_typst_pdf",
        lambda main_typ, pdf_path: pdf_path.write_bytes(b"%PDF-1.7\n"),
    )
    bodies = [("DOC1V1", "Hi #(P1V1)."), ("DOC2V1", "Bye #(P1V1).")]
    tokens = {"P1V1", "DOC1V1", "DOC2V1"}

    written = render_pdfs(bodies, tokens, tmp_path / "pdf")

    assert [p.name for p in written] == ["D1.pdf", "D2.pdf"]
    assert sorted(p.name for p in (tmp_path / "pdf").iterdir()) == [
        "D1.pdf",
        "D2.pdf",
    ]
    # The labelled Typst sources must not survive next to the PDFs.
    assert not list((tmp_path / "pdf").glob("*.typ"))


def test_render_pdfs_combine_writes_one(tmp_path, monkeypatch):
    from did.utils import file_utils

    monkeypatch.setattr(
        file_utils,
        "compile_typst_pdf",
        lambda main_typ, pdf_path: pdf_path.write_bytes(b"%PDF-1.7\n"),
    )
    written = render_pdfs(
        [("DOC1V1", "A"), ("DOC2V1", "B")],
        {"DOC1V1", "DOC2V1"},
        tmp_path / "pdf",
        combine=True,
    )
    assert [p.name for p in written] == ["combined.pdf"]


@pytest.mark.skipif(shutil.which("typst") is None, reason="typst is not installed")
def test_render_pdfs_compiles_real_pdfs(tmp_path):
    written = render_pdfs(
        [("DOC1V1", "Meeting with #(P1V1) and #(P2V1).")],
        {"P1V1", "P2V1", "DOC1V1"},
        tmp_path / "pdf",
    )
    assert written[0].read_bytes().startswith(b"%PDF")
