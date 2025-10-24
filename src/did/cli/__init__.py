"""CLI package."""

from treeparse import cli, command, argument, option, group
from rich.console import Console
from .extract import extract_cmd
from .pseudo import pseudo_group
from .full import full_cmd

app = cli(
    name="did",
    help="DID (De-ID) Pseudonymizer - A CLI tool to anonymize text files with entity detection.",
    max_width=120,
    show_types=True,
    show_defaults=True,
    line_connect=True,
)

app.commands.append(extract_cmd)
app.commands.append(full_cmd)
app.subgroups.append(pseudo_group)


def main():
    app.run()
