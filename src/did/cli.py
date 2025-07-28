"""CLI interface for the DID tool."""

import rich_click as click
import yaml
from rich.console import Console
from rich.syntax import Syntax
from pathlib import Path
from .file_utils import extract_text, anonymize_file, md_to_typst
from .core.anonymizer import Anonymizer
import re
import random


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=False,
)
def main():
    """DID (De-ID) Pseudonymizer - A CLI tool to anonymize text files with entity detection."""
    pass


@main.command(
    help="Extract entities from input text files and generate a YAML configuration file."
)
@click.option("--file", "-f", multiple=True, required=True, help="Input files")
@click.option("--config", "-c", default="__temp.yaml", help="Output YAML config file")
@click.option(
    "--language",
    "-l",
    default="en",
    help="Language for entity detection (e.g., 'en', 'da')",
)
def ex(file, config, language):
    """Extract entities from text files and generate YAML config."""
    anonymizer = Anonymizer(language=language)
    console = Console()
    try:
        click.echo("=" * 20)
        click.echo("Reading input text files...")
        texts = []
        for input_file in file:
            file_path = Path(input_file)
            text = extract_text(file_path)
            texts.append(text)

        click.echo("Detecting entities...")
        with console.status(
            f"[bold green]Detecting entities in {language}...[/bold green]"
        ):
            anonymizer.detect_entities(texts)

        click.echo("Detected entities:")
        click.echo(f"  Names found: {anonymizer.counts['names_found']}")
        click.echo(f"  Emails found: {anonymizer.counts['emails_found']}")
        click.echo(f"  Addresses found: {anonymizer.counts['addresses_found']}")
        click.echo(f"  Numbers found: {anonymizer.counts['numbers_found']}")
        click.echo(f"  Number patterns found: {anonymizer.counts['patterns_found']}")
        click.echo(f"  CPR found: {anonymizer.counts['cpr_found']}")

        yaml_str = anonymizer.generate_yaml()
        click.echo("Writing YAML config...")
        with open(config, "w") as f:
            f.write(yaml_str)

        click.echo(f"Config written to {config}")

        syntax = Syntax(yaml_str, "yaml")
        console.print(syntax)

        click.echo("=" * 20)

    except FileNotFoundError as e:
        click.echo(f"Error: File not found - {e}")
        raise click.Abort()
    except yaml.YAMLError as e:
        click.echo(f"Error in YAML configuration: {e}")
        raise click.Abort()
    except Exception as e:
        click.echo(f"Error: {e}")
        raise click.Abort()


