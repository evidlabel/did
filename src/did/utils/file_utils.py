"""Utilities for handling different file types."""

import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from html import unescape
from pathlib import Path

import bibtexparser
from docx import Document
from docx.table import Table
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..core import entity_types
from ..core.anonymizer import Anonymizer
from ..core.verification import TOKEN_RE as _TOKEN_RE
from .typst_cleaning import clean_text_for_typst, typst_str_escape

# A page score at or above this is prose. Below it, the next reader is tried.
# Native text that is one repeated glyph, or Poppler text full of controls and
# ÿ-as-space, stays under the floor so OCR can win.
_READING_FLOOR = 0.5
_PDF_TOOL_TIMEOUT_S = 120
_TYPST_TIMEOUT_S = 300
_TESSERACT_LANGS = ("swe", "dan", "eng")

PDF_SUFFIXES = (".pdf",)
DOCX_SUFFIXES = (".docx",)
# Plain-text-ish formats read directly (with a latin-1 fallback for old files).
TEXT_SUFFIXES = (
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".rst",
    ".org",
    ".log",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".tex",
    ".bib",
    ".rtf",
    ".srt",
    ".vtt",
)
SUPPORTED_SUFFIXES = PDF_SUFFIXES + DOCX_SUFFIXES + TEXT_SUFFIXES


@dataclass(frozen=True)
class Reading:
    """The one text of a file for this run.

    Detection, preview, and publish all use ``text``. ``content_hash`` ties a
    saved entity review to this reading. ``readable`` is false when every
    reader stayed under the floor; publish must refuse that.
    """

    path: Path
    text: str
    reader: str
    content_hash: str
    readable: bool


def _extract_docx_text(file_path: Path) -> str:
    """Extract body paragraphs and tables from a DOCX in document order."""
    lines = []
    for block in Document(file_path).iter_inner_content():
        if isinstance(block, Table):
            lines.extend(
                "\t".join(cell.text.strip() for cell in row.cells) for row in block.rows
            )
        else:
            lines.append(block.text)
    return "\n".join(lines)


def _reading_score(text: str) -> float:
    """Rank a candidate reading. Ordinary prose is at or above ``_READING_FLOOR``."""
    if not text or not text.strip():
        return 0.0
    letters = [character.casefold() for character in text if character.isalpha()]
    count = len(letters)
    variety = len(set(letters))
    controls = sum(
        1 for character in text if ord(character) < 32 and character not in "\n\r\t\f"
    )
    control_ratio = controls / max(len(text), 1)
    separators = text.count("ÿ")
    spaces = text.count(" ")
    broken_separator = separators >= 8 and separators > max(spaces, 1) * 0.2
    words = [
        word for word in text.split() if any(character.isalpha() for character in word)
    ]
    long_words = sum(1 for word in words if len(word) >= 40)
    collapsed = variety <= 2 and count >= 40
    run_on = bool(words) and long_words >= 3 and variety <= 6
    if collapsed or broken_separator or run_on or control_ratio > 0.02:
        return 0.05 * min(variety, 10) / 10
    if count < 8:
        return 0.6 if variety else 0.2
    return min(1.0, 0.5 + min(variety, 20) / 40)


def _as_reading(path: Path, text: str, reader: str, *, readable: bool) -> Reading:
    digest = sha256(text.encode("utf-8")).hexdigest()
    return Reading(Path(path), text, reader, digest, readable)


def _resolved(obj):
    if obj is None:
        return None
    getter = getattr(obj, "get_object", None)
    return getter() if getter else obj


