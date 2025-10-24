"""Full command for CLI: chains extraction and typst pseudonymization."""

import sys
from pathlib import Path
from treeparse import command, argument, option
from ..utils.file_utils import extract_text
from ..core.anonymizer import Anonymizer


def full(file, output, language):
    """Extract entities from input file, generate config, then pseudonymize to Typst."""
    input_path = Path(file)
    if not input_path.exists():
        print(f"Error: Input file {file} not found.")
        sys.exit(1)
    if output is None:
        output = str(input_path.with_suffix(".typ"))
    output_path = Path(output)
    if output_path.suffix != ".typ":
        print("Error: Output file must end with .typ")
        sys.exit(1)

    # Config file in local dir
    stem = input_path.stem
    config_file = input_path.parent / f"{stem}_config.yaml"

    try:
        print("=" * 20)
        print("Step 1: Extracting entities...")
        anonymizer = Anonymizer(language=language)
        text = extract_text(input_path)
        anonymizer.detect_entities([text])

        print("Detected entities:")
        print(f"  PERSON found: {anonymizer.counts['person_found']}")
        print(f"  EMAIL_ADDRESS found: {anonymizer.counts['email_address_found']}")
        print(f"  LOCATION found: {anonymizer.counts['location_found']}")
        print(f"  PHONE_NUMBER found: {anonymizer.counts['phone_number_found']}")
        print(f"  DATE_NUMBER found: {anonymizer.counts['date_number_found']}")
        print(f"  ID_NUMBER found: {anonymizer.counts['id_number_found']}")
        print(f"  CODE_NUMBER found: {anonymizer.counts['code_number_found']}")
        print(f"  GENERAL_NUMBER found: {anonymizer.counts['general_number_found']}")

        yaml_str = anonymizer.generate_yaml()
        with open(config_file, "w") as f:
            f.write(yaml_str)

        print(f"Config written to {config_file}")

        print("Step 2: Pseudonymizing to Typst...")
        # Reuse anonymizer or create new? Since we have config, load into new anonymizer
        pseudo_anonymizer = Anonymizer()
        import ruamel.yaml as yaml
        yaml_obj = yaml.YAML()
        with open(config_file, "r") as f:
            config_data = yaml_obj.load(f) or {}
        pseudo_anonymizer.load_replacements(config_data)

        from ..utils.file_utils import export_to_typst
        export_to_typst(input_path, pseudo_anonymizer, output_path)

        print("Replacement counts:")
        print(f"  PERSON replaced: {pseudo_anonymizer.counts['person_replaced']}")
        print(f"  EMAIL_ADDRESS replaced: {pseudo_anonymizer.counts['email_address_replaced']}")
        print(f"  LOCATION replaced: {pseudo_anonymizer.counts['location_replaced']}")
        print(f"  PHONE_NUMBER replaced: {pseudo_anonymizer.counts['phone_number_replaced']}")
        print(f"  DATE_NUMBER replaced: {pseudo_anonymizer.counts['date_number_replaced']}")
        print(f"  ID_NUMBER replaced: {pseudo_anonymizer.counts['id_number_replaced']}")
        print(f"  CODE_NUMBER replaced: {pseudo_anonymizer.counts['code_number_replaced']}")
        print(f"  GENERAL_NUMBER replaced: {pseudo_anonymizer.counts['general_number_replaced']}")

        stem = output_path.stem
        parent = output_path.parent
        vars_path = parent / f"{stem}_vars.typ"
        fake_path = parent / f"{stem}_fakevars.typ"
        print(f"\nTypst files written to {parent}")
        print(f" - {output_path}")
        print(f" - {vars_path}")
        print(f" - {fake_path}")
        print(f" - {config_file}")

        print("=" * 20)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

full_cmd = command(
    name="full",
    help="Extract entities from input file and pseudonymize to Typst files with sensible defaults.",
    callback=full,
    arguments=[
        argument(name="file", arg_type=str, sort_key=0),
    ],
    options=[
        option(
            flags=["--output", "-o"],
            arg_type=str,
            default=None,
            help="Main Typst file path (default: <input>.typ)",
            sort_key=0,
        ),
        option(
            flags=["--language", "-l"],
            arg_type=str,
            default="en",
            help="Language for entity detection (e.g., 'en', 'da')",
            sort_key=1,
        ),
    ],
)
