"""Compile token-only documents to placeholder PDFs for an evid handoff.

``did anon`` writes agent-safe token Typst to ``anon/``, but evid ingests PDFs,
not Typst. This renders each document with its tokens rewritten to
non-identifying labels (``[PERSON 1]`` by default) and compiles the result, so a
case folder can be handed on as ordinary PDFs. Only PDFs are written — no key
set, no real values.
"""

from __future__ import annotations

import re
import tempfile
from hashlib import sha256
from pathlib import Path

from faker import Faker

from ..core.entity_types import PLACEHOLDER_WORDS
from .typst_cleaning import typst_str_escape

#: Label styles ``--render`` accepts.
RENDER_MODES = ("placeholder", "redacted", "synthetic")

_TOKEN_ID_RE = re.compile(r"([A-Z]+)(\d+)V(\d+)")

_FAKER_LOCALES = {"da": "da_DK", "sv": "sv_SE"}


def _synthetic_value(fake: Faker, prefix: str, number: str) -> str:
    factories = {
        "P": fake.name,
        "O": fake.company,
        "E": fake.safe_email,
        "A": lambda: fake.address().replace("\n", ", "),
        "PH": fake.phone_number,
        "DT": lambda: fake.date_object().isoformat(),
        "ID": fake.ssn,
        "CD": lambda: fake.bothify("??-####").upper(),
        "GN": lambda: str(fake.random_number(digits=6)),
        "URL": fake.url,
        "DOC": lambda: f"Document {number}",
    }
    factory = factories.get(prefix)
    return factory() if factory else "[REDACTED]"


def token_labels(tokens, mode="placeholder", language="en", seed="did") -> dict:
    """Map each ``<prefix><n>V<m>`` token id to the label the PDF shows.

    A token's variant is dropped: every written form of one identity shares the
    same label, which is what matters when reading the document.
    """
    if mode not in RENDER_MODES:
        raise ValueError(f"Unknown render mode: {mode!r}")
    fake = Faker(_FAKER_LOCALES.get(language, "en_US"))
    labels = {}
    for token in tokens:
        match = _TOKEN_ID_RE.fullmatch(token)
        if match is None:
            continue
        prefix, number = match.group(1), match.group(2)
        if mode == "placeholder":
            word = PLACEHOLDER_WORDS.get(prefix, prefix)
            label = f"[{word} {number}]"
        elif mode == "redacted":
            label = "[REDACTED]"
        else:
            digest = sha256(f"{seed}:{prefix}:{number}".encode()).digest()
            fake.seed_instance(int.from_bytes(digest[:8], "big"))
            label = _synthetic_value(fake, prefix, number)
        labels[token] = label
    return labels


def _labels_file(build: Path, labels: dict) -> Path:
    path = build / "pdf_labels.typ"
    with open(path, "w", encoding="utf-8") as f:
        for token in sorted(labels):
            f.write(f'#let {token} = "{typst_str_escape(labels[token])}"\n')
    return path


def render_pdfs(
    bodies,
    tokens,
    out_dir,
    *,
    combine=False,
    mode="placeholder",
    language="en",
    seed="did",
):
    """Compile a placeholder PDF per document (or one combined) into *out_dir*.

    ``bodies`` is ``[(title_token, token_only_body), ...]`` as ``did anon``
    builds it. The Typst sources live in a temp dir, so *out_dir* receives only
    the PDFs. Returns the paths written.
    """
    from .file_utils import compile_typst_pdf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = token_labels(tokens, mode, language, seed)
    written = []
    with tempfile.TemporaryDirectory() as td:
        build = Path(td)
        _labels_file(build, labels)
        if combine:
            main = build / "combined.typ"
            parts = ['#import "pdf_labels.typ": *\n']
            for title_token, body in bodies:
                parts.append(f"= {title_token}\n\n{body}\n")
            main.write_text("\n".join(parts), encoding="utf-8")
            pdf = out_dir / "combined.pdf"
            compile_typst_pdf(main, pdf)
            written.append(pdf)
        else:
            for index, (title_token, body) in enumerate(bodies, 1):
                main = build / f"D{index}.typ"
                main.write_text(
                    f'#import "pdf_labels.typ": *\n\n= {title_token}\n\n{body}\n',
                    encoding="utf-8",
                )
                pdf = out_dir / f"D{index}.pdf"
                compile_typst_pdf(main, pdf)
                written.append(pdf)
    return written
