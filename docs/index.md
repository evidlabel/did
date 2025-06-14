# Markdown Anonymizer Documentation

A CLI tool to anonymize Markdown files with spaCy-based entity detection and rapidfuzz for fuzzy name and number matching.

## Getting Started
1. Install the tool using Poetry: `poetry install`
2. Extract entities: `poetry run anon2 extract -i input1.md [input2.md ...] -c config.yaml`
3. Anonymize files: `poetry run anon2 anonymize -i input1.md [input2.md ...] -c config.yaml [-o output1.md [output2.md ...]]`
4. View output in VS Code.

## Features

### Entity Detection and Anonymization
- **Extract**: Detects names, variants, emails, addresses, and numbers using spaCy and groups name and number variants with rapidfuzz (`anon2 extract`).
- **Anonymize**: Replaces detected entities using YAML config (`anon2 anonymize`).
- **Entities**: Names (`PERSON`), emails (`LIKE_EMAIL`), addresses (custom patterns), numbers (exact and patterns).
- Auto-generates YAML configuration with grouped name and number variants.
- Verbose output with entity counts.

## Configuration
Generated `config.yaml` example:
```yaml
names:
  - { id: person1, variants: ["John Doe", "Jon Doe"] }
  - { id: person2, variants: ["Jane Smith", "Jane Smyth"] }
emails:
  example@email.com: Email1
addresses:
  "Oneway 23, 4355, Herning, Denmark": Address1
numbers:
  - { id: number1, variants: ["1234567890", "1234567"] }
  - { id: number2, variants: ["12 34 56 78"], pattern: "\\d{2}\\s+\\d{2}\\s+\\d{2}\\s+\\d{2}" }
```

## Testing
Run tests with:
```bash
poetry run pytest
```
Tests cover entity detection, fuzzy name and number grouping, and anonymization.
