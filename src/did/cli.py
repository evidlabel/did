"""CLI interface for the DID tool."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent / "treeparse" / "src"))
from treeparse import cli, command, argument, option
import ruamel.yaml as yaml  # Switched from PyYAML to ruamel.yaml
from rich.console import Console
from rich.syntax import Syntax
from .file_utils import extract_text, anonymize_file, md_to_typst
from .core.anonymizer import Anonymizer
import re
import random


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
        print(f"  CPR_NUMBER found: {anonymizer.counts['cpr_number_found']}")

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


def pseudo(file, config, output, language, typst):
    """Pseudonymize text files using YAML config."""
    if config is None:
        print("Error: --config is required")
        sys.exit(1)
    anonymizer = Anonymizer(language=language)
    input_path = Path(file)
    if output is None and not typst:
        output = str(
            input_path.parent / (input_path.stem + "_anon" + input_path.suffix)
        )
    output_path = Path(output) if output else None
    try:
        print("=" * 20)
        print("Loading config...")
        yaml_obj = yaml.YAML()  # Use ruamel.yaml YAML object for loading
        with open(config, "r") as f:
            config_data = yaml_obj.load(f) or {}
        anonymizer.load_replacements(config_data)

        print(f"Processing {file}...")

        if typst:
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
                "cpr_number": 0,
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
                "cpr_number": "cpr_number_replaced",
            }
            typst_mappings = {}  # var -> real_value
            fake_mappings = {}  # var -> fake_value
            counts = {k: 0 for k in anonymizer.counts}
            text = extract_text(input_path)
            all_replacements = []  # (variant, pattern, repl, cat, pat)

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
                            # Fallback for unexpected longer variants
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
                "cpr_number",
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
                    "cpr_number": "C",
                }[cat]
                entities = getattr(anonymizer.entities, cat)
                for entity in entities:
                    var_counters[cat] += 1
                    ent_idx = var_counters[cat]
                    sorted_variants = sorted(entity.variants, key=len, reverse=True)

                    # Category-specific fake setup with max digit length
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

                        # Generate fake value
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

                        # Prepare replacement
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
                                "cpr_number",
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

            # Sort replacements by variant length descending
            sorted_replacements = sorted(
                all_replacements, key=lambda x: len(x[0]), reverse=True
            )

            # Apply replacements
            for variant, pattern, repl, cat, pat in sorted_replacements:
                count = len(re.findall(pattern, text))
                replaced_key = category_mapping[cat]
                found_key = replaced_key.replace("_replaced", "_found")
                counts[found_key] += count
                counts[replaced_key] += count
                text = re.sub(pattern, repl, text)

            anonymized_text = text

            # Create output paths
            temp_dir = Path(typst)
            temp_dir.mkdir(parents=True, exist_ok=True)
            main_path = temp_dir / "main.typ"
            vars_path = temp_dir / "vars.typ"
            fake_path = temp_dir / "fakevars.typ"

            # Write vars.typ
            with open(vars_path, "w", encoding="utf-8") as f:
                for var, val in typst_mappings.items():
                    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                    f.write(f'#let {var} = "{escaped}"\n')

            # Write fakevars.typ
            with open(fake_path, "w", encoding="utf-8") as f:
                for var, val in fake_mappings.items():
                    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                    f.write(f'#let {var} = "{escaped}"\n')

            # Write main.typ
            with open(main_path, "w", encoding="utf-8") as f:
                f.write('#import "vars.typ": *\n\n')
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
            print(f"  CPR_NUMBER replaced: {counts['cpr_number_replaced']}")

            console = Console()
            print(f"\nTypst files written to {main_path.parent}")
            print(f" - {main_path}")
            print(f" - {vars_path}")
            print(f" - {fake_path}")

            print("\nPreview of vars.typ:")
            with open(vars_path, "r", encoding="utf-8") as f:
                vars_content = f.read()
            syntax = Syntax(vars_content, "rust")
            console.print(syntax)

            print("\nPreview of fakevars.typ:")
            with open(fake_path, "r", encoding="utf-8") as f:
                fake_content = f.read()
            syntax = Syntax(fake_content, "rust")
            console.print(syntax)

            print("\nPreview of main.typ:")
            with open(main_path, "r", encoding="utf-8") as f:
                main_content = f.read()
            syntax = Syntax(main_content, "rust")
            console.print(syntax)

        else:
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
            print(f"  CPR_NUMBER replaced: {counts['cpr_number_replaced']}")

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


app = cli(
    name="did",
    help="DID (De-ID) Pseudonymizer - A CLI tool to anonymize text files with entity detection.",
    max_width=120,
    show_types=True,
    show_defaults=True,
    line_connect=True,
    theme="monochrome",
)

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
app.commands.append(extract_cmd)

pseudo_cmd = command(
    name="pseudo",
    help="Pseudonymize input text file using a YAML configuration and save the result.",
    callback=pseudo,
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
        option(
            flags=["--language", "-l"],
            arg_type=str,
            default="en",
            help="Language for entity detection (e.g., 'en', 'da')",
            sort_key=2,
        ),
        option(
            flags=["--typst", "-t"],
            arg_type=str,
            default=None,
            help="Directory to export Typst files (main.typ and vars.typ) with real entity values.",
            sort_key=3,
        ),
    ],
)
app.commands.append(pseudo_cmd)


def main():
    app.run()


if __name__ == "__main__":
    main()
