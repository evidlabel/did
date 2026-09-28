"""Entity review panel: table, filters, details, and population from YAML.

MainWindow remains the orchestrator for project lifecycle and review mutations
(add variant, change type, merge, mark-not-name) that touch pipeline/project
state. This module owns the entity table widget tree, row roles, filtering,
and refresh-from-YAML display logic.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from did.core import entity_types

from .. import pipeline

ROLE_ENTITY_ID = Qt.ItemDataRole.UserRole + 10
ROLE_VARIANT = Qt.ItemDataRole.UserRole + 11
ROLE_ENTITY_TYPE = Qt.ItemDataRole.UserRole + 12
ROLE_VARIANTS = Qt.ItemDataRole.UserRole + 13
# 1-based position of the identity within its type: the number its tokens carry.
ROLE_TOKEN_INDEX = Qt.ItemDataRole.UserRole + 14
# 1-based variant position on child rows: the V<n> of the token.
ROLE_VARIANT_INDEX = Qt.ItemDataRole.UserRole + 15

_TYPE_PREFIX = {
    entity.config_key: entity.prefix for entity in entity_types.ENTITY_TYPES
}

# Types offered in the type filter / review combo (not document titles).
REVIEW_ENTITY_TYPES = (
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "DATE_NUMBER",
    "ID_NUMBER",
    "CODE_NUMBER",
    "GENERAL_NUMBER",
    "URL",
)


class EntityTable(QTableWidget):
    """Entity table that reports row-to-row drops without moving cells itself."""

    mergeRequested = Signal(int, int)
    contextMenuRequested = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        self._drag_row = -1
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTableWidget.DragDropMode.DragDrop)

    def startDrag(self, supported_actions):
        self._drag_row = self.currentRow()
        super().startDrag(supported_actions)

    def dropEvent(self, event):
        target_row = self.indexAt(event.position().toPoint()).row()
        if self._drag_row >= 0 and target_row >= 0:
            self.mergeRequested.emit(self._drag_row, target_row)
            event.acceptProposedAction()
            return
        event.ignore()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            pos = event.position().toPoint()
            self.contextMenuRequested.emit(pos, self.viewport().mapToGlobal(pos))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class EntityPanel(QWidget):
    """Entity review tab: type filter, search, table, details, validation line."""

    changeTypeRequested = Signal(str)
    addVariantRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        filters = QHBoxLayout()
        self.type_filter = QComboBox()
        self.type_filter.addItem("ALL", "ALL")
        for entity_type in REVIEW_ENTITY_TYPES:
            self.type_filter.addItem(entity_type.replace("_", " "), entity_type)
        self.type_filter.setToolTip("Filter identities by entity type.")
        self.type_filter.currentIndexChanged.connect(
            lambda: self.filter_entities(self.search.text())
        )
        filters.addWidget(self.type_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search detected entities…")
        self.search.textChanged.connect(self.filter_entities)
        filters.addWidget(self.search, 1)

        self.table = EntityTable()
        self.table.setHorizontalHeaderLabels(["Type", "#", "Variants", "Confidence"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        entity_header = self.table.horizontalHeader()
        entity_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        entity_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        entity_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        entity_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        entity_header.resizeSection(0, 104)
        entity_header.resizeSection(1, 52)
        entity_header.resizeSection(3, 86)
        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        review_actions = QHBoxLayout()
        self.review_type_combo = QComboBox()
        for entity_type in REVIEW_ENTITY_TYPES:
            self.review_type_combo.addItem(entity_type.replace("_", " "), entity_type)
        self.review_type_combo.setToolTip("New type for all selected identities.")
        review_actions.addWidget(self.review_type_combo, 1)
        self.apply_type_button = QPushButton("Change type")
        self.apply_type_button.clicked.connect(
            lambda: self.changeTypeRequested.emit(self.review_type_combo.currentData())
        )
        review_actions.addWidget(self.apply_type_button)
        self.add_variant_button = QPushButton("Add variant…")
        self.add_variant_button.clicked.connect(
            lambda: self.addVariantRequested.emit(self.table.currentRow())
        )
        review_actions.addWidget(self.add_variant_button)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        self.details.setPlaceholderText(
            "Select an identity to see all detected variants."
        )
        self.validation_label = QLabel(
            "Entities appear here automatically after documents are added."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(filters)
        layout.addLayout(review_actions)
        layout.addWidget(self.table)
        layout.addWidget(self.details)
        layout.addWidget(self.validation_label)

        # editable flag controlled by MainWindow (version view is read-only)
        self._editable = True
        self.update_review_actions()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.update_review_actions()

    def selected_rows(self) -> list[int]:
        return sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()}
        )

    def update_review_actions(self) -> None:
        rows = self.selected_rows()
        self.apply_type_button.setEnabled(self._editable and bool(rows))
        self.add_variant_button.setEnabled(self._editable and len(rows) == 1)
        if len(rows) > 1:
            self.validation_label.setText(
                f"{len(rows)} rows selected · changes apply to all identities"
            )

    def _on_selection_changed(self) -> None:
        self.update_review_actions()

    def _on_current_cell_changed(self, row, _column, _previous_row, _previous_column):
        self.show_entity_details(row)

    def show_entity_details(self, row: int) -> None:
        if row < 0:
            self.details.clear()
            return
        type_item = self.table.item(row, 0)
        id_item = self.table.item(row, 1)
        variants_item = self.table.item(row, 2)
        if type_item is None or id_item is None or variants_item is None:
            self.details.clear()
            return
        variants = variants_item.data(ROLE_VARIANTS) or []
        confidence_item = self.table.item(row, 3)
        entity_type = variants_item.data(ROLE_ENTITY_TYPE) or type_item.text()
        entity_id = variants_item.data(ROLE_ENTITY_ID) or id_item.text()
        lines = [f"{entity_type} · {entity_id}", ""]
        if confidence_item is not None and confidence_item.text() not in {"", "—"}:
            lines.extend([f"Detection confidence: {confidence_item.text()}", ""])
        lines.extend(f"• {variant}" for variant in variants)
        self.details.setPlainText("\n".join(lines))

    def filter_entities(self, query=None) -> None:
        if query is None:
            query = self.search.text()
        query = query.casefold().strip()
        selected_type = self.type_filter.currentData()
        for row in range(self.table.rowCount()):
            type_item = self.table.item(row, 2)
            entity_type = (
                type_item.data(ROLE_ENTITY_TYPE) if type_item is not None else None
            )
            values = [
                self.table.item(row, column).text()
                for column in range(self.table.columnCount())
                if self.table.item(row, column) is not None
            ]
            self.table.setRowHidden(
                row,
                bool(
                    (selected_type != "ALL" and entity_type != selected_type)
                    or (query and query not in " ".join(values).casefold())
                ),
            )

    def _row_key(self, row):
        item = self.table.item(row, 2) if row >= 0 else None
        if item is None:
            return None
        return (
            item.data(ROLE_ENTITY_TYPE),
            item.data(ROLE_TOKEN_INDEX),
            item.data(ROLE_VARIANT_INDEX),
        )

    def _find_row(self, entity_type, number, variant=None):
        """Row for identity *number* of *entity_type*, or its *variant* child row."""
        parent_row = None
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            if (
                item is None
                or item.data(ROLE_ENTITY_TYPE) != entity_type
                or item.data(ROLE_TOKEN_INDEX) != number
            ):
                continue
            row_variant = item.data(ROLE_VARIANT_INDEX)
            if row_variant is None:
                parent_row = row
                if variant is None:
                    return row
            elif row_variant == variant:
                return row
        return parent_row

    def refresh_from_yaml(self, text: str) -> bool:
        """Populate the table from YAML config. Returns False on parse error.

        The selected identity and the scroll position survive the rebuild, so a
        review edit does not throw the reviewer back to the top of the list.
        """
        previous = self._row_key(self.table.currentRow())
        scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(0)
        self.details.clear()
        if not text.strip():
            self.validation_label.setText(
                "Entities appear here automatically after documents are added."
            )
            return True
        try:
            data = pipeline.read_keys(text)
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #b3261e;")
            return False
        count = 0
        for entity_type, entities in data.items():
            if not isinstance(entities, list):
                continue
            position = 0
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                position += 1
                row = self.table.rowCount()
                self.table.insertRow(row)
                variants = entity.get("variants", [])
                entity_id = str(entity.get("id", ""))
                type_item = QTableWidgetItem(str(entity_type))
                prefix = _TYPE_PREFIX.get(str(entity_type), "")
                id_item = QTableWidgetItem(f"{prefix}{position}")
                id_item.setToolTip(
                    f"Token #({prefix}{position}V…) · YAML id {entity_id or '—'}"
                )
                variants = list(map(str, variants))
                variants_item = QTableWidgetItem(", ".join(variants))
                confidence = entity.get("confidence")
                confidence_item = QTableWidgetItem(
                    f"{float(confidence):.0%}" if confidence is not None else "—"
                )
                if confidence is not None:
                    confidence_item.setToolTip(
                        "Highest Presidio/spaCy confidence reported for this identity’s variants."
                    )
                else:
                    confidence_item.setToolTip(
                        "No confidence was reported (for example, a fallback or manually added identity)."
                    )
                for item in (type_item, id_item, variants_item, confidence_item):
                    item.setData(ROLE_ENTITY_ID, entity_id)
                    item.setData(ROLE_ENTITY_TYPE, str(entity_type))
                    item.setData(ROLE_VARIANTS, variants)
                    item.setData(ROLE_TOKEN_INDEX, position)
                variants_item.setData(Qt.ItemDataRole.UserRole, variants)
                variants_item.setToolTip("\n".join(variants))
                self.table.setItem(row, 0, type_item)
                self.table.setItem(row, 1, id_item)
                self.table.setItem(row, 2, variants_item)
                self.table.setItem(row, 3, confidence_item)
                if entity_type == "PERSON" and len(variants) > 1:
                    for variant_index, variant in enumerate(variants, 1):
                        child_row = self.table.rowCount()
                        self.table.insertRow(child_row)
                        child_type = QTableWidgetItem("")
                        child_id = QTableWidgetItem(f"↳ V{variant_index}")
                        child_variant = QTableWidgetItem(variant)
                        child_confidence = QTableWidgetItem("")
                        for item in (
                            child_type,
                            child_id,
                            child_variant,
                            child_confidence,
                        ):
                            item.setData(ROLE_ENTITY_ID, entity_id)
                            item.setData(ROLE_ENTITY_TYPE, str(entity_type))
                            item.setData(ROLE_VARIANT, variant)
                            item.setData(ROLE_VARIANTS, [variant])
                            item.setData(ROLE_TOKEN_INDEX, position)
                            item.setData(ROLE_VARIANT_INDEX, variant_index)
                        child_variant.setData(Qt.ItemDataRole.UserRole, [variant])
                        child_variant.setToolTip(variant)
                        self.table.setItem(child_row, 0, child_type)
                        self.table.setItem(child_row, 1, child_id)
                        self.table.setItem(child_row, 2, child_variant)
                        self.table.setItem(child_row, 3, child_confidence)
                count += 1
        self.validation_label.setStyleSheet("")
        self.validation_label.setText(f"{count} identities · configuration valid")
        self.filter_entities(self.search.text())
        restored = self._find_row(*previous) if previous else None
        if restored is not None:
            self.table.setCurrentCell(restored, 0)
            self.table.verticalScrollBar().setValue(scroll)
        elif count:
            self.table.setCurrentCell(0, 0)
        return True

    def select_entity(self, entity_type: str, number: int, variant=None) -> bool:
        """Select identity *number* of *entity_type* — the one a token names.

        A ``V<n>`` token selects that variant's child row when the identity
        lists its variants separately. A row hidden by the type filter or the
        search is revealed first, so a clicked token always lands visibly.
        """
        row = self._find_row(entity_type, number, variant)
        if row is None:
            return False
        if self.table.isRowHidden(row):
            self.type_filter.setCurrentIndex(0)
            self.search.clear()
            self.filter_entities("")
        item = self.table.item(row, 2)
        self.table.clearSelection()
        self.table.setCurrentCell(row, 2)
        self.table.selectRow(row)
        self.table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)
        return True

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.details.clear()
        self.validation_label.setStyleSheet("")
        self.validation_label.setText(
            "Entities appear here automatically after documents are added."
        )


class KeysSourceBar(QWidget):
    """One line naming the keys file behind what is shown, with a folder link."""

    openFolderRequested = Signal(object)  # Path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.label = QLabel()
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        self.open_button = QPushButton("Open folder")
        self.open_button.setFlat(True)
        self.open_button.setToolTip("Show the keys file’s folder in the file manager.")
        self.open_button.clicked.connect(
            lambda: (
                self._folder is not None and self.openFolderRequested.emit(self._folder)
            )
        )
        layout.addWidget(self.open_button)
        self.set_source("Keys: none yet — add documents to detect entities.")

    @property
    def text(self) -> str:
        return self.label.text()

    @property
    def folder(self):
        return self._folder

    def set_source(self, text: str, *, tooltip: str = "", folder=None, warn=False):
        self.label.setText(text)
        self.label.setStyleSheet("color: #b3261e;" if warn else "")
        self.setToolTip(tooltip)
        self.label.setToolTip(tooltip)
        self._folder = folder
        self.open_button.setEnabled(folder is not None and folder.exists())
