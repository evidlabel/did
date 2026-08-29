"""Verify command: re-scan pseudonymized output for surviving identifiers.

Detection runs on source text; this runs on the result. It answers the separate
question of whether anything identifying survived replacement — a check on the
content of a document *before it is sent*, which can be run against output this
session did not produce.
"""

import json
import sys
from pathlib import Path

from rich.rule import Rule
from ruamel import yaml
from treeparse import argument, command, option

from ..core.verification import verify as run_verify
from ..utils.console import console, print_verification

VERIFY_INPUT_SUFFIXES = (".typ", ".md", ".txt")


def _collect_outputs(paths):
    """Expand files and directories into a sorted, de-duplicated list of documents."""
    collected = []
    seen = set()
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            candidates = sorted(
                q for q in p.rglob("*") if q.suffix.lower() in VERIFY_INPUT_SUFFIXES
            )
        elif p.exists():
            candidates = [p]
        else:
            console.print(f"[red]Error:[/red] Input path {raw} not found.")
            sys.exit(1)
        for c in candidates:
            resolved = c.resolve()
            if resolved not in seen:
                seen.add(resolved)
                collected.append(c)
    return collected


def verify_cmd_impl(files, config, not_names, report, quiet):
    """Check pseudonymized documents for identifiers that survived replacement."""
    inputs = _collect_outputs(files)
    if not inputs:
        console.print(
            "[red]Error:[/red] No documents found "
            f"(supported: {', '.join(VERIFY_INPUT_SUFFIXES)})."
        )
        sys.exit(1)

    config_path = Path(config)
    if not config_path.exists():
        console.print(f"[red]Error:[/red] Config {config} not found.")
        sys.exit(1)

    try:
        with open(config_path, encoding="utf-8") as f:
            config_data = yaml.YAML().load(f) or {}

        exclusions = []
        if not_names:
            exclusions = json.loads(Path(not_names).read_text(encoding="utf-8"))
            if not isinstance(exclusions, list):
                raise ValueError(f"{not_names} must contain a JSON list of names.")

        outputs = {p.name: p.read_text(encoding="utf-8") for p in inputs}

        if not quiet:
            console.print(Rule("verify"))
            console.print(f"Checking {len(outputs)} document(s) against {config_path}")
        result = run_verify(outputs, config_data, not_names=exclusions)
        if not quiet:
            print_verification(result)
            console.print(Rule())

        if report:
            Path(report).write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if not quiet:
                console.print(f"Report written to [cyan]{report}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Findings warn; they never fail the command. DID over-detects by design, so
    # a non-zero exit would turn expected false positives into broken pipelines.


verify_cmd = command(
    name="verify",
    help=(
        "Re-scan pseudonymized output for identifiers that survived replacement "
        "(quality control before a document is shared)."
    ),
    callback=verify_cmd_impl,
    arguments=[
        argument(
            name="files",
            arg_type=str,
            nargs="+",
            help="Pseudonymized documents and/or directories (.typ, .md, .txt).",
            sort_key=0,
        ),
    ],
    options=[
        option(
            flags=["--config", "-c"],
            arg_type=str,
            required=True,
            help="Entity configuration YAML used to produce the output.",
            sort_key=0,
        ),
        option(
            flags=["--not-names", "-n"],
            arg_type=str,
            default=None,
            help="JSON list of names deliberately left unpseudonymized.",
            sort_key=1,
        ),
        option(
            flags=["--report", "-r"],
            arg_type=str,
            default=None,
            help="Write the full JSON report here (contains PII — treat as sensitive).",
            sort_key=2,
        ),
        option(
            flags=["--quiet", "-q"],
            flag=True,
            help="Suppress console output; useful with --report.",
            sort_key=3,
        ),
    ],
)
