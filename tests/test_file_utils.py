"""Tests for file_utils."""

import pytest
from docx import Document

from did.core.anonymizer import Anonymizer
from did.utils.file_utils import anonymize_file, extract_text


@pytest.fixture
def temp_files(tmp_path):
    md_file = tmp_path / "test.md"
    md_file.write_text("# Heading\n**Bold** text")
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("Plain text")
    tex_file = tmp_path / "test.tex"
    tex_file.write_text(
        "\\documentclass{article} \\begin{document} Hello \\end{document}"
    )
    bib_file = tmp_path / "test.bib"
    bib_file.write_text(
        "@article{test, title={Test Title by John Doe}, author={John Doe}}"
    )
    return md_file, txt_file, tex_file, bib_file


def test_extract_text_md(temp_files):
    md_file, _, _, _ = temp_files
    text = extract_text(md_file)
    assert "# Heading" in text
    assert "**Bold** text" in text


def test_extract_text_txt(temp_files):
    _, txt_file, _, _ = temp_files
    text = extract_text(txt_file)
    assert text == "Plain text"


def test_extract_text_tex(temp_files):
    _, _, tex_file, _ = temp_files
    text = extract_text(tex_file)
    assert "Hello" in text
    assert "\\documentclass" not in text


def test_extract_text_bib(temp_files):
    _, _, _, bib_file = temp_files
    text = extract_text(bib_file)
    assert "Test Title by John Doe" in text
    assert "John Doe" in text


def test_extract_text_html_strips_tags(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><h1>Title</h1><p>John &amp; Jane</p></body></html>",
        encoding="utf-8",
    )
    text = extract_text(path)
    assert "Title" in text
    assert "John & Jane" in text
    assert "<h1>" not in text


def test_extract_text_rtf_strips_control_words(tmp_path):
    path = tmp_path / "note.rtf"
    path.write_text(r"{\rtf1\ansi Hello \b world\b0 }", encoding="utf-8")
    text = extract_text(path)
    assert "Hello" in text
    assert "world" in text
    assert "\\rtf" not in text


def test_extract_text_from_any_plain_text_extension(tmp_path):
    path = tmp_path / "notes.widget"
    path.write_text("John Doe was here", encoding="utf-8")
    assert extract_text(path) == "John Doe was here"


