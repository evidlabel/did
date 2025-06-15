# DID (De-ID) Pseudonymizer

A CLI tool to anonymize text files based on Presidio, using spaCy for entity detection and rapidfuzz for fuzzy name and number matching.

## Features
- Detects names, email addresses, addresses, and numbers using Presidio with spaCy
- Groups name and number variants (e.g., "John Doe", "Jon Doe"; "1234567890", "1234567") using rapidfuzz
- Extracts entities to generate a YAML config (`did ex`)
- Anonymizes text using YAML config (`did an`)
- Verbose output with detected entities and replacement counts

## Installation
```bash
uv sync
```

## Usage
Extract entities:
```bash
uv run did ex input1.txt [input2.txt ...] config.yaml
```
Anonymize files:
```bash
uv run did an input.txt config.yaml output.txt output_dir
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
```
