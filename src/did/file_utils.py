"""Utilities for handling different file types for entity extraction and anonymization."""

from pathlib import Path
import re  # Added for simple text extraction from .tex files
import bibtexparser  # Replaced pybtex with bibtexparser
from .core.anonymizer import Anonymizer  # Import Anonymizer for processing


def extract_text(file_path: Path) -> str:
    """Extract processable text from the file based on its type, focusing on body text."""
    if file_path.suffix in [".md", ".txt"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()  # For Markdown and text, return full content as body
    elif file_path.suffix == ".tex":
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Simple extraction: Remove LaTeX commands and environments to get body text
            # This is a basic approach; may not cover all cases
            body_text = re.sub(r"\\[\w]+.*?(\s|})", " ", content)  # Strip commands
            body_text = re.sub(
                r"\\begin\{.*?\}.*?\\end\{.*?\}", " ", body_text, flags=re.DOTALL
            )
            return re.sub(r"\s+", " ", body_text).strip()  # Clean up extra spaces
    elif file_path.suffix == ".bib":
        with open(file_path, "r", encoding="utf-8") as bibfile:
            database = bibtexparser.load(bibfile)  # Use bibtexparser to parse BibTeX
            text_content = []
            for entry in database.entries:  # Iterate over entries
                for field, value in entry.items():  # Access fields as dictionary
                    text_content.append(
                        str(value)
                    )  # Include only field values as body text
            return " ".join(text_content)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")


def anonymize_file(input_path: Path, config: dict, output_path: Path):
    """Anonymize the file based on its type and write to output in the same format."""
    anonymizer = Anonymizer()
    anonymizer.load_replacements(config)

    if input_path.suffix in [".md", ".txt"]:
        with open(input_path, "r", encoding="utf-8") as f:
            text = f.read()
        anonymized_text, counts = anonymizer.anonymize(text)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(anonymized_text)
    elif input_path.suffix == ".tex":
        with open(input_path, "r", encoding="utf-8") as f:
            text = f.read()
        anonymized_text, counts = anonymizer.anonymize(text)  # Anonymize whole content
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(anonymized_text)  # Output as .tex
    elif input_path.suffix == ".bib":
        with open(input_path, "r", encoding="utf-8") as bibfile:
            database = bibtexparser.load(bibfile)  # Use bibtexparser to parse BibTeX
            for entry in database.entries:  # Iterate over entries
                for field in list(entry.keys()):  # Anonymize all fields
                    if field in entry:  # Ensure field exists
                        field_text = str(entry[field])
                        anonymized_field, _ = anonymizer.anonymize(field_text)
                        entry[field] = (
                            anonymized_field  # Replace with anonymized version
                        )
            with open(output_path, "w", encoding="utf-8") as bibfile_out:
                bibtexparser.dump(
                    database, bibfile_out
                )  # Output as BibTeX using bibtexparser
