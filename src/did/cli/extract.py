"""Extract command for CLI."""

import sys
from pathlib import Path
from treeparse import command, argument, option
import ruamel.yaml as yaml
from rich.console import Console
from rich.syntax import Syntax
from ..utils.file_utils import extract_text
from ..core.anonymizer import Anonymizer


def extract(files, config, language):
    """Extract entities from text files and generate YAML config."""
    if not files:
        print("Error: At least one input file is required.")
        sys.exit(1)
    anonymizer = Anonymizer(language=language)
    console = Console()
    try:
        print("=" * 20)
        print("Reading input text files...")
        texts = []
        for input_file in files:
            file_path = Path(input_file)
            text = extract_text(file_path)
            texts.append(text)

        print("Detecting entities...")
        with console.status(
            f"[bold green]Detecting entities in {language}...[/bold green]"
        ):
            anonymizer.detect_entities(texts)

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
        print("Writing YAML config...")
        with open(config, "w") as f:
            f.write(yaml_str)

        print(f"Config written to {config}")

        syntax = Syntax(yaml_str, "yaml")
        console.print(syntax)

        print("=" * 20)

    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error in YAML configuration: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

extract_cmd = command(
    name="extract",
    help="Extract entities from input text files and generate a YAML configuration file.",
    callback=extract,
    arguments=[
        argument(name="files", arg_type=str, nargs="*", sort_key=0),
    ],
    options=[
        option(
            flags=["--config", "-c"],
            arg_type=str,
            default="__temp.yaml",
            help="Output YAML config file",
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
