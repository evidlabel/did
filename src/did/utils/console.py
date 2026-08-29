"""Shared Rich console and output helpers."""

import sys

from rich.console import Console
from rich.table import Table

console = Console()


def print_counts(counts: dict, verb: str) -> None:
    """Render entity counts as a rich table. verb is 'found' or 'replaced'."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Entity type", style="dim")
    table.add_column(verb.capitalize(), justify="right")
    keys = [
        ("PERSON", f"person_{verb}"),
        ("ORGANIZATION", f"organization_{verb}"),
        ("EMAIL_ADDRESS", f"email_address_{verb}"),
        ("LOCATION", f"location_{verb}"),
        ("PHONE_NUMBER", f"phone_number_{verb}"),
        ("DATE_NUMBER", f"date_number_{verb}"),
        ("ID_NUMBER", f"id_number_{verb}"),
        ("CODE_NUMBER", f"code_number_{verb}"),
        ("GENERAL_NUMBER", f"general_number_{verb}"),
    ]
    for label, key in keys:
        n = counts.get(key, 0)
        style = "green" if n > 0 else "dim"
        table.add_row(label, f"[{style}]{n}[/{style}]")
    # Bind at call time so redirect_stdout/capsys and embedded callers capture
    # the report rather than writing to the stream active during module import.
    Console(file=sys.stdout).print(table)


def print_verification(report, *, show_text=True) -> None:
    """Render an output-verification report.

    ``show_text`` must be ``False`` wherever the console output could be read by
    an agent that is only meant to see tokens — the findings quote the very PII
    the run exists to remove.
    """
    out = Console(file=sys.stdout)
    if report.is_clean:
        out.print(f"[green]{report.summary()}[/green]")
        if report.retained:
            out.print(
                f"[dim]{len(report.retained)} identifier(s) deliberately retained "
                "via exclusions.[/dim]"
            )
        return

    out.print(f"[bold yellow]{report.summary()}[/bold yellow]")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Severity", style="dim")
    table.add_column("Document", style="dim")
    table.add_column("Line", justify="right")
    table.add_column("Type", style="dim")
    if show_text:
        table.add_column("Text")
    for finding in report.leaks:
        row = ["[red]leak[/red]", finding.document, str(finding.line)]
        row.append(finding.entity_id or finding.entity_type or "")
        if show_text:
            row.append(f"[red]{finding.text}[/red]")
        table.add_row(*row)
    for finding in report.suspected:
        row = ["[yellow]suspect[/yellow]", finding.document, str(finding.line)]
        row.append(finding.entity_type or "")
        if show_text:
            row.append(finding.text)
        table.add_row(*row)
    out.print(table)
    out.print(
        "[dim]Leaks are configured identifiers still present in the output. "
        "Suspects were never in the config — review before sharing.[/dim]"
    )
