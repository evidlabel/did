# DID (De-ID) Pseudonymizer

A CLI tool to anonymize Markdown files using spaCy for entity detection and rapidfuzz for fuzzy name and number matching.

## Features
- Detects names, email addresses, addresses, and numbers using spaCy
- Groups name and number variants (e.g., "John Doe", "Jon Doe"; "1234567890", "1234567") using rapidfuzz
- Extracts entities to generate a YAML config (`did extract`)
- Anonymizes text using YAML config (`did anonymize`)
- Verbose output with detected entities and replacement counts

## Installation
```bash
poetry install
```

## Usage
Extract entities:
```bash
poetry run did extract -i input1.md [input2.md ...] -c config.yaml
```
Anonymize files:
```bash
poetry run did anonymize -i input1.md [input2.md ...] -c config.yaml [-o output1.md [output2.md ...]]
```

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

For details, see the [documentation](docs/index.md).