def _content_stats(data: bytes) -> dict[str, int]:
    """Count text, curve, and image operators outside strings and comments."""
    text_ops = curves = draws = 0
    index = 0
    length = len(data)
    while index < length:
        char = data[index]
        if char in b" \t\r\n\x0c\x00":
            index += 1
            continue
        if char == ord("%"):
            newline = data.find(b"\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char == ord("("):
            index += 1
            depth = 1
            while index < length and depth:
                if data[index] == ord("\\"):
                    index += 2
                    continue
                if data[index] == ord("("):
                    depth += 1
                elif data[index] == ord(")"):
                    depth -= 1
                index += 1
            continue
        if char == ord("<"):
            if index + 1 < length and data[index + 1] == ord("<"):
                index += 2
                continue
            end = data.find(b">", index + 1)
            index = length if end < 0 else end + 1
            continue
        if char == ord(">"):
            index += 2 if index + 1 < length and data[index + 1] == ord(">") else 1
            continue
        if char in b"[]{}":
            index += 1
            continue
        start = index
        index += 1
        while index < length and data[index] not in b" \t\r\n\x0c\x00()<>[]{}/%":
            index += 1
        token = data[start:index]
        if token in (b"Tj", b"TJ", b"'", b'"'):
            text_ops += 1
        elif token in (b"c", b"v", b"y"):
            curves += 1
        elif token == b"Do":
            draws += 1
    return {
        "text_ops": text_ops,
        "curves": curves,
        "draws": draws,
        "size": length,
    }


@dataclass(frozen=True)
class _PdfPageSignals:
    has_font: bool
    has_text_ops: bool
    has_image: bool
    has_form: bool
    outline_text: bool
    painted: bool


def _page_signals(page) -> _PdfPageSignals:
    """Describe how a thin page was painted, without trusting extracted text."""
    try:
        contents = page.get_contents()
        data = contents.get_data() if contents is not None else b""
        stats = _content_stats(data or b"")
        resources = _resolved(page.get("/Resources"))
        fonts = None
        xobjects = None
        if resources is not None:
            fonts = _resolved(resources.get("/Font"))
            xobjects = _resolved(resources.get("/XObject"))
        has_font = bool(fonts)
        kinds = set()
        if xobjects:
            for item in xobjects.values():
                obj = _resolved(item)
                if obj is not None:
                    kinds.add(str(obj.get("/Subtype")))
        has_image = "/Image" in kinds
        has_form = "/Form" in kinds or stats["draws"] > 0
        has_text_ops = stats["text_ops"] > 0
        outline_text = (
            not has_text_ops and stats["curves"] >= 20 and stats["size"] >= 800
        )
        painted = bool(
            has_font
            or has_text_ops
            or has_image
            or has_form
            or outline_text
            or stats["size"] > 40
        )
    except (PdfReadError, KeyError, TypeError, ValueError, AttributeError, OSError):
        return _PdfPageSignals(False, False, False, False, False, False)
    return _PdfPageSignals(
        has_font, has_text_ops, has_image, has_form, outline_text, painted
    )


def _run_tool(argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            timeout=_PDF_TOOL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _pdftotext_page(path: Path, page_number: int) -> str | None:
    """Poppler text for one page. Evince uses this library."""
    executable = shutil.which("pdftotext")
    if executable is None:
        return None
    result = _run_tool(
        [
            executable,
            "-layout",
            "-enc",
            "UTF-8",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            str(path),
            "-",
        ]
    )
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").replace("\f", "\n")


def _tesseract_bin() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "tesseract"
    return str(local) if local.is_file() else None


def _tesseract_language() -> str | None:
    executable = _tesseract_bin()
    if executable is None:
        return None
    result = _run_tool([executable, "--list-langs"])
    if result is None:
        return None
    installed = set(
        (result.stdout + result.stderr).decode("utf-8", errors="replace").split()
    )
    chosen = [code for code in _TESSERACT_LANGS if code in installed]
    return "+".join(chosen) if chosen else None


def _ocr_page(path: Path, page_number: int) -> str | None:
    """Rasterize one painted page and read it, when Tesseract is installed."""
    tesseract = _tesseract_bin()
    pdftoppm = shutil.which("pdftoppm")
    language = _tesseract_language()
    if tesseract is None or pdftoppm is None or language is None:
        return None
    with tempfile.TemporaryDirectory() as directory:
        prefix = str(Path(directory) / "page")
        rendered = _run_tool(
            [
                pdftoppm,
                "-png",
                "-r",
                "300",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                str(path),
                prefix,
            ]
        )
        if rendered is None or rendered.returncode != 0:
            return None
        images = sorted(Path(directory).glob("page*.png"))
        if not images:
            return None
        recognized = _run_tool(
            [tesseract, str(images[0]), "stdout", "-l", language, "--psm", "6"]
        )
    if recognized is None or recognized.returncode != 0:
        return None
    return recognized.stdout.decode("utf-8", errors="replace")


def _choose_pdf_page(
    path: Path, page_number: int, native: str, signals: _PdfPageSignals
):
    """Return ``(text, reader)`` for one page. The first reader above the floor wins."""
    native_letters = sum(character.isalpha() for character in native)
    if not signals.painted or (
        _reading_score(native) >= _READING_FLOOR and native_letters >= 8
    ):
        return native, "native"
    options = [("native", native)]
    popped = _pdftotext_page(path, page_number)
    if popped is not None:
        options.append(("poppler", popped))
        if _reading_score(popped) >= _READING_FLOOR:
            return popped, "poppler"
    ocr = _ocr_page(path, page_number)
    if ocr is not None:
        options.append(("ocr", ocr))
        if _reading_score(ocr) >= _READING_FLOOR:
            return ocr, "ocr"
    reader, text = max(options, key=lambda item: _reading_score(item[1]))
    return text, reader


def _read_pdf(path: Path) -> Reading:
    document = PdfReader(path)
    parts = []
    readers = []
    readable = True
    order = {"native": 0, "poppler": 1, "ocr": 2}
    for number, page in enumerate(document.pages, start=1):
        native = page.extract_text() or ""
        signals = _page_signals(page)
        text, reader = _choose_pdf_page(path, number, native, signals)
        parts.append(text)
        readers.append(reader)
        if signals.painted and _reading_score(text) < _READING_FLOOR:
            readable = False
    deepest = max(readers, key=lambda name: order[name], default="native")
    return _as_reading(path, "\n".join(parts), deepest, readable=readable)


def _read_tex(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    body_text = re.sub(r"\\[\w]+.*?(\s|})", " ", content)
    body_text = re.sub(
        r"\\begin\{.*?\}.*?\\end\{.*?\}", " ", body_text, flags=re.DOTALL
    )
    return re.sub(r"\s+", " ", body_text).strip()


def _read_bib(path: Path) -> str:
    if hasattr(bibtexparser, "load"):
        with open(path, encoding="utf-8") as bibfile:
            database = bibtexparser.load(bibfile)
        return " ".join(
            str(value) for entry in database.entries for value in entry.values()
        )
    library = bibtexparser.parse_file(str(path))
    return " ".join(
        str(field.value) for entry in library.entries for field in entry.fields
    )


def _read_text(path: Path) -> str:
    """Decode a text file, refusing binaries and tolerating legacy encodings."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise ValueError(f"Unsupported binary file: {path.name}")
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode text file: {path.name}")


def _read_html(path: Path) -> str:
    content = _read_text(path)
    content = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", content)
    content = re.sub(r"(?s)<[^>]+>", " ", content)
    content = unescape(content)
    return re.sub(r"[ \t]+", " ", content).strip()


def _read_rtf(path: Path) -> str:
    content = _read_text(path)
    content = re.sub(r"\\'[0-9a-fA-F]{2}", " ", content)
    content = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", content)
    content = content.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", content).strip()


def read_document(file_path: Path) -> Reading:
    """Read a document once. Callers after this use ``Reading.text``.

    Unknown suffixes are decoded as text when they are not binary, so any
    plain-text file can be added, not just the listed extensions.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _read_pdf(path)
    if suffix in DOCX_SUFFIXES:
        return _as_reading(path, _extract_docx_text(path), "native", readable=True)
    if suffix in {".html", ".htm"}:
        return _as_reading(path, _read_html(path), "native", readable=True)
    if suffix == ".rtf":
        return _as_reading(path, _read_rtf(path), "native", readable=True)
    if suffix == ".tex":
        return _as_reading(path, _read_tex(path), "native", readable=True)
    if suffix == ".bib":
        return _as_reading(path, _read_bib(path), "native", readable=True)
    return _as_reading(path, _read_text(path), "native", readable=True)


def extract_text(file_path: Path) -> str:
    """Text of :func:`read_document`. Prefer the :class:`Reading` when the hash matters."""
    return read_document(file_path).text


def anonymize_file(input_path: Path, anonymizer: Anonymizer, output_path: Path) -> dict:
    """Anonymize the file using the provided anonymizer and return counts."""
    counts = dict.fromkeys(anonymizer.counts, 0)
    suffix = input_path.suffix.lower()
    if suffix == ".bib":
        if hasattr(bibtexparser, "load"):
            with open(input_path, encoding="utf-8") as bibfile:
                database = bibtexparser.load(bibfile)
            for entry in database.entries:
                for field in list(entry.keys()):
                    if field in entry:
                        anonymized_field, field_counts = anonymizer.anonymize(
                            str(entry[field])
                        )
                        entry[field] = anonymized_field
                        for key in counts:
                            counts[key] += field_counts[key]
            with open(output_path, "w", encoding="utf-8") as bibfile_out:
                bibtexparser.dump(database, bibfile_out)
        else:
            library = bibtexparser.parse_file(str(input_path))
            field_cls = bibtexparser.model.Field
            for entry in library.entries:
                updates = []
                for field in entry.fields:
                    anonymized_field, field_counts = anonymizer.anonymize(
                        str(field.value)
                    )
                    updates.append((field.key, anonymized_field, field_counts))
                for key, anonymized_field, field_counts in updates:
                    entry.set_field(field_cls(key=key, value=anonymized_field))
                    for count_key in counts:
                        counts[count_key] += field_counts[count_key]
            bibtexparser.write_file(str(output_path), library)
    elif suffix in SUPPORTED_SUFFIXES:
        if suffix == ".tex":
            text = input_path.read_text(encoding="utf-8", errors="replace")
        else:
            text = extract_text(input_path)
        anonymized_text, field_counts = anonymizer.anonymize(text)
        output_path.write_text(anonymized_text, encoding="utf-8")
        for k in counts:
            counts[k] += field_counts[k]
    else:
        raise ValueError(f"Unsupported file type: {input_path.suffix}")
    return counts


def md_to_typst(md: str) -> str:
    """Simple Markdown to Typst converter."""
    # Headings
    md = re.sub(r"^#\s+(.*)$", r"= \1", md, flags=re.MULTILINE)
    md = re.sub(r"^##\s+(.*)$", r"== \1", md, flags=re.MULTILINE)
    md = re.sub(r"^###\s+(.*)$", r"=== \1", md, flags=re.MULTILINE)
    md = re.sub(r"^####\s+(.*)$", r"==== \1", md, flags=re.MULTILINE)
    # Italic and bold (process italic first to avoid conflict)
    md = re.sub(r"\*(.*?)\*", r"_\1_", md)
    md = re.sub(r"_(.*?)_", r"_\1_", md)
    md = re.sub(r"\*\*(.*?)\*\*", r"*\1*", md)
    md = re.sub(r"__(.*?)__", r"*\1*", md)
    # Code
    md = re.sub(r"`(.*?)`", r"`\1`", md)
    # Links
    md = re.sub(r"\[(.*?)\]\((.*?)\)", r'#link("\2")[\1]', md)
    return md


def _stash_tokens(text: str):
    tokens = []

    def stash(match):
        tokens.append(match.group(0))
        return f"\x00{len(tokens) - 1}\x00"

    return _TOKEN_RE.sub(stash, text), tokens


def clean_typst_body(text: str) -> str:
    """Neutralise Typst markup in rendered text without touching DID tokens.

    Scraped and OCR'd text carries bare ``#``, ``$``, ``_``, ``[``/``]`` and
    unbalanced delimiters that Typst parses as code. Placeholders such as
    ``#(P1V1)`` must survive as markup, so they are stashed before cleaning.
    """
    protected, tokens = _stash_tokens(text)
    cleaned = clean_text_for_typst(protected)
    for index, token in enumerate(tokens):
        cleaned = cleaned.replace(f"\x00{index}\x00", token)
    return cleaned


def compile_typst_pdf(main_typ: Path, pdf_path: Path) -> None:
    """Compile a Typst document to *pdf_path* with the ``typst`` executable.

    Raises :class:`ValueError` with a readable message when Typst is missing or
    the document does not compile, so the GUI can surface it instead of dying.
    """
    main_typ = Path(main_typ)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["typst", "compile", str(main_typ), str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=_TYPST_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "The 'typst' executable was not found. Install Typst "
            "(https://typst.app) to export PDFs."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Typst timed out compiling {main_typ.name}.") from exc
    except subprocess.CalledProcessError as exc:
        lines = [
            line.strip()
            for line in (exc.stderr or exc.stdout or "").splitlines()
            if line.strip()
        ]
        # Prefer the "error: …" line; the caret line below it is not useful.
        message = next((line for line in lines if "error:" in line), None)
        if message is None:
            message = lines[-1] if lines else "no output"
        raise ValueError(f"Typst could not compile {main_typ.name}: {message}") from exc


def export_to_typst(
    input_path: Path,
    anonymizer: Anonymizer,
    main_path: Path,
    vars_filename: str | None = None,
    fakevars_filename: str | None = None,
    write_imports: bool = True,
    source_text: str | None = None,
) -> None:
    """Export anonymized content to Typst files.

    When ``write_imports`` is False, the main file is written as token-only text
    (no ``#import`` header). This is used for agent-safe output where the token
    document must not point at — or reveal the existence of — the vars/fakevars
    files. The vars/fakevars files themselves are still written.
    """
    if input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Typst export does not support {input_path.suffix or 'extensionless'} files."
        )

    stem = main_path.stem
    parent = main_path.parent
    vars_path = parent / (vars_filename or f"{stem}_vars.typ")
    fake_path = parent / (fakevars_filename or f"{stem}_fakevars.typ")

    # Generate Typst mappings. Driven by the registry so that every category the
    # replacer can emit a token for also gets a `#let` here — keeping its own
    # list is what once left `#(O1V1)` in bodies with nothing to resolve it.
    var_counters = dict.fromkeys(entity_types.PREFIX_MAP, 0)
    typst_mappings = {}
    fake_mappings = {}

    def generate_fake_digits(length):
        return "".join(
            str(random.randint(1 if i == 0 else 0, 9)) for i in range(length)
        )

    def apply_format(variant, fake_digits):
        fake = ""
        d_idx = 0
        for char in variant:
            if char.isdigit():
                if d_idx < len(fake_digits):
                    fake += fake_digits[d_idx]
                    d_idx += 1
                else:
                    fake += str(random.randint(0, 9))
            else:
                fake += char
        return fake

    number_cats = entity_types.NUMBER_CATEGORIES
    for cat in var_counters:
        prefix = entity_types.PREFIX_MAP[cat]
        entities = getattr(anonymizer.entities, cat, [])
        for entity in entities:
            var_counters[cat] += 1
            ent_idx = var_counters[cat]

            if cat in number_cats:
                if entity.variants:
                    max_digit_len = max(
                        len(re.sub(r"\D", "", v)) for v in entity.variants
                    )
                    fake_digits = generate_fake_digits(max_digit_len)
                else:
                    fake_digits = ""
            else:
                fake_digits = ""

            for v_idx, variant in enumerate(entity.variants, 1):
                var = f"{prefix}{ent_idx}V{v_idx}"
                typst_mappings[var] = variant

                if cat == "person":
                    fake_var = f"Person{ent_idx} Var{v_idx}"
                elif cat == "organization":
                    fake_var = f"Organization{ent_idx} Var{v_idx}"
                elif cat == "email_address":
                    fake_var = f"email{ent_idx}var{v_idx}@example.com"
                elif cat == "location":
                    fake_var = f"Address{ent_idx} Var{v_idx}"
                elif cat == "url":
                    fake_var = f"https://example.com/url{ent_idx}v{v_idx}"
                elif cat == "document_title":
                    fake_var = f"Document{ent_idx}"
                elif cat in number_cats:
                    fake_var = apply_format(variant, fake_digits)
                else:
                    fake_var = "<FAKE>"
                fake_mappings[var] = fake_var

    # Anonymize the same reading detection used. A second extract can disagree
    # with that reading when OCR is involved.
    text = source_text if source_text is not None else read_document(input_path).text
    anonymized_text, _ = anonymizer.anonymize(text)

    # Post-processing: Add empty line after lines ending with period
    lines = anonymized_text.split("\n")
    new_lines = []
    for line in lines:
        new_lines.append(line)
        if line.strip().endswith("."):
            new_lines.append("")
    anonymized_text = "\n".join(new_lines)

    # Remove sequences of multiple blank lines, reduce to single blank line
    cleaned_lines = []
    for line in new_lines:
        if line.strip() == "":
            if not cleaned_lines or cleaned_lines[-1].strip() != "":
                cleaned_lines.append(line)
        else:
            cleaned_lines.append(line)
    anonymized_text = "\n".join(cleaned_lines)

    parent.mkdir(parents=True, exist_ok=True)

    # Write vars.typ
    with open(vars_path, "w", encoding="utf-8") as f:
        for var, val in typst_mappings.items():
            f.write(f'#let {var} = "{typst_str_escape(val)}"\n')

    # Write fakevars.typ
    with open(fake_path, "w", encoding="utf-8") as f:
        for var, val in fake_mappings.items():
            f.write(f'#let {var} = "{typst_str_escape(val)}"\n')

    # Write main.typ
    body = (
        md_to_typst(anonymized_text)
        if input_path.suffix.lower() in {".md", ".markdown", ".pdf"}
        else anonymized_text
    )
    with open(main_path, "w", encoding="utf-8") as f:
        if write_imports:
            f.write(f'#import "{fake_path.name}": *\n')
            f.write(f'// #import "{vars_path.name}": *\n')
            f.write(
                "// Uncomment the above line to show the document with real PII instead of fake data.\n\n"
            )
        f.write(clean_typst_body(body))