def test_extract_text_falls_back_to_a_legacy_encoding(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("b\u00f8rn".encode("latin-1"))
    assert "b\u00f8rn" in extract_text(path)


def test_extract_text_refuses_binary(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00\x01\x02binary")
    with pytest.raises(ValueError, match="binary"):
        extract_text(path)


def test_export_to_typst_accepts_a_csv(tmp_path):
    from did.utils.file_utils import export_to_typst

    anonymizer = Anonymizer.for_regex_only("en")
    anonymizer.load_replacements(
        {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    )
    source = tmp_path / "rows.csv"
    source.write_text("name,note\nJohn Doe,ok\n", encoding="utf-8")
    main = tmp_path / "rows.typ"

    export_to_typst(source, anonymizer, main, source_text=source.read_text())

    body = main.read_text(encoding="utf-8")
    assert "#(P1V1)" in body
    assert "John Doe" not in body


def test_extract_text_docx_preserves_paragraph_and_table_order(tmp_path):
    path = tmp_path / "case.docx"
    document = Document()
    document.add_paragraph("Before table")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "John Doe"
    table.cell(0, 1).text = "Counsel"
    document.add_paragraph("After table")
    document.save(path)

    assert extract_text(path) == ("Before table\nJohn Doe\tCounsel\nAfter table")


def _pdf_bytes(objects: list[bytes]) -> bytes:
    header = b"%PDF-1.4\n"
    chunks = [header]
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n".encode(), b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return b"".join([*chunks, *xref, trailer])


def _write_text_pdf(path, sentence: str) -> None:
    text = f"BT /F1 12 Tf 40 100 Td ({sentence}) Tj ET".encode()
    path.write_bytes(
        _pdf_bytes(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
                b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            ]
        )
    )


def _write_outline_pdf(path) -> None:
    curves = " ".join(
        f"{n} {n} {n + 1} {n + 1} {n + 2} {n + 2} {n + 3} {n + 3} c" for n in range(40)
    )
    stream = f"10 10 m {curves} S".encode()
    assert len(stream) >= 800
    path.write_bytes(
        _pdf_bytes(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Contents 4 0 R >>",
                b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            ]
        )
    )


def test_garbled_type3_extraction_falls_back_to_poppler(tmp_path, monkeypatch):
    from pypdf._page import PageObject

    path = tmp_path / "distiller.pdf"
    _write_text_pdf(path, "Anna Andersson")
    monkeypatch.setattr(
        PageObject, "extract_text", lambda self, *args, **kwargs: "A" * 200
    )
    assert "Anna Andersson" in extract_text(path)
    assert "AAAA" not in extract_text(path)


def test_mojibake_text_layer_falls_back_to_png_ocr(tmp_path, monkeypatch):
    from pypdf._page import PageObject

    path = tmp_path / "distiller.pdf"
    _write_text_pdf(path, "Anna Andersson")
    monkeypatch.setattr(
        PageObject, "extract_text", lambda self, *args, **kwargs: "A" * 200
    )
    mojibake = ("K/J" + "ÿ" + "\x13") * 30
    monkeypatch.setattr("did.utils.file_utils._pdftotext_page", lambda *_args: mojibake)
    monkeypatch.setattr(
        "did.utils.file_utils._ocr_page", lambda *_args: "Hej Anna, kan vi talas vid?"
    )
    assert extract_text(path) == "Hej Anna, kan vi talas vid?"


def test_reading_score_rejects_a_repeated_glyph_and_mojibake():
    from did.utils.file_utils import _READING_FLOOR, _reading_score

    assert _reading_score("A" * 80) < _READING_FLOOR
    assert _reading_score("K/Jÿ\x13" * 30) < _READING_FLOOR
    prose = "Anna Andersson bor i Göteborg och arbetar där."
    assert _reading_score(prose) >= _READING_FLOOR


def test_still_garbled_after_poppler_uses_png_ocr(tmp_path, monkeypatch):
    path = tmp_path / "outlined.pdf"
    _write_outline_pdf(path)
    monkeypatch.setattr("did.utils.file_utils._pdftotext_page", lambda *_args: "B" * 80)
    seen = {}

    def fake_ocr(_path, page_number):
        seen["page"] = page_number
        return "Hej Anna Andersson"

    monkeypatch.setattr("did.utils.file_utils._ocr_page", fake_ocr)
    assert extract_text(path) == "Hej Anna Andersson"
    assert seen["page"] == 1


def test_content_stats_ignore_text_operators_inside_strings():
    from did.utils.file_utils import _content_stats

    stats = _content_stats(b"BT (not a Tj operator) Tj ET 1 2 3 4 5 6 7 8 c S")
    assert stats["text_ops"] == 1
    assert stats["curves"] == 1


def test_readable_pdf_does_not_call_poppler_or_ocr(tmp_path, monkeypatch):
    path = tmp_path / "letter.pdf"
    _write_text_pdf(path, "Anna Andersson")

    def fail_tool(*_args, **_kwargs):
        raise AssertionError("painted text should stay on the pypdf path")

    monkeypatch.setattr("did.utils.file_utils._run_tool", fail_tool)
    assert extract_text(path) == "Anna Andersson"


def test_unreadable_text_layer_falls_back_to_poppler(tmp_path, monkeypatch):
    from pypdf._page import PageObject

    path = tmp_path / "gmail.pdf"
    _write_text_pdf(path, "Anna Andersson")
    monkeypatch.setattr(PageObject, "extract_text", lambda self, *args, **kwargs: "")
    assert "Anna Andersson" in extract_text(path)


def test_stray_header_on_a_painted_page_falls_back_to_poppler(tmp_path, monkeypatch):
    from pypdf._page import PageObject

    path = tmp_path / "gmail.pdf"
    _write_text_pdf(path, "Anna Andersson")
    monkeypatch.setattr(
        PageObject, "extract_text", lambda self, *args, **kwargs: "Gmail"
    )
    assert "Anna Andersson" in extract_text(path)


def test_outlined_glyphs_fall_back_to_ocr(tmp_path, monkeypatch):
    path = tmp_path / "outlined.pdf"
    _write_outline_pdf(path)
    monkeypatch.setattr("did.utils.file_utils._pdftotext_page", lambda *_args: "")
    monkeypatch.setattr(
        "did.utils.file_utils._ocr_page", lambda *_args: "Hej Anna Andersson"
    )
    assert extract_text(path) == "Hej Anna Andersson"


def test_extract_text_rejects_an_undecodable_file(tmp_path):
    unsupported = tmp_path / "test.odt"
    unsupported.write_bytes(b"\x00\x01\x02archive bytes")
    with pytest.raises(ValueError, match="binary"):
        extract_text(unsupported)


def test_anonymize_file_md(temp_files):
    md_file, _, _, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(md_file)])
    output = md_file.with_stem("output")
    counts = anonymize_file(md_file, anonymizer, output)
    assert output.exists()
    assert counts["person_found"] == 0  # No persons in sample
    assert counts["person_replaced"] == 0