@main.command(
    help="Anonymize input text file using a YAML configuration and save the result."
)
@click.option("--file", "-f", required=True, help="Input file")
@click.option("--config", "-c", required=True, help="Config file")
@click.option("--output", "-o", default=None, help="Output file path")
@click.option(
    "--language",
    "-l",
    default="en",
    help="Language for entity detection (e.g., 'en', 'da')",
)
@click.option(
    "--typst",
    "-t",
    type=str,
    default=None,
    help="Directory to export Typst files (main.typ and vars.typ) with real entity values.",
)
def an(file, config, output, language, typst):
    """Pseudonymize text files using YAML config."""
    anonymizer = Anonymizer(language=language)
    input_path = Path(file)
    if output is None and not typst:
        output = str(
            input_path.parent / (input_path.stem + "_anon" + input_path.suffix)
        )
    output_path = Path(output) if output else None
    try:
        click.echo("=" * 20)
        click.echo("Loading config...")
        with open(config, "r") as f:
            config_data = yaml.safe_load(f) or {}
        anonymizer.load_replacements(config_data)

        click.echo(f"Processing {file}...")

        if typst:
            if input_path.suffix not in [".md", ".txt"]:
                click.echo(
                    "Typst export currently supported only for .md and .txt files."
                )
                raise click.Abort()

            # Generate Typst mappings and real values per variant
            var_counters = {
                "names": 0,
                "emails": 0,
                "addresses": 0,
                "numbers": 0,
                "cpr": 0,
            }
            category_mapping = {
                "names": "names_replaced",
                "emails": "emails_replaced",
                "addresses": "addresses_replaced",
                "numbers": "numbers_replaced",
                "cpr": "cpr_replaced",
            }
            typst_mappings = {}  # var -> real_value
            fake_mappings = {}  # var -> fake_value
            counts = {k: 0 for k in anonymizer.counts}
            text = extract_text(input_path)
            all_replacements = []  # (variant, pattern, repl, cat, pat)

            fake_first_names = [
                "Alex",
                "Jordan",
                "Taylor",
                "Casey",
                "Riley",
                "Jamie",
                "Morgan",
                "Drew",
            ]
            fake_last_names = [
                "Smith",
                "Johnson",
                "Williams",
                "Brown",
                "Jones",
                "Garcia",
                "Miller",
                "Davis",
            ]

            def generate_fake_digits(length):
                return "".join(
                    str(random.randint(1 if i == 0 else 0, 9)) for i in range(length)
                )

            def apply_format(variant, fake_digits):
                fake = ""
                d_idx = 0
                for char in variant:
                    if char.isdigit():
                        fake += fake_digits[d_idx]
                        d_idx += 1
                    else:
                        fake += char
                return fake

            for cat in var_counters:
                prefix = {
                    "names": "P",
                    "emails": "E",
                    "addresses": "A",
                    "numbers": "N",
                    "cpr": "C",
                }[cat]
                entities = getattr(anonymizer.entities, cat)
                for entity in entities:
                    var_counters[cat] += 1
                    ent_idx = var_counters[cat]
                    sorted_variants = sorted(entity.variants, key=len, reverse=True)

                    # Category-specific fake setup
                    if cat == "names":
                        fake_first = random.choice(fake_first_names)
                        fake_last = random.choice(fake_last_names)
                        full = max(entity.variants, key=len)
                        full_parts = full.split()
                        presumed_first = full_parts[0] if full_parts else ""
                        presumed_last = full_parts[-1] if full_parts else ""
                    elif cat in ["numbers", "cpr"]:
                        if entity.variants:
                            digits = re.sub(r"\D", "", entity.variants[0])
                            fake_digits = generate_fake_digits(len(digits))
                        else:
                            fake_digits = ""
                    else:
                        fake_digits = ""

                    for v_idx, variant in enumerate(sorted_variants, 1):
                        var = f"{prefix}{ent_idx}V{v_idx}"
                        typst_mappings[var] = variant

                        # Generate fake value
                        if cat == "names":
                            parts = variant.split()
                            num_parts = len(parts)
                            if num_parts == 1:
                                if variant == presumed_first:
                                    fake_var = fake_first
                                elif variant == presumed_last:
                                    fake_var = fake_last
                                else:
                                    fake_var = fake_first
                            elif num_parts == 2:
                                if parts[0].endswith(".") and len(parts[0]) < 4:
                                    fake_var = f"{fake_first[0]}. {fake_last}"
                                elif parts[1].endswith("."):
                                    fake_var = f"{fake_first} {fake_last[0]}."
                                else:
                                    fake_var = f"{fake_first} {fake_last}"
                            elif num_parts == 3:
                                fake_var = f"{fake_first} X. {fake_last}"
                            else:
                                fake_var = f"{fake_first} {fake_last}"
                        elif cat == "emails":
                            fake_var = f"fake{ent_idx}@example.com"
                        elif cat == "addresses":
                            fake_var = f"123 Fake Street, Anytown {ent_idx}, USA"
                        elif cat in ["numbers", "cpr"]:
                            fake_var = apply_format(variant, fake_digits)
                        else:
                            fake_var = "<FAKE>"
                        fake_mappings[var] = fake_var

                        # Prepare replacement
                        repl = f"#({var})"
                        escaped = re.escape(variant)
                        pattern = (
                            escaped if cat == "addresses" else r"\b" + escaped + r"\b"
                        )
                        all_replacements.append(
                            (
                                variant,
                                pattern,
                                repl,
                                cat,
                                entity.pattern if cat == "numbers" else None,
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
                if cat == "numbers" and pat:
                    counts["patterns_found"] += count
                    counts["patterns_replaced"] += count
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

            click.echo("Replacement counts:")
            click.echo(f"  Names replaced: {counts['names_replaced']}")
            click.echo(f"  Emails replaced: {counts['emails_replaced']}")
            click.echo(f"  Addresses replaced: {counts['addresses_replaced']}")
            click.echo(f"  Numbers replaced: {counts['numbers_replaced']}")
            click.echo(f"  Number patterns replaced: {counts['patterns_replaced']}")
            click.echo(f"  CPR replaced: {counts['cpr_replaced']}")

            console = Console()
            click.echo(f"\nTypst files written to {main_path.parent}")
            click.echo(f" - {main_path}")
            click.echo(f" - {vars_path}")
            click.echo(f" - {fake_path}")

            click.echo("\nPreview of vars.typ:")
            with open(vars_path, "r", encoding="utf-8") as f:
                vars_content = f.read()
            syntax = Syntax(vars_content, "rust")
            console.print(syntax)

            click.echo("\nPreview of fakevars.typ:")
            with open(fake_path, "r", encoding="utf-8") as f:
                fake_content = f.read()
            syntax = Syntax(fake_content, "rust")
            console.print(syntax)

            click.echo("\nPreview of main.typ:")
            with open(main_path, "r", encoding="utf-8") as f:
                main_content = f.read()
            syntax = Syntax(main_content, "rust")
            console.print(syntax)

        else:
            counts = anonymize_file(input_path, anonymizer, output_path)

            click.echo("Replacement counts:")
            click.echo(f"  Names replaced: {counts['names_replaced']}")
            click.echo(f"  Emails replaced: {counts['emails_replaced']}")
            click.echo(f"  Addresses replaced: {counts['addresses_replaced']}")
            click.echo(f"  Numbers replaced: {counts['numbers_replaced']}")
            click.echo(f"  Number patterns replaced: {counts['patterns_replaced']}")
            click.echo(f"  CPR replaced: {counts['cpr_replaced']}")

            console = Console()
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
                if output_path.suffix == ".md":
                    syntax = Syntax(content, "markdown", theme="monokai")
                else:
                    syntax = Syntax(content, "text", theme="monokai")
                console.print(syntax)

        click.echo("=" * 20)

    except FileNotFoundError as e:
        click.echo(f"Error: File not found - {e}")
        raise click.Abort()
    except yaml.YAMLError as e:
        click.echo(f"Error in YAML configuration: {e}")
        raise click.Abort()
    except Exception as e:
        click.echo(f"Error: {e}")
        raise click.Abort()


if __name__ == "__main__":
    main()
