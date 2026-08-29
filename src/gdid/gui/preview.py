"""Document preview panel: read-only editor, find bar, and state badge.

MainWindow remains the orchestrator (project lifecycle, extract/anonymize, and
preview context-menu actions that touch YAML / project state). This module owns
the preview widget tree and pure preview UI behaviour (find, state, empty page).
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from did.core import entity_types

from .. import pipeline
from .highlighter import TypstTokenHighlighter

_MONO = QFont("monospace")
_MONO.setStyleHint(QFont.StyleHint.Monospace)
PREVIEW_MONO = QFont(_MONO)
PREVIEW_MONO.setStyleStrategy(
    PREVIEW_MONO.styleStrategy() | QFont.StyleStrategy.PreferNoShaping
)

PREVIEW_TOKEN_RE = re.compile(
    rf"#\(({entity_types.prefix_pattern()})(\d+)V\d+\)"
    r"|\[("
    + "|".join(sorted(set(pipeline.PLACEHOLDER_WORDS.values()), key=len, reverse=True))
    + r") (\d+)\]"
)
PREFIX_ENTITY_TYPE = {
    entity.prefix: entity.config_key for entity in entity_types.ENTITY_TYPES
}
WORD_ENTITY_TYPE = {
    word: PREFIX_ENTITY_TYPE[prefix]
    for prefix, word in pipeline.PLACEHOLDER_WORDS.items()
}

_PREVIEW_STATES = {
    "EMPTY": ("No document", "#666", "transparent"),
    "RAW": ("RAW · contains personal data", "#7a2500", "#ffd8c2"),
    "PROCESSING": ("PROCESSING", "#594400", "#fff0b3"),
    "PSEUDONYMIZED": ("PSEUDONYMIZED", "#145c2e", "#ccebd6"),
    "REDACTED": ("REDACTED", "#5b2500", "#f2d4b5"),
    "SYNTHETIC": ("SYNTHETIC · FAKER", "#3e246b", "#ded0f5"),
    "ERROR": ("ERROR", "#7a0018", "#ffd0d8"),
    "VERSION": ("VERSION · READ-ONLY", "#234e70", "#d5e8f6"),
}


def token_at_position(text: str, position: int):
    """Return ``(entity_type, possible_ids)`` for the token under *position*.

    *possible_ids* covers both compact (``P1``) and expanded (``PERSON_1``)
    identity forms so callers can match either encoding in the entity table.
    """
    match = next(
        (
            candidate
            for candidate in PREVIEW_TOKEN_RE.finditer(text)
            if candidate.start() <= position <= candidate.end()
        ),
        None,
    )
    if match is None:
        return None
    prefix, token_number, word, written_number = match.groups()
    number = token_number or written_number
    entity_type = PREFIX_ENTITY_TYPE[prefix] if prefix else WORD_ENTITY_TYPE[word]
    possible_ids = {f"{entity_type}_{number}"}
    if prefix:
        possible_ids.add(f"{prefix}{number}")
    return entity_type, possible_ids


class PreviewEdit(QPlainTextEdit):
    """Read-only preview whose context menu is extended by the main window."""

    def __init__(self, menu_extender, parent=None):
        super().__init__(parent)
        self._menu_extender = menu_extender

    def build_context_menu(self):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        self._menu_extender(menu)
        return menu

    def contextMenuEvent(self, event):
        self.build_context_menu().exec(event.globalPos())


class PreviewPanel(QWidget):
    """Document preview column: state badge, find bar, empty page, and editor."""

    def __init__(
        self,
        *,
        menu_extender,
        on_new_project=None,
        on_open_project=None,
        on_add_documents=None,
        parent=None,
    ):
        super().__init__(parent)
        self.edit = PreviewEdit(menu_extender)
        self.edit.setReadOnly(True)
        self.edit.setFont(PREVIEW_MONO)
        self.edit.setPlaceholderText("Select a document to preview it.")
        self.highlighter = TypstTokenHighlighter(self.edit.document())

        self.stack = QStackedWidget()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.addStretch()
        empty_title = QLabel("<h2>Start a pseudonymization project</h2>")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_copy = QLabel(
            "Create a named project, open an existing one, or add documents "
            "to begin. Documents are anonymized automatically."
        )
        empty_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_copy.setWordWrap(True)
        empty_copy.setMaximumWidth(520)
        empty_copy.setStyleSheet("color: #888; font-size: 13px;")
        empty_copy_row = QHBoxLayout()
        empty_copy_row.addStretch()
        empty_copy_row.addWidget(empty_copy)
        empty_copy_row.addStretch()
        empty_layout.addLayout(empty_copy_row)
        empty_actions = QHBoxLayout()
        empty_actions.addStretch()
        empty_new = QPushButton("New project")
        if on_new_project is not None:
            empty_new.clicked.connect(on_new_project)
        empty_actions.addWidget(empty_new)
        empty_open = QPushButton("Open project…")
        if on_open_project is not None:
            empty_open.clicked.connect(on_open_project)
        empty_actions.addWidget(empty_open)
        empty_add = QPushButton("Add documents…")
        if on_add_documents is not None:
            empty_add.clicked.connect(on_add_documents)
        empty_actions.addWidget(empty_add)
        empty_actions.addStretch()
        empty_layout.addLayout(empty_actions)
        empty_layout.addStretch()
        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.edit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel("<b>Document preview</b>"))
        header_layout.addStretch()
        self.state_label = QLabel()
        header_layout.addWidget(self.state_label)
        layout.addWidget(header)

        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(0, 0, 0, 4)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in document…")
        self.find_input.textChanged.connect(self.find_text)
        self.find_input.returnPressed.connect(self.find_text)
        find_layout.addWidget(self.find_input, 1)
        find_previous = QPushButton("Previous")
        find_previous.clicked.connect(lambda: self.find_text(backward=True))
        find_layout.addWidget(find_previous)
        find_next = QPushButton("Next")
        find_next.clicked.connect(self.find_text)
        find_layout.addWidget(find_next)
        find_close = QPushButton("Close")
        find_close.clicked.connect(self.hide_find)
        find_layout.addWidget(find_close)
        self.find_bar.hide()
        layout.addWidget(self.find_bar)
        layout.addWidget(self.stack)

        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self.edit)
        self._find_shortcut.activated.connect(self.show_find)
        self._find_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_input)
        self._find_escape.activated.connect(self.hide_find)
        self._find_previous_shortcut = QShortcut(
            QKeySequence(Qt.KeyboardModifier.ShiftModifier | Qt.Key.Key_Return),
            self.find_input,
        )
        self._find_previous_shortcut.activated.connect(
            lambda: self.find_text(backward=True)
        )

        self.set_state("EMPTY")

    def set_state(self, state: str) -> None:
        text, color, background = _PREVIEW_STATES[state]
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"color: {color}; background: {background}; padding: 3px 8px; "
            "border-radius: 4px; font-weight: 600;"
        )
        target = self.empty_page if state == "EMPTY" else self.edit
        self.stack.setCurrentWidget(target)

    def show_find(self) -> None:
        self.find_bar.show()
        selected = self.edit.textCursor().selectedText().strip()
        if selected and "\n" not in selected:
            self.find_input.setText(selected)
        self.find_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.find_input.selectAll()

    def hide_find(self) -> None:
        self.find_bar.hide()
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def find_text(self, _text=None, *, backward=False) -> bool:
        query = self.find_input.text()
        if not query:
            return False
        flags = (
            QTextDocument.FindFlag.FindBackward
            if backward
            else QTextDocument.FindFlag(0)
        )
        if self.edit.find(query, flags):
            return True
        cursor = self.edit.textCursor()
        cursor.movePosition(
            cursor.MoveOperation.End if backward else cursor.MoveOperation.Start
        )
        self.edit.setTextCursor(cursor)
        return self.edit.find(query, flags)

    def clear(self) -> None:
        self.edit.clear()
        self.set_state("EMPTY")

    def set_plain_text(self, text: str) -> None:
        self.edit.setPlainText(text)
