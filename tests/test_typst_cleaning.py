"""Typst cleaning ported from evid: scraped text must compile."""

import shutil
import subprocess

import pytest

from did.core.anonymizer import Anonymizer
from did.utils.file_utils import clean_typst_body, export_to_typst
from did.utils.typst_cleaning import (
    clean_text_for_typst,
    dehyphenate,
    typst_str_escape,
)


def test_clean_expands_ligatures_and_escapes_specials():
    cleaned = clean_text_for_typst("The \ufb01le costs $5 for #1 *really* [see] {here}")

    assert "file" in cleaned
    assert "\\$5" in cleaned
    assert "\\#1" in cleaned
    assert "\\*really\\*" in cleaned
    assert "\\[see\\]" in cleaned
    assert "\\{here\\}" in cleaned


def test_clean_comments_lines_that_would_be_references():
    assert (
        clean_text_for_typst("see @smith2020 for this") == "// see @smith2020 for this"
    )


def test_clean_escapes_a_line_leading_term_marker():
    assert clean_text_for_typst("/ 30 days") == "\\/ 30 days"
    assert clean_text_for_typst("a / b") == "a / b"


def test_dehyphenate_joins_lowercase_wraps_only():
    assert dehyphenate("mar-\nkant") == "markant"
    assert dehyphenate("GDPR-\n2018") == "GDPR-\n2018"


def test_typst_str_escape_handles_control_characters():
    assert typst_str_escape('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_clean_typst_body_keeps_placeholders_intact():
    body = "Price #1 for #(P1V1) and [PERSON 2]."

    cleaned = clean_typst_body(body)

    assert "#(P1V1)" in cleaned
    assert "\\#1" in cleaned
    assert clean_typst_body("#(DOC1V1) is a heading").startswith("#(DOC1V1)")


def _stub_anonymizer():
    anonymizer = Anonymizer.for_regex_only("en")
    anonymizer.load_replacements(
        {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    )
    return anonymizer


def test_export_keeps_tokens_and_escapes_the_rest(tmp_path):
    source = tmp_path / "case.txt"
    source.write_text("John Doe paid $5 for #1 [draft]", encoding="utf-8")

    main = tmp_path / "case.typ"
    export_to_typst(source, _stub_anonymizer(), main, source_text=source.read_text())

    body = main.read_text(encoding="utf-8")
    assert "#(P1V1)" in body
    assert "\\$5" in body
    assert "\\#1" in body
    assert "John Doe" not in body


@pytest.mark.skipif(shutil.which("typst") is None, reason="typst is not installed")
def test_a_document_full_of_specials_compiles(tmp_path):
    source = tmp_path / "gmail.txt"
    source.write_text(
        "Fwd: $x^2$ *bold* _under_ [bracket] {brace} ~tilde~ 50% a#b @ref\n"
        "John Doe wrote: / 30 days\n",
        encoding="utf-8",
    )
    main = tmp_path / "gmail.typ"
    pdf = tmp_path / "gmail.pdf"
    export_to_typst(source, _stub_anonymizer(), main, source_text=source.read_text())

    result = subprocess.run(
        ["typst", "compile", str(main), str(pdf)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert pdf.read_bytes().startswith(b"%PDF")
