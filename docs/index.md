# DID (De-ID) Pseudonymizer Documentation

A CLI tool to anonymize text, Markdown, TeX, and BibTeX files based on Presidio, with spaCy-based entity detection and rapidfuzz for fuzzy name and number matching.

## Getting Started
1. Install the tool: `uv sync`
2. Extract entities: `uv run did ex input1.txt [input2.txt ...] config.yaml`
3. Anonymize files: `uv run did an input.txt config.yaml output.txt`
4. View output in text editor.

## Features

### Entity Detection and Anonymization
- **Extract**: Detects names, variants, emails, addresses, and numbers using Presidio with spaCy and groups name and number variants with rapidfuzz (`did ex`).
- **Anonymize**: Replaces detected entities using YAML config (`did an`), outputting in the same format as input (e.g., .bib to .bib).
- **Entities**: Names (`PERSON`), emails (`LIKE_EMAIL`), addresses (custom patterns), numbers (exact and patterns).
- Auto-generates YAML configuration with grouped name and number variants.
- Verbose output with entity counts.
- Supports .md, .txt, .tex, and .bib files, applying changes only to content while preserving syntax.

## Usage Workflow
...  # (Rest unchanged for brevity)
