"""CLI interface for the DID tool."""

import click
import yaml
import rich
from rich.console import Console
from rich.syntax import Syntax
from pathlib import Path
from .file_utils import extract_text
from .core.anonymizer import Anonymizer


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
@click.argument("input_files", nargs=-1, required=True, metavar="INPUT_FILES...")
@click.option("--config", "-c", default="__temp.yaml", help="Output YAML config file")
@click.option(
    "--language", default="en", help="Language for entity detection (e.g., 'en', 'da')"
)
def ex(input_files, config, language):
    """Extract entities from text files and generate YAML config."""
    anonymizer = Anonymizer(language=language)  # Pass selected language
    console = Console()
    try:
        click.echo("=" * 20)
        click.echo("Reading input text files...")
        texts = []
        for input_file in input_files:
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
@click.argument("input_file", required=True, metavar="INPUT_FILE")
@click.argument("config", required=True, metavar="CONFIG_FILE")
@click.option("--output", default=None, help="Output file path")
@click.option(
    "--language",
    "-l",
    default="en",
    help="Language for entity detection (e.g., 'en', 'da')",
)
def an(input_file, config, output, language):
    """Pseudonymize text files using YAML config."""
    anonymizer = Anonymizer(language=language)  # Pass selected language
    if output is None:
        input_path = Path(input_file)
        output = str(
            input_path.parent / (input_path.stem + "_anon" + input_path.suffix)
        )
    try:
        click.echo("=" * 20)
        click.echo("Loading config...")
        with open(config, "r") as f:
            config_data = yaml.safe_load(f) or {}
        anonymizer.load_replacements(config_data)

        click.echo(f"Processing {input_file}...")
        with open(input_file, "r") as f:
            text = f.read()

        result, counts = anonymizer.anonymize(text)

        click.echo("Replacement counts:")
        click.echo(f"  Names replaced: {counts['names_replaced']}")
        click.echo(f"  Emails replaced: {counts['emails_replaced']}")
        click.echo(f"  Addresses replaced: {counts['addresses_replaced']}")
        click.echo(f"  Numbers replaced: {counts['numbers_replaced']}")
        click.echo(f"  Number patterns replaced: {counts['patterns_replaced']}")
        click.echo(f"  CPR replaced: {counts['cpr_replaced']}")

        output_file = Path(output)
        click.echo(f"Writing to {output_file}...")
        with open(output_file, "w") as f:
            f.write(result)

        console = Console()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
            if output_file.suffix == ".md":
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
