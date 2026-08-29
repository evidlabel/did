"""CLI package."""

from treeparse import cli

from .batch import batch_cmd
from .extract import extract_cmd
from .full import full_cmd
from .gui import gui_cmd
from .pseudo import pseudo_group
from .verify import verify_cmd

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
app.commands.append(batch_cmd)
app.commands.append(verify_cmd)
app.commands.append(gui_cmd)
app.subgroups.append(pseudo_group)


def main():
    app.run()
