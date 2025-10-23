"""Pseudo commands for CLI."""

import sys
from pathlib import Path
from treeparse import group, command, argument, option
import ruamel.yaml as yaml
from rich.console import Console
from rich.syntax import Syntax
from ..file_utils import extract_text, anonymize_file, md_to_typst
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

        if input_path.suffix not in [".md", ".txt"]:
            print("Typst export currently supported only for .md and .txt files.")
            sys.exit(1)

        # Generate Typst mappings and real values per variant
        var_counters = {
            "person": 0,
            "email_address": 0,
            "location": 0,
            "phone_number": 0,
            "date_number": 0,
            "id_number": 0,
            "code_number": 0,
            "general_number": 0,
        }
        category_mapping = {
            "person": "person_replaced",
            "email_address": "email_address_replaced",
            "location": "location_replaced",
            "phone_number": "phone_number_replaced",
            "date_number": "date_number_replaced",
            "id_number": "id_number_replaced",
            "code_number": "code_number_replaced",
            "general_number": "general_number_replaced",
        }
        typst_mappings = {}
        fake_mappings = {}
        counts = {k: 0 for k in anonymizer.counts}
        text = extract_text(input_path)
        all_replacements = []

        def generate_fake_digits(length):
            return "".join(
                str(random.randint(1 if i == 0 else 0, 9)) for i in range(length)
            )

        def apply_format(variant, fake_digits):
            fake = ""
            d_idx = 0
            for char in variant:
                if char.isdigit():
                    if d_idx < len(fake_digits):
                        fake += fake_digits[d_idx]
                        d_idx += 1
                    else:
                        fake += str(random.randint(0, 9))
                else:
                    fake += char
            return fake

        number_cats = [
            "phone_number",
            "date_number",
            "id_number",
            "code_number",
            "general_number",
        ]
        for cat in var_counters:
            prefix = {
                "person": "P",
                "email_address": "E",
                "location": "A",
                "phone_number": "PH",
                "date_number": "DT",
                "id_number": "ID",
                "code_number": "CD",
                "general_number": "GN",
            }[cat]
            entities = getattr(anonymizer.entities, cat)
            for entity in entities:
                var_counters[cat] += 1
                ent_idx = var_counters[cat]
                sorted_variants = sorted(entity.variants, key=len, reverse=True)

                if cat in number_cats:
                    if entity.variants:
                        max_digit_len = max(
                            len(re.sub(r"\D", "", v)) for v in entity.variants
                        )
                        fake_digits = generate_fake_digits(max_digit_len)
                    else:
                        fake_digits = ""
                else:
                    fake_digits = ""

                for v_idx, variant in enumerate(sorted_variants, 1):
                    var = f"{prefix}{ent_idx}V{v_idx}"
                    typst_mappings[var] = variant

                    if cat == "person":
                        fake_var = f"Person{ent_idx} Var{v_idx}"
                    elif cat == "email_address":
                        fake_var = f"email{ent_idx}var{v_idx}@example.com"
                    elif cat == "location":
                        fake_var = f"Address{ent_idx} Var{v_idx}"
                    elif cat in number_cats:
                        fake_var = apply_format(variant, fake_digits)
                    else:
                        fake_var = "<FAKE>"
                    fake_mappings[var] = fake_var

                    repl = f"#({var})"
                    escaped = re.escape(variant)
                    if (
                        cat
                        in [
                            "person",
                            "phone_number",
                            "date_number",
                            "id_number",
                            "code_number",
                            "general_number",
                        ]
                        and "\n" in variant
                    ):
                        pattern = escaped
                    elif cat == "location":
                        pattern = escaped
                    else:
                        pattern = r"\b" + escaped + r"\b"
                    all_replacements.append(
                        (
                            variant,
                            pattern,
                            repl,
                            cat,
                            entity.pattern if cat in number_cats else None,
                        )
                    )

        sorted_replacements = sorted(
            all_replacements, key=lambda x: len(x[0]), reverse=True
        )

        for variant, pattern, repl, cat, pat in sorted_replacements:
            count = len(re.findall(pattern, text))
            replaced_key = category_mapping[cat]
            found_key = replaced_key.replace("_replaced", "_found")
            counts[found_key] += count
            counts[replaced_key] += count
            text = re.sub(pattern, repl, text)

        anonymized_text = text

        parent.mkdir(parents=True, exist_ok=True)

        with open(vars_path, "w", encoding="utf-8") as f:
            for var, val in typst_mappings.items():
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                f.write(f'#let {var} = "{escaped}"\n')

        with open(fake_path, "w", encoding="utf-8") as f:
            for var, val in fake_mappings.items():
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                f.write(f'#let {var} = "{escaped}"\n')

        with open(main_path, "w", encoding="utf-8") as f:
            f.write(f'#import "{vars_path.name}": *\n\n')
            if input_path.suffix == ".md":
                f.write(md_to_typst(anonymized_text))
            else:
                f.write(anonymized_text)

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
