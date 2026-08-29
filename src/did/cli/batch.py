"""Batch command for CLI: multi-document anonymization to Typst with one shared config.

Produces an *agent-safe* token-only view (``anon/``) and a *secret* key set
(``keys/``) that maps tokens back to real or fake PII. The agent is meant to read
only ``anon/``; everything that can reveal PII lives in ``keys/``.

Output layout::

    <out>/
      anon/                  # AGENT-SAFE — token-only Typst, no real or fake PII
        D1.typ, D2.typ, …    #   body uses #(P1V1) tokens, no #import header;
                             #   named by position because a source filename is
                             #   itself identifying
        manifest.json        #   doc list + token IDs only (no values)
      keys/                  # SECRET — never expose to an agent
        config.yaml          #   token -> real PII (the sensitive map)
        shared_vars.typ      #   #let P1V1 = "<real>"
        shared_fakevars.typ  #   #let P1V1 = "<fake>"
        render_real.typ      #   self-contained: imports shared_vars + all bodies
        render_fake.typ      #   self-contained: imports shared_fakevars + all bodies
"""

import json
import sys
from pathlib import Path

from rich.rule import Rule
from ruamel import yaml
from treeparse import argument, command, option

from ..core.anonymizer import Anonymizer
from ..core.entity_types import DOCUMENT_TITLE
from ..core.verification import TOKEN_RE, verify
from ..utils.console import console, print_counts, print_verification
from ..utils.file_utils import export_to_typst, extract_text

TYPST_INPUT_SUFFIXES = (".md", ".txt", ".pdf", ".docx")


def _token_ids(body: str) -> set:
    """Extract ``P1V1``-style token IDs from a pseudonymized body."""
    return {
        f"{prefix}{number}V{variant}"
        for prefix, number, variant in TOKEN_RE.findall(body)
    }


