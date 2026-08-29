"""Syntax highlighter for Typst pseudonymization tokens in the preview pane.

Replaces the old `re.sub`-into-HTML approach with a proper QSyntaxHighlighter:
each `#(P1V1)`-style token is coloured by its entity prefix.
"""

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from did.core.entity_types import prefix_pattern

from ..pipeline import PLACEHOLDER_WORDS
from .theme import token_colors

# Longest prefixes first so e.g. "PH" matches before "P". Also matches the
# written-out form `[PERSON 1]` produced by pipeline.to_written_out.
TOKEN_RE = re.compile(
    rf"#\(({prefix_pattern()})\d+V\d+\)"
    r"|\[("
    + "|".join(sorted(set(PLACEHOLDER_WORDS.values()), key=len, reverse=True))
    + r") \d+\]"
)
_WORD_TO_PREFIX = {word: prefix for prefix, word in PLACEHOLDER_WORDS.items()}


class TypstTokenHighlighter(QSyntaxHighlighter):
    """Colour `#(<PREFIX><n>V<m>)` and `[<WORD> <n>]` tokens by entity type."""

    def __init__(self, document):
        super().__init__(document)
        self._formats = {}
        colors = token_colors()
        missing = set(PLACEHOLDER_WORDS) - set(colors)
        if missing:
            raise ValueError(
                f"Missing preview token colors: {', '.join(sorted(missing))}"
            )
        for prefix, color in colors.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            # Regular weight keeps thin punctuation and wider letters from
            # appearing like different shades through font antialiasing.
            fmt.setFontWeight(QFont.Weight.Normal)
            self._formats[prefix] = fmt

    def highlightBlock(self, text):
        # Preview contents change in place as output modes and documents switch.
        # Explicitly discard spans from the previous block contents so punctuation
        # that moves into an old token position cannot inherit its colour.
        self.setFormat(0, len(text), QTextCharFormat())
        for m in TOKEN_RE.finditer(text):
            prefix = m.group(1) or _WORD_TO_PREFIX.get(m.group(2))
            fmt = self._formats.get(prefix)
            if fmt is not None:
                self.setFormat(m.start(), m.end() - m.start(), fmt)
