"""CLI interface for the DID tool."""

import rich_click as click
import yaml
from rich.console import Console
from rich.syntax import Syntax
from pathlib import Path
from .file_utils import extract_text, anonymize_file
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
@click.option("--file", "-f", multiple=True, required=True, help="Input files")
@click.option("--config", "-c", default="__temp.yaml", help="Output YAML config file")
@click.option("--language", "-l", default="en", help="Language for entity detection (e.g., 'en', 'da')")
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
@click.option("--language", "-l", default="en", help="Language for entity detection (e.g., 'en', 'da')")
def an(file, config, output, language):
    """Pseudonymize text files using YAML config."""
    anonymizer = Anonymizer(language=language)
    input_path = Path(file)
    if output is None:
        output = str(input_path.parent / (input_path.stem + "_anon" + input_path.suffix))
    output_path = Path(output)
    try:
        click.echo("=" * 20)
        click.echo("Loading config...")
        with open(config, "r") as f:
            config_data = yaml.safe_load(f) or {}
        anonymizer.load_replacements(config_data)

        click.echo(f"Processing {file}...")
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
