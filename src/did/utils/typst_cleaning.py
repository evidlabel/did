"""Make extracted text safe to drop into Typst markup.

Ported from evid's ``evid.core.text_cleaning``: scraped/OCR text routinely
carries bare ``#``, ``$``, ``_``, ``[``/``]`` and unbalanced ligatures that
Typst parses as code, math, emphasis, or an unclosed delimiter.
"""

import re

LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "ft",
    "\ufb06": "st",
}

_URL_CONT_END = "/?#&=-+_"
_URL_CONT_START = "/?#&="
_URL_SPLIT_RE = re.compile(r"(https?://\S+)\n(\S+)")

_DEHYPHEN_RE = re.compile(r"([a-zæøåäöü])-\s*\n\s*([a-zæøåäöü])")

_TYPST_SPECIALS = ("#", "*", "$", "_", "`", "<", "[", "]", "~", "^", "{", "}")
_TERM_LIST_RE = re.compile(r"(?m)^([ \t]*)/(?=[^\S\n]*$|[^\S\n]+\S)")


def _rejoin_split_urls(text: str) -> str:
    """Rejoin URLs broken across lines by HTML/PDF text extraction."""

    def _maybe_join(match: re.Match) -> str:
        head, tail = match.group(1), match.group(2)
        if head[-1] in _URL_CONT_END or tail[0] in _URL_CONT_START:
            return head + tail
        return match.group(0)

    prev = None
    while text != prev:
        prev = text
        text = _URL_SPLIT_RE.sub(_maybe_join, text)
    return text


def dehyphenate(text: str) -> str:
    """Rejoin words split by an end-of-line soft hyphen.

    Only joins when both sides of the ``-\\n`` are lowercase letters, so a real
    compound or range (capital or digit after the hyphen) is left alone.
    """
    return _DEHYPHEN_RE.sub(r"\1\2", text)


def escape_typst_specials(text: str) -> str:
    """Backslash every character Typst would otherwise read as syntax."""
    for char in _TYPST_SPECIALS:
        text = text.replace(char, "\\" + char)
    return _TERM_LIST_RE.sub(r"\1\\/", text)


def clean_text_for_typst(text: str) -> str:
    """Expand ligatures and neutralise Typst markup in scraped text.

    Lines containing ``@`` are commented out (Typst reads ``@name`` as a
    reference). Marks are escaped so scraped text cannot open a delimiter it
    never closes; multiple blank lines collapse to one.
    """
    text = _rejoin_split_urls(text)
    for ligature, replacement in LIGATURES.items():
        if ligature in text:
            text = text.replace(ligature, replacement)

    processed_lines = []
    for line in text.split("\n"):
        if "@" in line:
            processed_lines.append("// " + line)
            continue
        processed_lines.append(line)
        stripped = line.strip()
        if stripped and stripped[-1] in ".!?":
            processed_lines.append("")

    text = escape_typst_specials("\n".join(processed_lines))
    return re.sub(r"(\n\s*\n)+", r"\n\n", text)


def typst_str_escape(value: str) -> str:
    """Escape *value* for a Typst double-quoted string literal."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
