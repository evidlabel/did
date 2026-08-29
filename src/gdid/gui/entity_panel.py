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

from .. import pipeline

ROLE_ENTITY_ID = Qt.ItemDataRole.UserRole + 10
ROLE_VARIANT = Qt.ItemDataRole.UserRole + 11
ROLE_ENTITY_TYPE = Qt.ItemDataRole.UserRole + 12
ROLE_VARIANTS = Qt.ItemDataRole.UserRole + 13

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

    def refresh_from_yaml(self, text: str) -> bool:
        """Populate the table from YAML config. Returns False on parse error."""
        self.table.setRowCount(0)
        self.details.clear()
        if not text.strip():
            self.validation_label.setText(
                "Entities appear here automatically after documents are added."
            )
            return True
        try:
            data = pipeline.parse_yaml(text)
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #b3261e;")
            return False
        count = 0
        for entity_type, entities in data.items():
            if not isinstance(entities, list):
                continue
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                variants = entity.get("variants", [])
                entity_id = str(entity.get("id", ""))
                type_item = QTableWidgetItem(str(entity_type))
                compact_id = entity_id.removeprefix(f"{entity_type}_")
                id_item = QTableWidgetItem(compact_id)
                id_item.setToolTip(entity_id)
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
                variants_item.setData(Qt.ItemDataRole.UserRole, variants)
                variants_item.setToolTip("\n".join(variants))
                self.table.setItem(row, 0, type_item)
                self.table.setItem(row, 1, id_item)
                self.table.setItem(row, 2, variants_item)
                self.table.setItem(row, 3, confidence_item)
                if entity_type == "PERSON" and len(variants) > 1:
                    for variant in variants:
                        child_row = self.table.rowCount()
                        self.table.insertRow(child_row)
                        child_type = QTableWidgetItem("")
                        child_id = QTableWidgetItem("↳")
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
        if count:
            self.table.setCurrentCell(0, 0)
        return True

    def select_entity(self, entity_type: str, possible_ids: set[str]) -> bool:
        """Select the first parent row matching type and any of *possible_ids*."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            type_item = self.table.item(row, 0)
            if item is None or type_item is None or not type_item.text():
                continue
            if (
                item.data(ROLE_ENTITY_TYPE) == entity_type
                and item.data(ROLE_ENTITY_ID) in possible_ids
            ):
                self.table.clearSelection()
                self.table.setCurrentCell(row, 2)
                self.table.selectRow(row)
                self.table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)
                return True
        return False

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.details.clear()
        self.validation_label.setStyleSheet("")
        self.validation_label.setText(
            "Entities appear here automatically after documents are added."
        )
