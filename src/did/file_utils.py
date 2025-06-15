"""Utilities for handling different file types for entity extraction and anonymization."""
from pathlib import Path
import pybtex.database as pybtex_db
from pybtex.database.output.bibtex import Writer as BibTeXWriter
from .anonymizer import Anonymizer  # Import Anonymizer for processing

def extract_text(file_path: Path) -> str:
    """Extract processable text from the file based on its type."""
    if file_path.suffix in ['.md', '.txt']:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    elif file_path.suffix == '.tex':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()  # Treat as plain text for now, preserving content
    elif file_path.suffix == '.bib':
        database = pybtex_db.parse_and_parse_file(str(file_path))
        text_content = []
        for entry in database.entries.values():
            for field, value in entry.fields.items():
                if field in ['author', 'editor', 'title']:  # Focus on entity-rich fields
                    text_content.append(str(value))
        return ' '.join(text_content)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")

def anonymize_file(input_path: Path, config: dict, output_path: Path):
    """Anonymize the file based on its type and write to output in the same format."""
    anonymizer = Anonymizer()
    anonymizer.load_replacements(config)
    
    if input_path.suffix in ['.md', '.txt']:
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
        anonymized_text, counts = anonymizer.anonymize(text)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(anonymized_text)
    elif input_path.suffix == '.tex':
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
        anonymized_text, counts = anonymizer.anonymize(text)  # Anonymize whole content
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(anonymized_text)  # Output as .tex
    elif input_path.suffix == '.bib':
        database = pybtex_db.parse_and_parse_file(str(input_path))
        for entry in database.entries.values():
            for field in ['author', 'editor', 'title']:  # Target fields for anonymization
                if field in entry.fields:
                    field_text = str(entry.fields[field])
                    anonymized_field, _ = anonymizer.anonymize(field_text)
                    entry.fields[field] = anonymized_field  # Replace with anonymized version
        with open(output_path, 'w', encoding='utf-8') as f:
            BibTeXWriter().write(database, f)  # Output as BibTeX
