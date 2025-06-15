"""CLI interface for the DID tool."""
import click
import yaml
import rich
from rich.console import Console
from rich.syntax import Syntax
from .core.anonymizer import Anonymizer
from pathlib import Path

@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=False,
)
def main():
    """DID (De-ID) Pseudonymizer - A CLI tool to anonymize text files with entity detection."""
    pass

@main.command(help="Extract entities from input text files and generate a YAML configuration file.")
@click.argument('input_files', nargs=-1, required=True, metavar='INPUT_FILES...')
@click.argument('config', required=True, metavar='CONFIG_FILE')
def ex(input_files, config):
    """Extract entities from text files and generate YAML config."""
    anonymizer = Anonymizer()
    try:
        click.echo("=" * 20)
        click.echo("Reading input text files...")
        texts = []
        for input_file in input_files:
            with open(input_file, "r") as f:
                texts.append(f.read())

        click.echo("Detecting entities...")
        anonymizer.detect_entities(texts)

        click.echo("Detected entities:")
        click.echo(f"  Names found: {anonymizer.counts['names_found']}")
        click.echo(f"  Emails found: {anonymizer.counts['emails_found']}")
        click.echo(f"  Addresses found: {anonymizer.counts['addresses_found']}")
        click.echo(f"  Numbers found: {anonymizer.counts['numbers_found']}")
        click.echo(f"  Number patterns found: {anonymizer.counts['patterns_found']}")
        click.echo(f"  CPR found: {anonymizer.counts['cpr_found']}")  # Added for Danish CPR numbers

        yaml_str = anonymizer.generate_yaml()
        click.echo("Writing YAML config...")
        with open(config, "w") as f:
            f.write(yaml_str)

        click.echo(f"Config written to {config}")

        # Print YAML with Rich syntax highlighting
        console = Console()
        syntax = Syntax(yaml_str, "yaml")
        console.print(syntax)

        click.echo("=" * 20)

    except FileNotFoundError as e:
        click.echo("=" * 20)
        click.echo(f"Error: File not found - {e}")
        raise click.Abort()
    except yaml.YAMLError as e:
        click.echo("=" * 20)
        click.echo(f"Error in YAML configuration: {e}")
        raise click.Abort()
    except Exception as e:
        click.echo("=" * 20)
        click.echo(f"Error: {e}")
        raise click.Abort()


@main.command(help="Anonymize input text file using a YAML configuration and save the result.")
@click.argument('input_file', required=True, metavar='INPUT_FILE')
@click.argument('config', required=True, metavar='CONFIG_FILE')
@click.argument('output_file', required=True, metavar='OUTPUT_FILE')
def an(input_file, config, output_file):
    """Pseudonymize text files using YAML config."""
    anonymizer = Anonymizer()
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
        click.echo(f"  CPR replaced: {counts['cpr_replaced']}")  # Added for Danish CPR numbers

        output_file = Path(output_file)
        click.echo(f"Writing to {output_file}...")
        with open(output_file, "w") as f:
            f.write(result)

        # Print the output file with Rich syntax highlighting
        console = Console()
        with open(output_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if output_file.suffix == '.md':
                syntax = Syntax(content, "python", theme="monokai")
            else:
                syntax = Syntax(content, "text", theme="monokai")
            console.print(syntax)

        click.echo("=" * 20)

    except FileNotFoundError as e:
        click.echo("=" * 20)
        click.echo(f"Error: File not found - {e}")
        raise click.Abort()
    except yaml.YAMLError as e:
        click.echo("=" * 20)
        click.echo(f"Error in YAML configuration: {e}")
        raise click.Abort()
    except Exception as e:
        click.echo("=" * 20)
        click.echo(f"Error: {e}")
        raise click.Abort()


if __name__ == "__main__":
    main()
