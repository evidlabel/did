"""Utilities for handling different file types."""

from pathlib import Path
import re
import bibtexparser
from .core.anonymizer import Anonymizer


def extract_text(file_path: Path) -> str:
    """Extract processable text from the file based on its type."""
    if file_path.suffix in [".md", ".txt"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    elif file_path.suffix == ".tex":
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            body_text = re.sub(r"\\[\w]+.*?(\s|})", " ", content)
            body_text = re.sub(
                r"\\begin\{.*?\}.*?\\end\{.*?\}", " ", body_text, flags=re.DOTALL
            )
            return re.sub(r"\s+", " ", body_text).strip()
    elif file_path.suffix == ".bib":
        with open(file_path, "r", encoding="utf-8") as bibfile:
            database = bibtexparser.load(bibfile)
            text_content = []
            for entry in database.entries:
                for value in entry.values():
                    text_content.append(str(value))
            return " ".join(text_content)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")


def anonymize_file(input_path: Path, anonymizer: Anonymizer, output_path: Path) -> dict:
    """Anonymize the file using the provided anonymizer and return counts."""
    counts = {k: 0 for k in anonymizer.counts}
    if input_path.suffix in [".md", ".txt", ".tex"]:
        with open(input_path, "r", encoding="utf-8") as f:
            text = f.read()
        anonymized_text, field_counts = anonymizer.anonymize(text)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(anonymized_text)
        for k in counts:
            counts[k] += field_counts[k]
    elif input_path.suffix == ".bib":
        with open(input_path, "r", encoding="utf-8") as bibfile:
            database = bibtexparser.load(bibfile)
        for entry in database.entries:
            for field in list(entry.keys()):
                if field in entry:
                    field_text = str(entry[field])
                    anonymized_field, field_counts = anonymizer.anonymize(field_text)
                    entry[field] = anonymized_field
                    for k in counts:
                        counts[k] += field_counts[k]
        with open(output_path, "w", encoding="utf-8") as bibfile_out:
            bibtexparser.dump(database, bibfile_out)
    else:
        raise ValueError(f"Unsupported file type: {input_path.suffix}")
    return counts


def md_to_typst(md: str) -> str:
    """Simple Markdown to Typst converter."""
    # Headings
    md = re.sub(r"^#\s+(.*)$", r"= \1", md, flags=re.M)
    md = re.sub(r"^##\s+(.*)$", r"== \1", md, flags=re.M)
    md = re.sub(r"^###\s+(.*)$", r"=== \1", md, flags=re.M)
    md = re.sub(r"^####\s+(.*)$", r"==== \1", md, flags=re.M)
    # Bold and italic
    md = re.sub(r"\*\*(.*?)\*\*", r"*\1*", md)
    md = re.sub(r"__(.*?)__", r"*\1*", md)
    md = re.sub(r"\*(.*?)\*", r"_\1_", md)
    md = re.sub(r"_(.*?)_", r"_\1_", md)
    # Code
    md = re.sub(r"`(.*?)`", r"`\1`", md)
    # Links
    md = re.sub(r"\[(.*?)\]\((.*?)\)", r'#link("\2")[\1]', md)
    return md
