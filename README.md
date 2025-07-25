[![Test](https://github.com/evidlabel/did/actions/workflows/pytest.yaml/badge.svg)](https://github.com/evidlabel/did/actions/workflows/pytest.yml)![Version](https://img.shields.io/github/v/release/evidlabel/did)

# DID (De-ID) Pseudonymizer

A CLI tool to anonymize Markdown, plain text, TeX, and BibTeX files with spaCy-based entity detection and automatic YAML configuration.

## Features
- Detects names, emails, addresses, phone numbers, and CPR numbers using Presidio with spaCy
- Groups name and number variants using rapidfuzz
- Extracts entities to generate a YAML config (`did ex`)
- Anonymizes text using YAML config (`did an`), preserving file formats
- Supports English (`en`) and Danish (`da`)

## Installation
```bash
uv pip install . 
```

## Quick Usage
Extract entities:
```bash
uv run did ex -f input.txt -c config.yaml
```
Anonymize:
```bash
uv run did an -f input.txt -c config.yaml -o output.txt
```

For details, see the [documentation](docs/index.md).