def test_anonymize_file_txt(temp_files):
    _, txt_file, _, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(txt_file)])
    output = txt_file.with_stem("output")
    counts = anonymize_file(txt_file, anonymizer, output)
    assert output.exists()
    assert counts["person_found"] == 0
    assert counts["person_replaced"] == 0


def test_anonymize_file_tex(temp_files):
    _, _, tex_file, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(tex_file)])
    output = tex_file.with_stem("output")
    counts = anonymize_file(tex_file, anonymizer, output)
    assert output.exists()
    assert counts["person_found"] == 0
    assert counts["person_replaced"] == 0


def test_anonymize_file_bib(temp_files):
    _, _, _, bib_file = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(bib_file)])
    output = bib_file.with_stem("output")
    counts = anonymize_file(bib_file, anonymizer, output)
    assert output.exists()
    with open(output) as f:
        bib_content = f.read()
        assert "#(P" in bib_content
    assert counts["person_replaced"] >= 1


def test_anonymize_file_unsupported(tmp_path):
    unsupported = tmp_path / "test.odt"
    unsupported.write_text("Dummy")
    anonymizer = Anonymizer(language="en")
    output = unsupported.with_stem("output")
    with pytest.raises(ValueError, match="Unsupported file type: .odt"):
        anonymize_file(unsupported, anonymizer, output)


def test_organization_reaches_the_vars_file(tmp_path):
    """Regression: #(O1V1) in the body with no #let O1V1 to resolve it.

    ``export_to_typst`` kept its own category list, which omitted
    ``organization`` while the replacer's included it. The exported document
    referenced a variable nothing defined — the Typst compile failed and the
    organization's real value never reached the key set at all.
    """
    from did.utils.file_utils import export_to_typst

    source = tmp_path / "org.md"
    source.write_text("The contract with Acme Holdings was signed today.")
    anonymizer = Anonymizer(language="en")
    anonymizer.load_replacements(
        {"ORGANIZATION": [{"id": "ORGANIZATION_1", "variants": ["Acme Holdings"]}]}
    )

    main_path = tmp_path / "org.typ"
    export_to_typst(source, anonymizer, main_path)

    body = main_path.read_text()
    assert "#(O1V1)" in body
    assert "Acme Holdings" not in body
    # Every token in the body must be defined in the vars file.
    assert '#let O1V1 = "Acme Holdings"' in (tmp_path / "org_vars.typ").read_text()
    assert "#let O1V1" in (tmp_path / "org_fakevars.typ").read_text()
