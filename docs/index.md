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
This section provides a detailed workflow using examples from the `examples/` directory to demonstrate how to use the DID Pseudonymizer for entity extraction and anonymization.

### Step 1: Entity Extraction
Use the `did ex` command to extract entities from input files and generate a configuration file. For example, to process `test_document.md` from the `examples/` directory and save the configuration to `__temp.yaml`:
```bash
uv run did ex examples/test_document.md examples/__temp.yaml
```

This command will analyze the input file for names, emails, addresses, and numbers, group similar entities using fuzzy matching (via rapidfuzz), and output a YAML configuration file. You can inspect the generated `__temp.yaml` to review the detected entities:
```bash
cat examples/__temp.yaml
```

Example content of `__temp.yaml`:
```yaml
names:
  - id: <PERSON_1>
    variants: ["John Doe", "Jon Doe", "john DOE"]
  - id: <PERSON_2>
    variants: ["Jane Smith", "Jane Smyth"]
emails:
  - id: <EMAIL_1>
    variants: ["john.doe@example.com"]
addresses:
  - id: <ADDRESS_1>
    variants: ["123 One Street, Springfield, US"]
numbers:
  - id: <NUMBER_1>
    variants: ["1234567890", "12 34 56 78"]
    pattern: "\\b\\d{2}\\s+\\d{2}\\s+\\d{2}\\s+\\d{2}\\b"
```

You can manually edit this YAML file to customize replacement IDs or patterns before proceeding to anonymization.

### Step 2: Anonymization
After generating or editing the configuration file, use the `did an` command to anonymize the input file based on the YAML config. For example:
```bash
uv run did an examples/test_document.md examples/__temp.yaml examples/gaai.md
```

This command reads the entities and replacement rules from `__temp.yaml` and replaces detected entities in `test_document.md` with their corresponding IDs (e.g., `<PERSON_1>`, `<NUMBER_1>`). The anonymized output is saved to `gaai.md`.

### Step 3: Review Anonymized Output
Open the output file (`examples/gaai.md`) in a text editor to verify the anonymization. For instance, the line:
```
Contact John Doe at 1234567890 or Jane Smith via 12 34 56 78.
```
will be anonymized to:
```
Contact <PERSON_1> at <NUMBER_1> or <PERSON_2> via <NUMBER_1>.
```

This workflow ensures that sensitive information is consistently replaced across your documents while handling variations in entity formats through fuzzy matching and pattern recognition.

## Configuration
Generated `config.yaml` example:

```yaml
names:
  - id: <PERSON_1>
    variants: ["John Doe", "Jon Doe", "john DOE"]
  - id: <PERSON_2>
    variants: ["Jane Smith", "Jane Smyth"]
emails:
  - id: <EMAIL_1>
    variants: ["john.doe@example.com"]
addresses:
  - id: <ADDRESS_1>
    variants: ["123 One Street, Springfield, US"]
numbers:
  - id: <NUMBER_1>
    variants: ["1234567890", "12 34 56 78"]
    pattern: "\\b\\d{2}\\s+\\d{2}\\s+\\d{2}\\s+\\d{2}\\b"
```

## Testing
Run tests with:
```bash
uv run pytest
```
Tests cover entity detection, fuzzy name and number grouping, and anonymization.
