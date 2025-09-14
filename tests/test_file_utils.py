"""Tests for file_utils."""

import pytest
from pathlib import Path
from did.file_utils import extract_text, anonymize_file, md_to_typst
from did.core.anonymizer import Anonymizer
import bibtexparser


@pytest.fixture
def temp_files(tmp_path):
    md_file = tmp_path / "test.md"
    md_file.write_text("# Heading\n**Bold** text")
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("Plain text")
    tex_file = tmp_path / "test.tex"
    tex_file.write_text("\\documentclass{article} \\begin{document} Hello \\end{document}")
    bib_file = tmp_path / "test.bib"
    bib_file.write_text("@article{test, title={Test Title}, author={John Doe}}")
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
    assert "Test Title" in text
    assert "John Doe" in text


def test_extract_text_unsupported(tmp_files):
    unsupported = tmp_files[0].with_suffix(".pdf")
    unsupported.write_text("Dummy")
    with pytest.raises(ValueError, match="Unsupported file type: .pdf"):
        extract_text(unsupported)


def test_anonymize_file_md(temp_files):
    md_file, _, _, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(md_file)])
    output = md_file.with_stem("output")
    counts = anonymize_file(md_file, anonymizer, output)
    assert output.exists()
    assert counts["person_found"] == 0  # No persons in sample


def test_anonymize_file_txt(temp_files):
    _, txt_file, _, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(txt_file)])
    output = txt_file.with_stem("output")
    counts = anonymize_file(txt_file, anonymizer, output)
    assert output.exists()


def test_anonymize_file_tex(temp_files):
    _, _, tex_file, _ = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(tex_file)])
    output = tex_file.with_stem("output")
    counts = anonymize_file(tex_file, anonymizer, output)
    assert output.exists()


def test_anonymize_file_bib(temp_files):
    _, _, _, bib_file = temp_files
    anonymizer = Anonymizer(language="en")
    anonymizer.detect_entities([extract_text(bib_file)])
    output = bib_file.with_stem("output")
    counts = anonymize_file(bib_file, anonymizer, output)
    assert output.exists()
    with open(output, "r") as f:
        bib_content = f.read()
        assert "<PERSON_1>" in bib_content


def test_anonymize_file_unsupported(temp_files):
    unsupported = temp_files[0].with_suffix(".pdf")
    unsupported.write_text("Dummy")
    anonymizer = Anonymizer(language="en")
    output = unsupported.with_stem("output")
    with pytest.raises(ValueError, match="Unsupported file type: .pdf"):
        anonymize_file(unsupported, anonymizer, output)


def test_md_to_typst():
    md = "# Heading\n**Bold** _italic_ `code` [link](url)"
    typst = md_to_typst(md)
    assert "= Heading" in typst
    assert "*Bold*" in typst
    assert "_italic_" in typst
    assert "`code`" in typst
    assert '#link("url")[link]' in typst
