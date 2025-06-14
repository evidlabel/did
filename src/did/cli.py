import click
import yaml
import json
from pathlib import Path
from .anonymizer import Anonymizer


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=False,
)
def main():
    """Main CLI group for Markdown Anonymizer."""
    pass


@main.command()
@click.argument('input_files', nargs=-1)
@click.argument('config')
def ex(input_files, config):
    """Extract entities from Markdown files and generate YAML config."""
    anonymizer = Anonymizer()
    try:
        click.echo("=" * 20)
        click.echo("Reading input Markdown files...")
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

        click.echo("Writing YAML config...")
        with open(config, "w") as f:
            f.write(anonymizer.generate_yaml())

        click.echo(f"Config written to {config}")
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


@main.command()
@click.argument('input_file')
@click.argument('config')
@click.argument('output_file')
@click.argument('output_dir')
def an(input_file, config, output_file, output_dir):
    """Pseudonymize Markdown files using YAML config."""
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

        result, counts = anonymizer.anonymize(text, output_dir=output_dir)

        click.echo("Replacement counts:")
        click.echo(f"  Names replaced: {counts['names_replaced']}")
        click.echo(f"  Emails replaced: {counts['emails_replaced']}")
        click.echo(f"  Addresses replaced: {counts['addresses_replaced']}")
        click.echo(f"  Numbers replaced: {counts['numbers_replaced']}")
        click.echo(f"  Number patterns replaced: {counts['patterns_replaced']}")

        click.echo(f"Writing to {output_file}...")
        with open(output_file, "w") as f:
            f.write(result)

        click.echo(f"Entity mapping saved to {output_dir}/entity_mapping.json")
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

