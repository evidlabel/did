"""Pseudo commands for CLI."""

import sys
from pathlib import Path
from treeparse import group, command, argument, option
import ruamel.yaml as yaml
from rich.console import Console
from rich.syntax import Syntax
from ..file_utils import extract_text, anonymize_file, export_to_typst
from ..core.anonymizer import Anonymizer
import re
import random


def plain(file, config, output):
    """Pseudonymize to plain output file."""
    if config is None:
        print("Error: --config is required")
        sys.exit(1)
    anonymizer = Anonymizer()
    input_path = Path(file)
    if output is None:
        output = str(
            input_path.parent / (input_path.stem + "_anon" + input_path.suffix)
        )
    output_path = Path(output)
    try:
        print("=" * 20)
        print("Loading config...")
        yaml_obj = yaml.YAML()
        with open(config, "r") as f:
            config_data = yaml_obj.load(f) or {}
        anonymizer.load_replacements(config_data)

        print(f"Processing {file}...")
        counts = anonymize_file(input_path, anonymizer, output_path)

        print("Replacement counts:")
        print(f"  PERSON replaced: {counts['person_replaced']}")
        print(f"  EMAIL_ADDRESS replaced: {counts['email_address_replaced']}")
        print(f"  LOCATION replaced: {counts['location_replaced']}")
        print(f"  PHONE_NUMBER replaced: {counts['phone_number_replaced']}")
        print(f"  DATE_NUMBER replaced: {counts['date_number_replaced']}")
        print(f"  ID_NUMBER replaced: {counts['id_number_replaced']}")
        print(f"  CODE_NUMBER replaced: {counts['code_number_replaced']}")
        print(f"  GENERAL_NUMBER replaced: {counts['general_number_replaced']}")

        console = Console()
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
            if output_path.suffix == ".md":
                syntax = Syntax(content, "markdown", theme="monokai")
            else:
                syntax = Syntax(content, "text", theme="monokai")
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


def typst(file, config, output):
    """Pseudonymize to Typst files."""
    if config is None:
        print("Error: --config is required")
        sys.exit(1)
    anonymizer = Anonymizer()
    input_path = Path(file)
    if output is None:
        output = str(input_path.with_suffix(".typ"))
    main_path = Path(output)
    if main_path.suffix != ".typ":
        print("Error: Output file must end with .typ")
        sys.exit(1)
    stem = main_path.stem
    parent = main_path.parent
    vars_path = parent / f"{stem}_vars.typ"
    fake_path = parent / f"{stem}_fakevars.typ"
    try:
        print("=" * 20)
        print("Loading config...")
        yaml_obj = yaml.YAML()
        with open(config, "r") as f:
            config_data = yaml_obj.load(f) or {}
        anonymizer.load_replacements(config_data)

        print(f"Processing {file}...")

        export_to_typst(input_path, anonymizer, main_path)

        print("Replacement counts:")
        print(f"  PERSON replaced: {anonymizer.counts['person_replaced']}")
        print(f"  EMAIL_ADDRESS replaced: {anonymizer.counts['email_address_replaced']}")
        print(f"  LOCATION replaced: {anonymizer.counts['location_replaced']}")
        print(f"  PHONE_NUMBER replaced: {anonymizer.counts['phone_number_replaced']}")
        print(f"  DATE_NUMBER replaced: {anonymizer.counts['date_number_replaced']}")
        print(f"  ID_NUMBER replaced: {anonymizer.counts['id_number_replaced']}")
        print(f"  CODE_NUMBER replaced: {anonymizer.counts['code_number_replaced']}")
        print(f"  GENERAL_NUMBER replaced: {anonymizer.counts['general_number_replaced']}")

        console = Console()
        print(f"\nTypst files written to {main_path.parent}")
        print(f" - {main_path}")
        print(f" - {vars_path}")
        print(f" - {fake_path}")

        print(f"\nPreview of {vars_path.name}:")
        with open(vars_path, "r", encoding="utf-8") as f:
            vars_content = f.read()
        syntax = Syntax(vars_content, "rust")
        console.print(syntax)

        print(f"\nPreview of {fake_path.name}:")
        with open(fake_path, "r", encoding="utf-8") as f:
            fake_content = f.read()
        syntax = Syntax(fake_content, "rust")
        console.print(syntax)

        print(f"\nPreview of {main_path.name}:")
        with open(main_path, "r", encoding="utf-8") as f:
            main_content = f.read()
        syntax = Syntax(main_content, "rust")
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

pseudo_group = group(
    name="pseudo", help="Pseudonymize input text file using a YAML configuration."
)

plain_cmd = command(
    name="plain",
    help="Pseudonymize to plain output file.",
    callback=plain,
    arguments=[
        argument(name="file", arg_type=str, sort_key=0),
    ],
    options=[
        option(
            flags=["--config", "-c"],
            arg_type=str,
            help="Config file",
            sort_key=0,
        ),
        option(
            flags=["--output", "-o"],
            arg_type=str,
            default=None,
            help="Output file path",
            sort_key=1,
        ),
    ],
)
pseudo_group.commands.append(plain_cmd)

typst_cmd = command(
    name="typst",
    help="Pseudonymize to Typst files, creating <input_stem>.typ, <input_stem>_vars.typ, <input_stem>_fakevars.typ.",
    callback=typst,
    arguments=[
        argument(name="file", arg_type=str, sort_key=0),
    ],
    options=[
        option(
            flags=["--config", "-c"],
            arg_type=str,
            help="Config file",
            sort_key=0,
        ),
        option(
            flags=["--output", "-o"],
            arg_type=str,
            default=None,
            help="Main Typst file path (default: <input>.typ)",
            sort_key=1,
        ),
    ],
)
pseudo_group.commands.append(typst_cmd)
