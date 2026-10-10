"""CLI package."""

from treeparse import cli

from .anon import anon_cmd
from .extract import extract_cmd
from .gui import gui_cmd
from .models import models_cmd

app = cli(
    name="did",
    help="DID (De-ID) Pseudonymizer - A CLI tool to anonymize text files with entity detection.",
    max_width=120,
    show_types=True,
    show_defaults=True,
    line_connect=True,
)

app.commands.append(extract_cmd)
app.commands.append(anon_cmd)
app.commands.append(gui_cmd)
app.commands.append(models_cmd)


def main():
    app.run()
