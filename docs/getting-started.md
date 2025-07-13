# Getting Started

1. Install the tool: `uv sync`
2. Extract entities: `uv run did ex -f input1.txt -f input2.txt -c config.yaml --language en`
3. Edit `config.yaml` if needed (e.g., adjust IDs or patterns).
4. Anonymize files: `uv run did an -f input.txt -c config.yaml -o output.txt --language en`
5. View the anonymized output in a text editor.

Use `--language da` for Danish support.