def _collect_inputs(paths):
    """Expand files and directories into a sorted, de-duplicated list of input docs."""
    collected = []
    seen = set()
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            candidates = sorted(
                q for q in p.iterdir() if q.suffix.lower() in TYPST_INPUT_SUFFIXES
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


def _write_render_file(path, import_name, bodies):
    """Write a self-contained renderable Typst file (one import + all bodies in scope)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'#import "{import_name}": *\n#outline()\n\n')
        for name, body in bodies:
            f.write(f"= {name}\n\n{body}\n\n")


def batch(files, output, language, combine):
    """Anonymize multiple documents with one shared config into token-only Typst."""
    inputs = _collect_inputs(files)
    if not inputs:
        console.print(
            "[red]Error:[/red] No input documents found "
            f"(supported: {', '.join(TYPST_INPUT_SUFFIXES)})."
        )
        sys.exit(1)

    # Resolve to absolute so export_to_typst, which joins vars/fakevars onto each
    # doc's parent dir, places the shared key files in keys/ (an absolute path on
    # the right of a join wins) rather than nesting them under anon/.
    out_dir = Path(output).resolve()
    anon_dir = out_dir / "anon"
    keys_dir = out_dir / "keys"
    anon_dir.mkdir(parents=True, exist_ok=True)
    keys_dir.mkdir(parents=True, exist_ok=True)

    shared_vars = keys_dir / "shared_vars.typ"
    shared_fakevars = keys_dir / "shared_fakevars.typ"
    config_file = keys_dir / "config.yaml"

    try:
        console.print(Rule("batch"))
        console.print(
            f"[bold]Step 1:[/bold] Detecting entities across {len(inputs)} document(s)..."
        )
        anonymizer = Anonymizer(language=language)
        texts = [extract_text(p) for p in inputs]
        anonymizer.detect_entities(texts)

        console.print("[bold]Detected entities:[/bold]")
        print_counts(anonymizer.counts, "found")

        yaml_str = anonymizer.generate_yaml()
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(yaml_str)
        console.print(f"Shared config written to [cyan]{config_file}[/cyan]")

        # Re-load the shared config so every doc pseudonymizes against the same map.
        console.print("[bold]Step 2:[/bold] Pseudonymizing to token-only Typst...")
        pseudo = Anonymizer(language=language)
        yaml_obj = yaml.YAML()
        with open(config_file, encoding="utf-8") as f:
            config_data = yaml_obj.load(f) or {}

        # A filename identifies the case as surely as the body does. Titles join
        # the config as ordinary entities rather than getting a bespoke code
        # path, so replacement, the vars/fakevars files and verification all
        # handle them exactly as they handle a person or an address — including
        # tokenizing a document that names itself in its own body.
        config_data[DOCUMENT_TITLE.config_key] = [
            {"id": f"{DOCUMENT_TITLE.config_key}_{index}", "variants": [src.name]}
            for index, src in enumerate(inputs, 1)
        ]
        with open(config_file, "w", encoding="utf-8") as f:
            yaml_obj.dump(config_data, f)

        pseudo.load_replacements(config_data)

        # (title token, token-only body) for render files / combine. The heading
        # is a token so render_real.typ still compiles to the real filename for a
        # human, while anon/ never spells it out.
        bodies = []
        tokens = set()
        doc_names = [f"D{index}.typ" for index in range(1, len(inputs) + 1)]
        for index, src in enumerate(inputs, 1):
            doc_out = anon_dir / doc_names[index - 1]
            export_to_typst(
                src,
                pseudo,
                doc_out,
                vars_filename=str(shared_vars),
                fakevars_filename=str(shared_fakevars),
                write_imports=False,
            )
            body = doc_out.read_text(encoding="utf-8").strip()
            bodies.append((f"#({DOCUMENT_TITLE.prefix}{index}V1)", body))
            tokens.update(_token_ids(body))

        if combine:
            # Replace per-doc files with a single chapterized token-only document.
            for name in doc_names:
                (anon_dir / name).unlink(missing_ok=True)
            combined = anon_dir / "combined.typ"
            with open(combined, "w", encoding="utf-8") as f:
                f.write("#outline()\n\n")
                for title, body in bodies:
                    f.write(f"= {title}\n\n{body}\n\n")
            anon_docs = [combined.name]
        else:
            anon_docs = list(doc_names)

        # Self-contained renderable views for humans (kept in the secret zone).
        _write_render_file(keys_dir / "render_real.typ", shared_vars.name, bodies)
        _write_render_file(keys_dir / "render_fake.typ", shared_fakevars.name, bodies)

        # Step 3 re-reads what was actually written, not what we think we wrote,
        # so a bug anywhere upstream still shows up here.
        console.print("[bold]Step 3:[/bold] Verifying output...")
        outputs = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(anon_dir.glob("*.typ"))
        }
        report = verify(outputs, config_data, anonymizer=pseudo)
        print_verification(report)

        # Findings quote the PII they report, so the full report belongs with the
        # keys. anon/ gets counts and locations only, or the report would itself
        # be the leak it is warning about.
        with open(keys_dir / "verification.json", "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

        # Title tokens belong to the set's vocabulary even when no body happens
        # to mention a filename, so an agent can correlate D1.typ with DOC1V1.
        tokens.update(
            f"{DOCUMENT_TITLE.prefix}{index}V1" for index in range(1, len(inputs) + 1)
        )

        # Agent-safe manifest: structure + token IDs only, never values.
        manifest = {
            "documents": anon_docs,
            "tokens": sorted(tokens),
            "token_count": len(tokens),
            "verification": report.to_safe_dict(),
        }
        with open(anon_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        console.print("[bold]Replacement counts:[/bold]")
        print_counts(pseudo.counts, "replaced")

        console.print(
            f"\n[bold green]Agent-safe[/bold green] token docs in [cyan]{anon_dir}[/cyan]"
        )
        console.print(
            f"[bold red]Secret[/bold red] key set in [cyan]{keys_dir}[/cyan] "
            "[dim](contains real PII — do not give to an agent)[/dim]"
        )
        console.print(Rule())

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


batch_cmd = command(
    name="batch",
    help=(
        "Anonymize multiple documents with one shared config into token-only Typst "
        "(agent-safe anon/ + secret keys/)."
    ),
    callback=batch,
    arguments=[
        argument(
            name="files",
            arg_type=str,
            nargs="+",
            help="Input documents and/or directories (.md, .txt, .pdf, .docx).",
            sort_key=0,
        ),
    ],
    options=[
        option(
            flags=["--output", "-o"],
            arg_type=str,
            default="anon_out",
            help="Output directory",
            sort_key=0,
        ),
        option(
            flags=["--language", "-l"],
            arg_type=str,
            default="en",
            help="Language for entity detection (e.g., 'en', 'da')",
            sort_key=1,
        ),
        option(
            flags=["--combine"],
            flag=True,
            help="Emit a single chapterized combined.typ instead of per-document files.",
            sort_key=2,
        ),
    ],
)
