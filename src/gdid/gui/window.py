"""Streamlined single-flow main window for gdid.

Open (or drag-drop) documents → text is extracted in the background → one
**Anonymize** action detects entities and pseudonymizes off-thread → the YAML
config is editable and re-applies live → **Save** writes Typst output. All real
work is delegated to :mod:`gdid.pipeline` via the workers in :mod:`gdid.gui.workers`.
"""

import dataclasses
import json
import shutil
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QFont,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from did import __version__
from did.core import entity_types
from did.core.anonymizer import Anonymizer
from did.utils.file_utils import Reading

from .. import detect_process, pipeline
from ..project import (
    CANONICAL_PROJECT_FILENAME,
    ENTITIES_FILENAME,
    PROJECT_SUFFIX,
    Project,
    ProjectError,
    abort_version,
    begin_version,
    convert_legacy_project,
    create_project_workdir,
    delete_project_workdir,
    draft_entities_path,
    finalize_version,
    list_versions,
    load_not_names,
    save_draft_entities,
    save_not_names,
)
from ..project import load_project as read_project
from ..project import save_project as write_project
from .document_input import (
    NO_DOCUMENTS_STATUS,
    NO_NEW_DOCUMENTS_STATUS,
    choose_document_paths,
    collect_document_additions,
    prepare_tree_project_for_documents,
    resolve_document_paths,
    select_document_in_tree,
)
from .entity_panel import (
    REVIEW_ENTITY_TYPES,
    ROLE_ENTITY_ID,
    ROLE_ENTITY_TYPE,
    ROLE_TOKEN_INDEX,
    ROLE_VARIANT,
    ROLE_VARIANTS,
    EntityPanel,
    KeysSourceBar,
)
from .preview import PreviewPanel, token_at_position
from .project_tree import (
    ROLE_ACTIVE_PROJECT,
    ROLE_DOCUMENT_PATH,
    ROLE_FILE_PATH,
    ROLE_PROJECT_KIND,
    ROLE_PROJECT_PATH,
    ROLE_VERSION_PATH,
    ProjectTreePanel,
    keys_origin_label,
    version_document_files,
)
from .reveal import open_in_file_manager
from .settings import AppSettings
from .workers import AnonymizeWorker, ExtractWorker, PseudoWorker, TaskWorker

# Re-export under prior private names so existing window code and tests keep
# working without a noisy rename across the project-tree helpers.
_ROLE_ENTITY_ID = ROLE_ENTITY_ID
_ROLE_VARIANT = ROLE_VARIANT
_ROLE_ENTITY_TYPE = ROLE_ENTITY_TYPE
_ROLE_VARIANTS = ROLE_VARIANTS
_ROLE_TOKEN_INDEX = ROLE_TOKEN_INDEX
_ROLE_PROJECT_KIND = ROLE_PROJECT_KIND
_ROLE_PROJECT_PATH = ROLE_PROJECT_PATH
_ROLE_VERSION_PATH = ROLE_VERSION_PATH
_ROLE_DOCUMENT_PATH = ROLE_DOCUMENT_PATH
_ROLE_ACTIVE_PROJECT = ROLE_ACTIVE_PROJECT

_MONO = QFont("monospace")
_MONO.setStyleHint(QFont.StyleHint.Monospace)

_PROJECT_URL = "https://github.com/evidlabel/did"
_DOCS_URL = f"{_PROJECT_URL}/tree/main/docs"
_LICENSE_URL = f"{_PROJECT_URL}/blob/main/LICENSE"
# Types a reviewer may reclassify an entity into. Document titles are
# assigned per document, never chosen, so they are not offered here.
_ENTITY_TYPES = tuple(entity.config_key for entity in entity_types.DETECTED_TYPES)


def _identities(count):
    return f"{count} identit{'y' if count == 1 else 'ies'}"


class MainWindow(QMainWindow):
    """Main gdid window."""

    def __init__(self, *, anonymizer_factory=Anonymizer, settings=None):
        super().__init__()
        self._factory = anonymizer_factory
        self._settings = settings or AppSettings()
        self._project: Project | None = None
        self._dirty = False
        self._loading_project = False
        # Review text to carry into the next detection pass (see _on_anonymized).
        self._pending_review_yaml = None
        # How the current draft keys came about: saved_review/merged/fresh/edited.
        self._keys_origin = None
        self._keys_saved_text = None
        self._redetect_on_return = False
        self._detection_status = None
        self._force_review_merge = False
        self._after_reapply = None
        self._export_worker = None
        self._viewing_version = None
        self._draft_view_state = None
        self._version_yaml_overrides = {}
        self._version_snapshot_yaml = ""
        self._selected_file: Path | None = None
        self._tree_pressed_state = None
        self._pending_tree_navigation = None
        self._source_digest_cache = {}
        self._session_generation = 0
        self._entity_context_menu = None
        self._entity_context_menu_popup_count = 0
        self._last_entity_context_labels = []
        self._suppress_context_menu_popup = False

        self._files: list[Path] = []
        self._readings: dict[Path, Reading] = {}
        self._anonymized: dict[Path, str] = {}
        self._anonymizer = None
        self._yaml_text = ""
        self._temp_dirs: list[Path] = []
        self._workers: list = []
        self._language = "da"
        self._detection_profile = "thorough"
        self._output_mode = "typst"
        self._show_original = False
        # Sources being read off the UI thread for a version's Original view.
        self._original_loads: set[Path] = set()
        self._auto_version_pending = False
        self._verification = None
        self._verification_acknowledged = False

        self.setAcceptDrops(True)
        self._build_ui()
        self._refresh_project_tree()
        self._update_window_title()
        self._refresh_actions()

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self):
        file_menu = self.menuBar().addMenu("&File")
        self.new_project_action = QAction("&New Project", self)
        self.new_project_action.setShortcut("Ctrl+N")
        self.new_project_action.triggered.connect(self._on_new_project)
        file_menu.addAction(self.new_project_action)
        self.open_project_action = QAction("&Open Project…", self)
        self.open_project_action.setShortcut("Ctrl+Shift+O")
        self.open_project_action.triggered.connect(self._on_open_project)
        file_menu.addAction(self.open_project_action)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        file_menu.addSeparator()
        self.save_project_action = QAction("&Save Project", self)
        self.save_project_action.setShortcut("Ctrl+S")
        self.save_project_action.triggered.connect(self._on_save_project)
        file_menu.addAction(self.save_project_action)
        self.save_project_as_action = QAction("Save Project &As…", self)
        self.save_project_as_action.setShortcut("Ctrl+Shift+S")
        self.save_project_as_action.triggered.connect(self._on_save_project_as)
        file_menu.addAction(self.save_project_as_action)
        self.rename_project_action = QAction("&Rename Project…", self)
        self.rename_project_action.setShortcut("F2")
        self.rename_project_action.triggered.connect(self._on_rename_project)
        file_menu.addAction(self.rename_project_action)
        self.meta_action = QAction("Project &Meta…", self)
        self.meta_action.triggered.connect(self._on_meta)
        file_menu.addAction(self.meta_action)
        file_menu.addSeparator()
        self.add_documents_action = QAction("&Add Documents…", self)
        self.add_documents_action.setShortcut("Ctrl+O")
        self.add_documents_action.triggered.connect(self._on_open)
        file_menu.addAction(self.add_documents_action)
        self.remove_document_action = QAction("&Remove Selected Document", self)
        self.remove_document_action.setShortcut("Delete")
        self.remove_document_action.triggered.connect(self._remove_selected_document)
        file_menu.addAction(self.remove_document_action)

        self.help_menu = self.menuBar().addMenu("&Help")
        self.documentation_action = QAction("&Documentation", self)
        self.documentation_action.setShortcut("F1")
        self.documentation_action.triggered.connect(
            lambda: self._open_web_url(_DOCS_URL)
        )
        self.help_menu.addAction(self.documentation_action)
        self.github_action = QAction("DID on &GitHub", self)
        self.github_action.triggered.connect(lambda: self._open_web_url(_PROJECT_URL))
        self.help_menu.addAction(self.github_action)
        self.license_action = QAction("MIT &License", self)
        self.license_action.triggered.connect(lambda: self._open_web_url(_LICENSE_URL))
        self.help_menu.addAction(self.license_action)
        self.help_menu.addSeparator()
        self.about_action = QAction("&About DID", self)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)

        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.open_action = QAction("Add Documents", self)
        self.open_action.triggered.connect(self._on_open)
        tb.addAction(self.open_action)

        self.anon_action = QAction("Re-run detection", self)
        self.anon_action.setToolTip(
            "Documents are processed automatically when added. Re-run detection "
            "after changing language or detection settings."
        )
        self.anon_action.triggered.connect(self._on_redetect)
        tb.addAction(self.anon_action)

        self.save_button = QToolButton(self)
        self.save_button.setText("Save output")
        self.save_button.setToolTip(
            "Write the pseudonymized output for this project. It replaces the "
            "previous output."
        )
        self.save_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        save_menu = QMenu(self.save_button)
        self.save_multi_action = save_menu.addAction("Multi (separate files)")
        self.save_multi_action.triggered.connect(lambda: self._on_save("multi"))
        self.save_single_action = save_menu.addAction("Single (combined)")
        self.save_single_action.triggered.connect(lambda: self._on_save("single"))
        self.save_pdf_action = save_menu.addAction("PDFs (via Typst)")
        self.save_pdf_action.setToolTip(
            "One <name>_pseudo.pdf per document, rendered from Typst."
        )
        self.save_pdf_action.triggered.connect(lambda: self._on_save("pdf"))
        self.save_button.setMenu(save_menu)
        tb.addWidget(self.save_button)

        self.llm_button = QToolButton(self)
        self.llm_button.setText("LLM handoff")
        self.llm_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.llm_button.setToolTip(
            "Copy or package reviewed pseudonymized documents for an LLM session. "
            "Identity mappings are never included."
        )
        llm_menu = QMenu(self.llm_button)
        self.copy_llm_current_markdown_action = llm_menu.addAction(
            "Copy current document as Markdown"
        )
        self.copy_llm_current_markdown_action.triggered.connect(
            self._copy_current_llm_markdown
        )
        self.copy_llm_current_json_action = llm_menu.addAction(
            "Copy current document as JSON"
        )
        self.copy_llm_current_json_action.triggered.connect(self._copy_current_llm_json)
        llm_menu.addSeparator()
        copy_markdown = llm_menu.addAction("Copy all as combined Markdown")
        copy_markdown.triggered.connect(self._copy_llm_markdown)
        copy_json = llm_menu.addAction("Copy all as structured JSON")
        copy_json.triggered.connect(self._copy_llm_json)
        llm_menu.addSeparator()
        export_zip = llm_menu.addAction("Save safe session ZIP…")
        export_zip.triggered.connect(self._save_llm_zip)
        self.llm_button.setMenu(llm_menu)
        tb.addWidget(self.llm_button)

        tb.addSeparator()
        tb.addWidget(QLabel(" Language: "))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Danish", "da")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Swedish", "sv")
        self.lang_combo.setToolTip(
            "Language used to detect names, addresses, dates, and other entities."
        )
        self.lang_combo.currentIndexChanged.connect(self._on_language)
        tb.addWidget(self.lang_combo)

        tb.addWidget(QLabel(" Detection: "))
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("Thorough", "thorough")
        self.profile_combo.addItem("Balanced", "balanced")
        self.profile_combo.setToolTip(
            "Thorough prioritizes detection coverage; Balanced uses fewer resources."
        )
        self.profile_combo.currentIndexChanged.connect(self._on_detection_profile)
        tb.addWidget(self.profile_combo)

        tb.addWidget(QLabel(" Preview format: "))
        self.output_combo = QComboBox()
        self.output_combo.addItem("Typst #(P1V1)", "typst")
        self.output_combo.addItem("Plain [PERSON 1]", "plain")
        self.output_combo.addItem("Redacted [REDACTED]", "redacted")
        self.output_combo.addItem("Synthetic (Faker)", "synthetic")
        self.output_combo.setToolTip(
            "Changes how reviewed identities appear in previews and LLM handoffs."
        )
        self.output_combo.currentIndexChanged.connect(self._on_output_mode)
        tb.addWidget(self.output_combo)
        self.original_button = QPushButton("Original")
        self.original_button.setCheckable(True)
        self.original_button.setToolTip(
            "Show the unanonymized source text of the current document. "
            "It contains personal data. LLM handoff still copies the pseudonymized text."
        )
        self.original_button.toggled.connect(self._on_show_original)
        tb.addWidget(self.original_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.project_tree_panel = ProjectTreePanel()
        self.project_tree = self.project_tree_panel.tree
        self.sidebar_new_button = self.project_tree_panel.new_button
        self.sidebar_open_button = self.project_tree_panel.open_button
        self.project_tree_panel.newProjectClicked.connect(self._on_new_project)
        self.project_tree_panel.openProjectClicked.connect(self._on_open_project)
        self.project_tree_panel.contextMenuRequested.connect(self._on_project_tree_menu)
        self.project_tree_panel.itemPressed.connect(self._on_project_tree_pressed)
        self.project_tree_panel.itemClicked.connect(self._on_project_tree_item_clicked)
        splitter.addWidget(self.project_tree_panel)

        self.preview_panel = PreviewPanel(
            menu_extender=self._extend_preview_menu,
            on_new_project=self._on_new_project,
            on_open_project=self._on_open_project,
            on_add_documents=self._on_open,
        )
        # Stable attribute surface used by tests and the rest of MainWindow.
        self.preview = self.preview_panel.edit
        self.preview_state = self.preview_panel.state_label
        self.preview_find = self.preview_panel.find_input
        self.preview_find_bar = self.preview_panel.find_bar
        self.preview_stack = self.preview_panel.stack
        self.empty_preview = self.preview_panel.empty_page
        self._highlighter = self.preview_panel.highlighter
        self.preview.cursorPositionChanged.connect(self._focus_preview_entity)
        splitter.addWidget(self.preview_panel)

        self.entity_panel = EntityPanel()
        self.entity_type_filter = self.entity_panel.type_filter
        self.entity_search = self.entity_panel.search
        self.entity_table = self.entity_panel.table
        self.review_type_combo = self.entity_panel.review_type_combo
        self.apply_type_button = self.entity_panel.apply_type_button
        self.add_variant_button = self.entity_panel.add_variant_button
        self.entity_details = self.entity_panel.details
        self.validation_label = self.entity_panel.validation_label
        self.entity_table.contextMenuRequested.connect(self._on_entity_table_menu)
        self.entity_table.mergeRequested.connect(self._merge_entity_rows)
        self.entity_panel.changeTypeRequested.connect(self._change_selected_entity_type)
        self.entity_panel.addVariantRequested.connect(self._add_entity_variant)

        self.yaml_edit = QPlainTextEdit()
        self.yaml_edit.setFont(_MONO)
        self.yaml_edit.setPlaceholderText("Detected entity config (YAML) — editable")
        self.yaml_edit.textChanged.connect(self._on_yaml_changed)
        self.entity_tabs = tabs = QTabWidget()
        tabs.addTab(self.entity_panel, "Entity review")
        tabs.addTab(self.yaml_edit, "Advanced YAML")
        self.keys_bar = KeysSourceBar()
        self.keys_bar.openFolderRequested.connect(self._reveal_path)
        keys_box = QWidget()
        keys_layout = QVBoxLayout(keys_box)
        keys_layout.setContentsMargins(0, 0, 0, 0)
        keys_layout.addWidget(self.keys_bar)
        keys_layout.addWidget(tabs)
        entities_box = self._titled("Detected entities", keys_box)
        entities_box.setMinimumWidth(460)
        splitter.addWidget(entities_box)

        splitter.setSizes([400, 620, 500])
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self._set_preview_state("EMPTY")
        self.setCentralWidget(splitter)

        self.status_label = QLabel("Open documents to begin.")
        self.statusBar().addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

        # Debounce live re-apply of edited YAML config.
        self._reapply_timer = QTimer(self)
        self._reapply_timer.setSingleShot(True)
        self._reapply_timer.setInterval(500)
        self._reapply_timer.timeout.connect(self._reapply_config)
        # Debounce autosave of the draft keys file.
        self._keys_save_timer = QTimer(self)
        self._keys_save_timer.setSingleShot(True)
        self._keys_save_timer.setInterval(500)
        self._keys_save_timer.timeout.connect(self._autosave_keys)

    @staticmethod
    def _titled(title, widget):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(4, 4, 4, 4)
        label = QLabel(f"<b>{title}</b>")
        layout.addWidget(label)
        layout.addWidget(widget)
        return box

    # -------------------------------------------------------------- helpers ---
    def _refresh_actions(self):
        read_only = self._viewing_version is not None
        self.anon_action.setEnabled(bool(self._readings))
        self.save_button.setEnabled(bool(self._anonymized))
        self.llm_button.setEnabled(bool(self._anonymized))
        current_ready = self._current_file() in self._anonymized
        self.copy_llm_current_markdown_action.setEnabled(current_ready)
        self.copy_llm_current_json_action.setEnabled(current_ready)
        self.save_project_action.setEnabled(self._project is not None)
        self.save_project_as_action.setEnabled(self._project is not None)
        self.rename_project_action.setEnabled(self._project is not None)
        self.meta_action.setEnabled(self._project is not None)
        self.remove_document_action.setEnabled(self._current_file() is not None)
        current = self._current_file()
        self.original_button.setEnabled(self._can_show_original(current))
        if self._export_worker is not None:
            self.save_button.setEnabled(False)
        if read_only:
            self.anon_action.setEnabled(False)
            self.save_button.setEnabled(False)
            self.add_documents_action.setEnabled(False)
            self.open_action.setEnabled(False)
            self.remove_document_action.setEnabled(False)
        else:
            self.add_documents_action.setEnabled(True)
            self.open_action.setEnabled(True)
        if hasattr(self, "entity_panel"):
            self._update_review_actions()

    def _update_window_title(self):
        name = self._project.name if self._project is not None else "No project"
        marker = "*" if self._dirty else ""
        self.setWindowTitle(f"{marker}{name} — DID Pseudonymizer")
        if not hasattr(self, "project_tree_panel"):
            return
        if (
            self._project is not None
            and not self.project_tree_panel.update_active_badge(
                self._project, name, self._source_digest_cache
            )
        ):
            self._refresh_project_tree()

    def _refresh_project_tree(self):
        """Rebuild the logical project tree without reading source contents."""
        if not hasattr(self, "project_tree_panel"):
            return
        self.project_tree_panel.refresh(
            active_project=self._project,
            recent_project_paths=self._settings.recent_projects(),
            selected_file=self._selected_file,
            viewing_version=self._viewing_version,
            draft_view_state=self._draft_view_state,
            anonymized=self._anonymized,
            extracted=self._readings,
            source_digest_cache=self._source_digest_cache,
            remove_missing_recent=self._settings.remove_recent_project,
        )

    # Short aliases keep the project/document refresh call sites explicit.
    def _refresh_project_list(self):
        self._refresh_project_tree()

    def _refresh_file_list(self):
        self._refresh_project_tree()

    def _on_project_tree_pressed(self, item, _column):
        self._tree_pressed_state = (item, item.isExpanded())

    def _on_project_tree_item_clicked(self, item, _column):
        if self._tree_pressed_state and self._tree_pressed_state[0] is item:
            was_expanded = self._tree_pressed_state[1]
            self._tree_pressed_state = None
            if item.childCount() and item.isExpanded() != was_expanded:
                return
        kind = item.data(0, ROLE_PROJECT_KIND)
        project_path = item.data(0, ROLE_PROJECT_PATH)
        version_path = item.data(0, ROLE_VERSION_PATH)
        document_path = item.data(0, ROLE_DOCUMENT_PATH)
        active_path = (
            str(self._project.project_file.resolve())
            if self._project is not None and self._project.project_file is not None
            else ""
        )
        if project_path != active_path and project_path:
            if kind in {"version", "version_document", "version_keys"}:
                self._pending_tree_navigation = (
                    Path(version_path),
                    Path(document_path) if document_path else None,
                )
            if not self._open_project_path(project_path):
                self._pending_tree_navigation = None
                self._refresh_project_tree()
                return
            if self._pending_tree_navigation is not None:
                if self._files:
                    self._set_status(
                        "Opening project before showing its saved version…"
                    )
                    return
                self._finish_pending_tree_navigation()
                return
        if kind == "project":
            if self._viewing_version is not None:
                self._return_to_draft()
            return
        if kind in {"meta", "meta_entry"}:
            self._on_meta()
            return
        if kind == "draft_keys":
            if self._viewing_version is not None:
                self._return_to_draft()
            self.entity_tabs.setCurrentWidget(self.yaml_edit)
            return
        if kind == "version_keys" and version_path:
            path = Path(version_path)
            if self._viewing_version != path:
                self._view_version(path)
            self.entity_tabs.setCurrentWidget(self.yaml_edit)
            return
        if kind in {"draft", "draft_document"}:
            if self._viewing_version is not None:
                self._return_to_draft()
            if document_path:
                self._select_document(Path(document_path))
                self._defer_clicked_document_focus(Path(document_path), None)
            elif self._files:
                self._select_document(self._selected_file or self._files[0])
            return
        if kind in {"version", "version_document"} and version_path:
            path = Path(version_path)
            if self._viewing_version != path:
                self._view_version(path)
            if document_path:
                self._select_document(Path(document_path))
                self._defer_clicked_document_focus(Path(document_path), path)

    def _defer_clicked_document_focus(self, document_path, version_path):
        """Restore a clicked document after Qt finishes dispatching the tree click."""
        QTimer.singleShot(
            0,
            lambda: self._restore_clicked_document_focus(
                Path(document_path), Path(version_path) if version_path else None
            ),
        )

    def _restore_clicked_document_focus(self, document_path, version_path):
        if version_path != self._viewing_version:
            return
        if self._select_document(document_path):
            self.project_tree.setFocus(Qt.FocusReason.MouseFocusReason)

    def _finish_pending_tree_navigation(self):
        pending = self._pending_tree_navigation
        self._pending_tree_navigation = None
        if pending is None:
            return
        version_path, document_path = pending
        self._view_version(version_path)
        if document_path is not None:
            self._select_document(document_path)
            self._defer_clicked_document_focus(document_path, version_path)

    def _on_project_tree_menu(self, pos):
        item = self.project_tree.itemAt(pos)
        menu = self._project_tree_menu(item)
        if menu is None:
            return
        menu.exec(self.project_tree.viewport().mapToGlobal(pos))

    def _project_tree_menu(self, item):
        if item is None:
            return None
        menu = self._project_tree_menu_actions(item)
        if menu is None:
            return None
        self._add_open_folder_action(menu, self._tree_item_folder(item))
        return menu

    def _project_tree_menu_actions(self, item):
        menu = QMenu(self)
        kind = item.data(0, ROLE_PROJECT_KIND)
        document_path = item.data(0, ROLE_DOCUMENT_PATH)
        if kind in {"draft_document", "version_document"}:
            f = Path(document_path) if document_path else None
            menu = self._copy_menu(f)
            if f is not None:
                view_original = menu.addAction("View original text")
                version_path = item.data(0, ROLE_VERSION_PATH)
                view_original.setEnabled(
                    kind == "version_document"
                    or (f in self._files and f in self._readings)
                )
                view_original.triggered.connect(
                    lambda doc=f, item_kind=kind, version=version_path: (
                        self._view_original_document(doc, item_kind, version)
                    )
                )
            if kind == "draft_document":
                menu.addSeparator()
                remove = menu.addAction("Remove document from project")
                project_path = item.data(0, ROLE_PROJECT_PATH)
                remove.setEnabled(project_path == self._active_project_path())
                remove.triggered.connect(
                    lambda: self._remove_tree_document(project_path, f)
                )
            return menu
        if kind in {"draft_keys", "version_keys"}:
            file_path = item.data(0, ROLE_FILE_PATH)
            show = menu.addAction("Show keys (Advanced YAML)")
            show.triggered.connect(lambda: self._on_project_tree_item_clicked(item, 0))
            copy_path = menu.addAction("Copy path")
            copy_path.setEnabled(bool(file_path))
            copy_path.triggered.connect(lambda: self._copy_path_to_clipboard(file_path))
            return menu
        if kind == "version":
            path = Path(item.data(0, ROLE_VERSION_PATH))
            copy = menu.addAction("Copy output to…")
            copy.triggered.connect(lambda: self._copy_version(path))
            return menu
        if kind == "draft":
            project_path = item.data(0, ROLE_PROJECT_PATH)
            add_documents = menu.addAction("Add documents…")
            add_documents.triggered.connect(
                lambda: self._add_documents_to_tree_project(project_path)
            )
            return menu
        if kind in {"meta", "meta_entry"}:
            project_path = item.data(0, ROLE_PROJECT_PATH)
            edit_meta = menu.addAction("Edit project meta…")
            edit_meta.triggered.connect(
                lambda: self._edit_tree_project_meta(project_path)
            )
            return menu
        if kind in {"excluded_group", "excluded_detection"}:
            return menu
        if item.data(0, ROLE_ACTIVE_PROJECT) or kind == "project":
            project_path = item.data(0, ROLE_PROJECT_PATH) or ""
            add_documents = menu.addAction("Add documents…")
            add_documents.triggered.connect(
                lambda: self._add_documents_to_tree_project(project_path)
            )
            menu.addSeparator()
            if item.data(0, ROLE_ACTIVE_PROJECT):
                rename = menu.addAction("Rename project…")
                rename.triggered.connect(self._on_rename_project)
                menu.addSeparator()
            self._add_remove_project_action(menu, project_path)
            if project_path:
                delete = menu.addAction("Delete project…")
                delete.triggered.connect(lambda: self._delete_project(project_path))
            return menu
        path = item.data(0, ROLE_PROJECT_PATH)
        if not path:
            return None
        add_documents = menu.addAction("Add documents…")
        add_documents.triggered.connect(
            lambda: self._add_documents_to_tree_project(path)
        )
        menu.addSeparator()
        self._add_remove_project_action(menu, path)
        delete = menu.addAction("Delete project…")
        delete.triggered.connect(lambda: self._delete_project(path))
        return menu

    @staticmethod
    def _tree_item_folder(item):
        """The directory "Open in file manager" shows for a tree item, or None."""
        kind = item.data(0, ROLE_PROJECT_KIND)
        project_path = item.data(0, ROLE_PROJECT_PATH)
        workdir = Path(project_path).parent if project_path else None
        if kind in {"draft_document", "version_document"}:
            document = item.data(0, ROLE_DOCUMENT_PATH)
            return Path(document).parent if document else None
        if kind in {"draft_keys", "version_keys"}:
            file_path = item.data(0, ROLE_FILE_PATH)
            return Path(file_path).parent if file_path else None
        if kind == "version":
            version = item.data(0, ROLE_VERSION_PATH)
            return Path(version) if version else None
        if kind in {"draft", "excluded_group", "excluded_detection"}:
            return workdir / "draft" if workdir is not None else None
        return workdir

    def _add_open_folder_action(self, menu, folder):
        if menu.actions():
            menu.addSeparator()
        action = menu.addAction("Open in file manager")
        action.setEnabled(folder is not None and Path(folder).is_dir())
        if folder is not None:
            action.setToolTip(str(folder))
            action.triggered.connect(lambda: self._reveal_path(Path(folder)))
        return action

    def _copy_path_to_clipboard(self, path):
        if not path:
            return
        QApplication.clipboard().setText(str(path))
        self._set_status(f"Copied path {path}.")

    def _active_project_path(self):
        return (
            str(self._project.project_file.resolve())
            if self._project is not None and self._project.project_file is not None
            else ""
        )

    def _add_remove_project_action(self, menu, project_path):
        remove = menu.addAction("Remove project")
        remove.triggered.connect(lambda: self._remove_project_from_tree(project_path))
        return remove

    def _remove_recent_project(self, path):
        self._settings.remove_recent_project(path)
        self._refresh_project_list()

    def _remove_project_from_tree(self, path):
        """Drop a project from the sidebar. The workdir stays on disk."""
        path = str(path or "")
        active = self._project is not None and (
            (not path and self._project.project_file is None)
            or (
                path
                and self._project.project_file is not None
                and str(self._project.project_file.resolve()) == path
            )
        )
        if active:
            name = self._project.name
            if not self._confirm_discard():
                return False
            if path:
                self._settings.remove_recent_project(path)
            self._reset_session()
            self._project = None
            self._set_dirty(False)
            self._refresh_actions()
        elif path:
            self._settings.remove_recent_project(path)
            try:
                name = read_project(path).name
            except ProjectError:
                name = Path(path).name.removesuffix(PROJECT_SUFFIX)
        else:
            return False
        self._refresh_project_list()
        self._set_status(f"Removed project ‘{name}’ from the list.")
        return True

    def _delete_project(self, path, *, confirmed=False):
        """Permanently remove a project's dedicated workdir."""
        path = Path(path).expanduser().resolve()
        try:
            project = read_project(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not delete project", str(exc))
            return False
        workdir = project.workdir
        if workdir is None or path.name != CANONICAL_PROJECT_FILENAME:
            QMessageBox.warning(
                self,
                "Could not delete project",
                "Only self-contained DID project workdirs can be deleted here. "
                "Use ‘Remove project’ to drop a legacy project file from the list.",
            )
            return False
        if not confirmed and not self._confirm_project_deletion(project.name, workdir):
            return False
        active = (
            self._project is not None
            and self._project.project_file is not None
            and self._project.project_file.resolve() == path
        )
        try:
            delete_project_workdir(project)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not delete project", str(exc))
            return False
        self._settings.remove_recent_project(path)
        if active:
            self._reset_session()
            self._project = None
            self._set_dirty(False)
            self._refresh_actions()
        self._refresh_project_list()
        self._set_status(f"Deleted project ‘{project.name}’.")
        return True

    def _confirm_project_deletion(self, project_name, workdir):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Delete project permanently?")
        dialog.setText(f"Delete ‘{project_name}’ and everything in its workdir?")
        dialog.setInformativeText(f"{workdir}\n\nThis cannot be undone.")
        delete_button = dialog.addButton(
            "Delete project", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        return dialog.clickedButton() is delete_button

    def _add_documents_to_tree_project(self, project_path):
        active_path = (
            str(self._project.project_file.resolve())
            if self._project is not None and self._project.project_file is not None
            else ""
        )
        if project_path != active_path:
            if not project_path or not self._open_project_path(project_path):
                return
        self._on_open()

    def _edit_tree_project_meta(self, project_path):
        active_path = (
            str(self._project.project_file.resolve())
            if self._project is not None and self._project.project_file is not None
            else ""
        )
        if project_path != active_path:
            if not project_path or not self._open_project_path(project_path):
                return
        self._on_meta()

    def _remove_tree_document(self, project_path, document_path):
        active_path = (
            str(self._project.project_file.resolve())
            if self._project is not None and self._project.project_file is not None
            else ""
        )
        if project_path != active_path:
            return
        if self._viewing_version is not None:
            self._return_to_draft()
        if self._select_document(document_path):
            self._remove_selected_document()

    @staticmethod
    def _reveal_path(path):
        # A desktop can point inode/directory at the wrong app (e.g. git-cola);
        # prefer a real file manager and only fall back to the desktop opener.
        if open_in_file_manager(path):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _open_web_url(url):
        QDesktopServices.openUrl(QUrl(url))

    def _show_about(self):
        QMessageBox.about(
            self,
            "About DID",
            f"<h3>DID Pseudonymizer {__version__}</h3>"
            "<p>Local-first document pseudonymization and reviewed LLM handoffs.</p>"
            "<p>Released under the MIT License.</p>"
            f'<p><a href="{_PROJECT_URL}">{_PROJECT_URL}</a></p>',
        )

    def _copy_version(self, path):
        parent = QFileDialog.getExistingDirectory(
            self, "Copy output to directory", str(Path.home())
        )
        if not parent:
            return
        destination = Path(parent) / path.name
        if destination.exists():
            QMessageBox.warning(
                self, "Copy failed", f"Destination already exists: {destination}"
            )
            return
        try:
            shutil.copytree(path, destination)
        except OSError as exc:
            QMessageBox.warning(self, "Copy failed", str(exc))
            return
        self._set_status(f"Copied {path.name} to {destination}.")

    def _set_dirty(self, dirty=True):
        self._dirty = dirty
        self._update_window_title()
        self._update_keys_bar()

    def _ensure_project(self):
        if self._project is None:
            self._project = Project()
            self._set_dirty(True)
        return self._project

    def _texts(self) -> dict[Path, str]:
        """The current readings, as the strings detection and preview use."""
        return {path: reading.text for path, reading in self._readings.items()}

    def _readings_publishable(self) -> bool:
        bad = [
            reading.path.name
            for reading in self._readings.values()
            if not reading.readable
        ]
        if not bad:
            return True
        self._set_status(f"Not publishing. No readable text for {', '.join(bad)}.")
        return False

    def _toolbar_language(self) -> str:
        """Language currently shown in the toolbar."""
        if hasattr(self, "lang_combo"):
            selected = self.lang_combo.currentData()
            if selected:
                self._language = selected
        return self._language

    def _sync_project(self):
        if self._project is None:
            return
        self._project.source_paths = list(self._files)
        self._project.language = self._toolbar_language()
        self._project.detection_profile = self._detection_profile
        self._project.entity_config_yaml = self.yaml_edit.toPlainText()
        self._project.preview_format = self._output_mode

    def _set_preview_state(self, state):
        self.preview_panel.set_state(state)

    def _refresh_entity_table(self):
        return self.entity_panel.refresh_from_yaml(self.yaml_edit.toPlainText())

    def _show_entity_details(
        self, row, _column=None, _previous_row=None, _previous_column=None
    ):
        self.entity_panel.show_entity_details(row)

    def _on_entity_table_menu(self, pos, global_pos=None):
        if self._viewing_version is not None:
            return
        item = self.entity_table.itemAt(pos)
        if item is None:
            return
        menu = self._build_entity_context_menu(item.row())
        if menu is not None:
            self._entity_context_menu = menu
            self._entity_context_menu_popup_count += 1
            self._last_entity_context_labels = [
                action.text() for action in menu.actions() if not action.isSeparator()
            ]
            menu.aboutToHide.connect(self._clear_entity_context_menu)
            if not self._suppress_context_menu_popup:
                menu.popup(global_pos or self.entity_table.viewport().mapToGlobal(pos))

    def _clear_entity_context_menu(self):
        self._entity_context_menu = None

    def _build_entity_context_menu(self, row):
        """Build all actions available for an entity row or selection."""
        variants_item = self.entity_table.item(row, 2)
        if variants_item is None:
            return None
        selected_rows = {
            index.row() for index in self.entity_table.selectionModel().selectedRows()
        }
        if row not in selected_rows:
            self.entity_table.clearSelection()
            self.entity_table.selectRow(row)
        menu = QMenu(self)
        add_variant = menu.addAction("Add variant…")
        add_variant.setEnabled(len(self._selected_entity_rows()) == 1)
        add_variant.triggered.connect(lambda: self._add_entity_variant(row))
        menu.addSeparator()
        change_type = menu.addMenu("Change type")
        # PySide can release a locally scoped submenu wrapper even though its
        # QAction remains in the parent menu. Retain it for the menu lifetime.
        menu._submenus = [change_type]
        current_types = {
            self.entity_table.item(selected_row, 2).data(_ROLE_ENTITY_TYPE)
            for selected_row in self._selected_entity_rows()
            if self.entity_table.item(selected_row, 2) is not None
        }
        for entity_type in REVIEW_ENTITY_TYPES:
            action = change_type.addAction(entity_type)
            action.setEnabled(current_types != {entity_type})
            action.triggered.connect(
                lambda _checked=False, target=entity_type: (
                    self._change_selected_entity_type(target)
                )
            )
        selected_rows = self._selected_entity_rows()
        selected_types = {
            selected_item.data(_ROLE_ENTITY_TYPE)
            for selected_row in selected_rows
            if (selected_item := self.entity_table.item(selected_row, 2)) is not None
        }
        if selected_types == {"PERSON"}:
            menu.addSeparator()
            identity_count = len(
                {
                    self.entity_table.item(selected_row, 2).data(_ROLE_ENTITY_ID)
                    for selected_row in selected_rows
                }
            )
            label = (
                "Do not pseudonymize"
                if identity_count == 1
                else f"Do not pseudonymize ({identity_count} selected)"
            )
            not_name = menu.addAction(label)
            not_name.triggered.connect(
                lambda _checked=False, rows=selected_rows: self._mark_not_names(rows)
            )
        return menu

    def _add_entity_variant(self, row, variant=None):
        item = self.entity_table.item(row, 2) if row >= 0 else None
        if item is None:
            self._set_status("Select an identity row before adding a variant.")
            return
        entity_type = item.data(_ROLE_ENTITY_TYPE)
        if not entity_type:
            type_item = self.entity_table.item(row, 0)
            entity_type = type_item.text() if type_item is not None else None
        entity_id = item.data(_ROLE_ENTITY_ID)
        position = item.data(_ROLE_TOKEN_INDEX)
        if not entity_type or not entity_id and not position:
            QMessageBox.warning(
                self,
                "Could not add variant",
                "This row does not identify a stored entity. Re-run detection "
                "or create the identity from a selection first.",
            )
            return
        label = entity_id or f"{entity_type} #{position}"
        if variant is None:
            variant, accepted = QInputDialog.getText(
                self,
                "Add identity variant",
                f"Variant for {entity_type} · {label}:",
            )
            if not accepted:
                return
        variant = str(variant).strip()
        if not variant:
            return
        try:
            if entity_id:
                changed_yaml = pipeline.add_entity_variant(
                    self.yaml_edit.toPlainText(), entity_type, entity_id, variant
                )
            else:
                changed_yaml = pipeline.add_entity_variant_at(
                    self.yaml_edit.toPlainText(), entity_type, position, variant
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not add variant", str(exc))
            return
        if changed_yaml == self.yaml_edit.toPlainText():
            self._set_status(
                f"“{variant}” is already a variant of {label} — other "
                "capitalizations (e.g. ALL CAPS) are replaced automatically."
            )
            return
        self._apply_manual_entity_yaml(
            changed_yaml, f"Added variant “{variant}” to {label}."
        )

    def _selected_entity_rows(self):
        return self.entity_panel.selected_rows()

    def _update_review_actions(self):
        self.entity_panel.set_editable(self._viewing_version is None)
        self.entity_panel.update_review_actions()

    def _change_selected_entity_type(self, target_type):
        entity_ids = {
            item.data(_ROLE_ENTITY_ID)
            for row in self._selected_entity_rows()
            if (item := self.entity_table.item(row, 2)) is not None
            and item.data(_ROLE_ENTITY_ID)
        }
        if not entity_ids:
            return
        try:
            changed_yaml = pipeline.change_entity_types(
                self.yaml_edit.toPlainText(), entity_ids, target_type
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not change entity type", str(exc))
            return
        count = len(entity_ids)
        self._apply_manual_entity_yaml(
            changed_yaml,
            f"Changed {count} identit{'y' if count == 1 else 'ies'} to {target_type}.",
        )

    def _mark_not_name(self, row):
        """Compatibility wrapper for excluding one PERSON table row."""
        self._mark_not_names([row])

    def _mark_not_names(self, rows=None):
        """Exclude all selected PERSON rows and persist their unique variants."""
        if self._project is None:
            self._ensure_project()
        if self._project.project_file is None and not self._save_project():
            return
        rows = self._selected_entity_rows() if rows is None else sorted(set(rows))
        variants_by_key = {}
        identity_ids = set()
        for row in rows:
            variants_item = self.entity_table.item(row, 2)
            if (
                variants_item is None
                or variants_item.data(_ROLE_ENTITY_TYPE) != "PERSON"
            ):
                continue
            identity_ids.add(variants_item.data(_ROLE_ENTITY_ID))
            for variant in variants_item.data(_ROLE_VARIANTS) or []:
                cleaned = str(variant).strip()
                if cleaned:
                    variants_by_key.setdefault(cleaned.casefold(), cleaned)
        variants = list(variants_by_key.values())
        if not variants:
            return
        try:
            exclusions = load_not_names(self._project)
            path = save_not_names(self._project, [*exclusions, *variants])
            filtered_yaml = pipeline.exclude_not_names(
                self.yaml_edit.toPlainText(), variants
            )
        except (ProjectError, ValueError) as exc:
            QMessageBox.warning(self, "Could not exclude name", str(exc))
            return
        self.yaml_edit.setPlainText(filtered_yaml)
        if self._save_keys_now() is None:
            return
        self._refresh_project_tree()
        identity_count = len(identity_ids)
        self._set_status(
            f"Marked {identity_count} identit{'y' if identity_count == 1 else 'ies'} "
            f"({len(variants)} variant(s)) as ‘Do not pseudonymize’ · "
            f"saved to draft/{path.name} and draft/{ENTITIES_FILENAME}."
        )

    def _merge_entity_rows(self, source_row, target_row):
        if self._viewing_version is not None:
            return
        source_item = self.entity_table.item(source_row, 2)
        target_item = self.entity_table.item(target_row, 2)
        if source_item is None or target_item is None:
            return
        if (
            source_item.data(_ROLE_ENTITY_TYPE) != "PERSON"
            or target_item.data(_ROLE_ENTITY_TYPE) != "PERSON"
        ):
            return
        source_id = source_item.data(_ROLE_ENTITY_ID)
        target_id = target_item.data(_ROLE_ENTITY_ID)
        variant = source_item.data(_ROLE_VARIANT)
        if not source_id or not target_id or source_id == target_id:
            return
        try:
            merged_yaml = pipeline.merge_person_entities(
                self.yaml_edit.toPlainText(), source_id, target_id, variant
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not merge identities", str(exc))
            return
        moved = f"variant “{variant}”" if variant else f"identity {source_id}"
        self._apply_manual_entity_yaml(merged_yaml, f"Merged {moved} into {target_id}.")

    def _filter_entities(self, query=None):
        self.entity_panel.filter_entities(query)

    def _set_busy(self, on, message=""):
        self.progress.setVisible(on)
        if on:
            self._set_preview_state("PROCESSING")
            self.progress.setRange(0, 0)  # indeterminate
            if message:
                self.status_label.setText(message)

    def _set_status(self, message):
        self.status_label.setText(message)

    # -------------------------------------------------- output verification ---
    def _set_verification(self, report):
        """Store a fresh verification report and drop any prior acknowledgement.

        An override applies to the output the reviewer actually looked at. Once
        the config changes and the preview is rebuilt, they have not seen the new
        output yet, so consent to export does not carry over.
        """
        self._verification = report
        self._verification_acknowledged = False

    def _verification_record(self):
        """Verification result for a version's audit manifest.

        Records what the scan found and whether the reviewer overrode it, so a
        version carries evidence of the check that was run on it rather than
        only of the settings used to produce it.
        """
        report = self._verification
        if report is None:
            return {"status": "not_run"}
        return {
            "status": "clean" if report.is_clean else "findings",
            "acknowledged": self._verification_acknowledged,
            **report.counts(),
        }

    def _verification_status(self, message):
        """Append a verification warning to a status-bar message."""
        report = self._verification
        if report is None or report.is_clean:
            return message
        return f"{message}  ⚠ {report.summary()}"

    def _confirm_verification(self, action_label):
        """Ask the reviewer to confirm an export when the output did not verify.

        Returns ``True`` when the action may proceed. Findings never hard-block:
        DID over-detects by design, so some findings are expected to be false
        positives and the reviewer stays the decision-maker.
        """
        report = self._verification
        if report is None or report.is_clean or self._verification_acknowledged:
            return True

        lines = []
        for finding in (*report.leaks, *report.suspected)[:12]:
            severity = "Leak" if finding in report.leaks else "Suspect"
            lines.append(
                f"• {severity}: “{finding.text}” — {finding.document} line {finding.line}"
            )
        remaining = len(report.leaks) + len(report.suspected) - len(lines)
        if remaining > 0:
            lines.append(f"• …and {remaining} more")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Output verification")
        box.setText(f"{report.summary()}\n\n{action_label} anyway?")
        box.setInformativeText(
            "Identifiers below are still readable in the pseudonymized output. "
            "Add them as entity variants and re-apply, or continue if they are "
            "false positives."
        )
        box.setDetailedText("\n".join(lines))
        proceed = box.addButton(
            f"{action_label} anyway", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not proceed:
            return False
        self._verification_acknowledged = True
        return True

    def _track(self, worker):
        self._workers.append(worker)

    def _advance_session_generation(self):
        self._session_generation += 1
        return self._session_generation

    def _run_if_current(self, generation, callback, *args):
        if generation == self._session_generation:
            callback(*args)

    def _connect_worker(self, worker, finished):
        generation = self._session_generation
        worker.finished.connect(
            lambda *args: self._run_if_current(generation, finished, *args)
        )
        worker.error.connect(
            lambda *args: self._run_if_current(generation, self._on_worker_error, *args)
        )

    def _select_document(self, path):
        path = Path(path)
        if path not in self._files:
            return False
        self._selected_file = path
        select_document_in_tree(self.project_tree, path)
        self._show_current_preview()
        self._refresh_actions()
        return True

    # ---------------------------------------------------------------- flow ---
    def _on_open(self):
        if not self._prepare_tree_project_for_documents():
            return
        paths = choose_document_paths(self, self._settings.directory("sources"))
        if paths:
            self._settings.set_directory("sources", paths[0].parent)
            self._add_paths(paths)

    def _prepare_tree_project_for_documents(self):
        """Activate the selected saved project before showing the source picker."""
        return prepare_tree_project_for_documents(
            has_active_project=self._project is not None,
            tree=self.project_tree,
            open_project_path=self._open_project_path,
            set_status=self._set_status,
        )

    def _add_paths(self, paths):
        if self._project is None and self.isVisible():
            self._on_new_project()
            if self._project is None:
                return
        project = self._ensure_project()
        additions, _files, temp_dirs = collect_document_additions(paths, self._files)
        self._temp_dirs.extend(temp_dirs)
        if not additions:
            self._set_status(NO_NEW_DOCUMENTS_STATUS)
            return
        project.source_paths.extend(
            path for path in additions if path not in project.source_paths
        )
        self._auto_version_pending = True
        self._set_dirty(True)
        self._load_paths([*self._files, *additions], collected=True)

    def _load_paths(self, paths, *, collected=False):
        files, temp_dirs = resolve_document_paths(paths, collected=collected)
        self._temp_dirs.extend(temp_dirs)
        if not files:
            self._set_status(NO_DOCUMENTS_STATUS)
            return
        self._advance_session_generation()
        self._stash_review()
        self._files = files
        if self._selected_file not in files:
            self._selected_file = files[0]
        self._readings = {}
        self._anonymized = {}
        self._anonymizer = None
        self._yaml_text = ""
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.clear()
        self.yaml_edit.blockSignals(False)
        self.preview.clear()
        self._set_preview_state("EMPTY")
        self._refresh_entity_table()
        self._refresh_file_list()
        self._refresh_actions()
        self._start_extract()

    def _remove_selected_document(self):
        selected = self._current_file()
        if selected is None:
            return
        old_index = self._files.index(selected)
        self._files = [path for path in self._files if path != selected]
        self._readings.pop(selected, None)
        self._anonymized.pop(selected, None)
        if self._project is not None:
            self._project.source_paths = [
                path for path in self._project.source_paths if path != selected
            ]
        self._set_dirty(True)
        self._refresh_file_list()
        self.preview.clear()
        if self._files:
            self._select_document(self._files[min(old_index, len(self._files) - 1)])
        else:
            self._selected_file = None
            self._set_preview_state("EMPTY")
        self._refresh_actions()

    def _start_extract(self):
        self._set_busy(True, f"Extracting text from {len(self._files)} document(s)…")
        worker = ExtractWorker(self._files)
        generation = self._session_generation
        worker.progress.connect(
            lambda i, n, name: self._run_if_current(
                generation,
                self._set_status,
                f"Extracting {i}/{n}: {name}",
            )
        )
        self._connect_worker(worker, self._on_extracted)
        self._track(worker)
        worker.start()

    def _on_extracted(self, extracted):
        self._readings = extracted
        self._set_busy(False)
        self._set_status(
            f"{len(self._readings)} document(s) extracted — detecting entities…"
        )
        self._refresh_file_list()
        self._refresh_actions()
        if self._selected_file is not None:
            self._select_document(self._selected_file)
        self._on_anonymize()

    def _current_not_names(self):
        """Project-level "do not pseudonymize" exclusions, or an empty list."""
        if self._project is None or self._project.project_file is None:
            return []
        try:
            return load_not_names(self._project)
        except (ProjectError, ValueError):
            return []

    def _stash_review(self):
        """Remember the current review so the next detection pass keeps it."""
        if self._pending_review_yaml is None and self._viewing_version is None:
            self._pending_review_yaml = self.yaml_edit.toPlainText()

    def _on_redetect(self, _checked=False):
        """Detect again on purpose: new detections are merged into the review.

        Unlike reopening a project, the review is not reused as-is even when
        the documents are unchanged — the point is to pick up what the new
        language or profile finds.
        """
        self._force_review_merge = True
        self._on_anonymize()

    def _on_anonymize(self):
        if not self._readings:
            return
        self._stash_review()
        language = self._toolbar_language()
        if self._project is not None:
            self._project.language = language
        self._advance_session_generation()
        self._set_busy(True, "Detecting entities…")
        if self._factory is Anonymizer:
            # Start the child here, on the GUI thread: forking a Qt process
            # from inside the worker thread can deadlock.
            try:
                detect_process.ensure_started()
            except Exception as exc:
                self._on_worker_error(f"Could not start detection: {exc}")
                return
        worker = AnonymizeWorker(
            self._texts(),
            language,
            detection_profile=self._detection_profile,
            anonymizer_factory=self._factory,
            not_names=self._current_not_names(),
        )
        generation = self._session_generation
        worker.status.connect(
            lambda message: self._run_if_current(generation, self._set_status, message)
        )
        self._connect_worker(worker, self._on_anonymized)
        self._track(worker)
        worker.start()

    def _on_anonymized(self, anonymizer, yaml_str, anonymized, report=None):
        """Settle which keys this detection pass uses, then pseudonymize with them.

        A review never silently disappears: when it was built from exactly
        these documents it is reused as-is; otherwise it is merged with the new
        detections (reviewed identities kept, new ones appended).
        """
        yaml_str = pipeline.drop_fragment_organizations(yaml_str)
        yaml_str = pipeline.stamp_reading(
            yaml_str, pipeline.readings_digest(self._readings)
        )
        review = self._pending_review_yaml or ""
        self._pending_review_yaml = None
        force_merge = self._force_review_merge
        self._force_review_merge = False
        effective_yaml = yaml_str
        origin = "fresh"
        status = f"Fresh detection · {pipeline.count_identities(yaml_str)} identities."
        if not force_merge and pipeline.review_matches_readings(review, self._readings):
            effective_yaml = pipeline.drop_fragment_organizations(review)
            origin = "saved_review"
            status = "Applied the saved review (documents unchanged)."
        elif pipeline.count_identities(review):
            try:
                effective_yaml, kept, added = pipeline.merge_review(review, yaml_str)
            except ValueError:
                status = (
                    "The previous review was not valid YAML, so entities were "
                    "detected afresh."
                )
            else:
                origin = "merged"
                status = f"Merged review: kept {kept} reviewed, added {added} new."
        if self._project is not None and self._project.project_file is not None:
            try:
                exclusions = load_not_names(self._project)
                effective_yaml = pipeline.exclude_not_names(effective_yaml, exclusions)
            except (ProjectError, ValueError) as exc:
                self._on_worker_error(str(exc))
                return
        self._anonymizer = anonymizer
        self._keys_origin = origin
        self._detection_status = status
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.setPlainText(effective_yaml)
        self.yaml_edit.blockSignals(False)
        self._refresh_entity_table()
        self._save_keys_now(effective_yaml)
        if effective_yaml != yaml_str:
            worker = PseudoWorker(
                anonymizer,
                effective_yaml,
                self._texts(),
                not_names=self._current_not_names(),
            )
            self._connect_worker(worker, self._on_reapplied)
            self._track(worker)
            worker.start()
            return
        self._yaml_text = yaml_str
        self._anonymized = anonymized
        self._set_verification(report)
        self._set_busy(False)
        self._set_status(
            self._verification_status(
                f"{status} {len(self._files)} document(s) · ready to save."
            )
        )
        self._refresh_file_list()
        self._refresh_actions()
        self._show_current_preview()
        self._set_preview_state("PSEUDONYMIZED")
        self._create_pending_import_version()
        self._finish_pending_tree_navigation()

    def _on_yaml_changed(self):
        valid = self._refresh_entity_table()
        if (
            not self._loading_project
            and self._project is not None
            and self._viewing_version is None
        ):
            self._project.entity_config_yaml = self.yaml_edit.toPlainText()
            self._keys_origin = "edited"
            # Keys autosave to draft/entities.yaml; only an unsaved project
            # has anything left to lose.
            if self._keys_path() is None:
                self._set_dirty(True)
            else:
                self._keys_save_timer.start()
        self._update_keys_bar()
        # Live re-apply only makes sense once we have an anonymizer.
        if self._anonymizer is not None and valid:
            self._reapply_timer.start()

    def _reapply_config(self):
        if self._anonymizer is None or not self._readings:
            return
        self._advance_session_generation()
        yaml_text = self.yaml_edit.toPlainText()
        worker = PseudoWorker(
            self._anonymizer,
            yaml_text,
            self._texts(),
            not_names=self._current_not_names(),
        )
        self._connect_worker(worker, self._on_reapplied)
        self._track(worker)
        worker.start()

    def _on_reapplied(self, anonymized, report=None):
        self._yaml_text = self.yaml_edit.toPlainText()
        self._anonymized = anonymized
        self._set_verification(report)
        self._set_busy(False)
        self._refresh_file_list()
        self._refresh_actions()
        self._show_current_preview()
        self._set_preview_state("PSEUDONYMIZED")
        self._refresh_entity_table()
        status = getattr(self, "_detection_status", None) or "Keys re-applied."
        self._detection_status = None
        self._set_status(self._verification_status(status))
        continuation, self._after_reapply = self._after_reapply, None
        self._create_pending_import_version()
        self._finish_pending_tree_navigation()
        if continuation is not None:
            continuation()

    def _ensure_fresh_then(self, continuation):
        """True when the preview reflects the current keys; else refresh first.

        A pending debounce or an in-flight worker would otherwise leave the
        reviewer confirming output made from older keys than the ones exported.
        The re-apply runs off the UI thread and *continuation* resumes the
        export once it lands.
        """
        if (
            self._viewing_version is not None
            or self._anonymizer is None
            or not self._readings
        ):
            return True
        pending = self._reapply_timer.isActive() or (
            self._yaml_text != self.yaml_edit.toPlainText()
        )
        if not pending:
            return True
        yaml_text = self.yaml_edit.toPlainText()
        try:
            pipeline.read_keys(yaml_text)
        except ValueError as exc:
            QMessageBox.warning(self, "Keys are not valid", str(exc))
            return False
        self._reapply_timer.stop()
        self._after_reapply = continuation
        self._set_busy(True, "Applying the latest keys before export…")
        self._reapply_config()
        return False

    def _start_version_export(self, *, label, mode, trigger, failure_title):
        """Write the project output off the UI thread; the window stays responsive."""
        if self._export_worker is not None:
            self._set_status("The output is already being written…")
            return False
        project = self._project
        project.export_mode = mode
        project.export_destination = project.workdir / "versions"
        self._sync_project()
        try:
            write_project(project)
            stage = begin_version(project, label)
        except ProjectError as exc:
            QMessageBox.warning(self, failure_title, str(exc))
            return False
        files = [path for path in self._files if path in self._anonymized]
        yaml_text = self.yaml_edit.toPlainText()
        texts = self._texts()
        anonymizer = self._anonymizer
        # finalize_version reads the project; hand the thread its own snapshot.
        snapshot = dataclasses.replace(
            project,
            source_paths=list(project.source_paths),
            metadata=dict(project.metadata),
        )
        processing = {
            "language": self._language,
            "detection_profile": self._detection_profile,
            "trigger": trigger,
            "verification": self._verification_record(),
        }
        export = {"format": project.export_format, "mode": mode}
        keys = self._keys_manifest_record(yaml_text)

        def task():
            pipeline.save_version_outputs(
                files, anonymizer, yaml_text, mode, stage.path, source_texts=texts
            )
            return finalize_version(
                snapshot,
                stage,
                processing=processing,
                export=export,
                app_version=__version__,
                keys=keys,
            )

        worker = TaskWorker(task)
        self._export_worker = worker

        def done(version):
            self._export_worker = None
            self._set_busy(False)
            self._set_preview_state(self._preview_state_for_current())
            self._set_dirty(False)
            self._refresh_project_list()
            self._refresh_actions()
            self._set_status(f"Wrote the output · {len(files)} document(s).")

        def failed(message):
            self._export_worker = None
            abort_version(stage)
            self._set_busy(False)
            self._set_preview_state(self._preview_state_for_current())
            self._refresh_actions()
            QMessageBox.warning(self, failure_title, message)

        worker.finished.connect(done)
        worker.error.connect(failed)
        self._track(worker)
        self._set_busy(True, f"Writing the output ({len(files)} document(s))…")
        self._refresh_actions()
        worker.start()
        return True

    def _preview_state_for_current(self):
        if self._viewing_version is not None:
            return "VERSION"
        if self._current_file() in self._anonymized:
            return {"redacted": "REDACTED", "synthetic": "SYNTHETIC"}.get(
                self._output_mode, "PSEUDONYMIZED"
            )
        return "RAW" if self._current_file() in self._readings else "EMPTY"

    def _keys_manifest_record(self, yaml_text):
        return {
            "origin": self._keys_origin or "edited",
            "identities": pipeline.count_identities(yaml_text),
            "excluded": len(self._current_not_names()),
        }

    def _create_pending_import_version(self):
        """Snapshot a successfully anonymized document import as a new version."""
        if not self._auto_version_pending:
            return
        if not self._readings_publishable():
            return
        if (
            self._project is None
            or self._project.project_file is None
            or not self._anonymized
        ):
            return
        if not self._ensure_fresh_then(self._create_pending_import_version):
            return
        self._auto_version_pending = False
        self._start_version_export(
            label="",
            mode=self._project.export_mode or "multi",
            trigger="document_import",
            failure_title="Automatic version failed",
        )

    # ------------------------------------------------------------ clipboard ---
    def _current_file(self):
        return self._selected_file if self._selected_file in self._files else None

    def _copy_menu(self, f):
        """Build the copy context menu for document ``f`` (may be None).

        Entries are enabled only for pseudonymized documents so raw text can
        never leak to the clipboard.
        """
        menu = QMenu(self)
        if f is not None and self._original_text(f) is not None:
            label = (
                "Show pseudonymized text"
                if self._show_original
                else "Show original text"
            )
            toggle = menu.addAction(label)
            toggle.triggered.connect(self._toggle_original_preview)
            menu.addSeparator()
        copy_one = menu.addAction("Copy document (pseudonymized)")
        copy_one.setEnabled(f in self._anonymized)
        copy_one.triggered.connect(lambda: self._copy_document(f))
        copy_all = menu.addAction("Copy all documents (combined)")
        copy_all.setEnabled(bool(self._anonymized))
        copy_all.triggered.connect(self._copy_all)
        return menu

    def _extend_preview_menu(self, menu):
        selected_text = self.preview.textCursor().selectedText().strip()
        if selected_text and self._viewing_version is None:
            selected_text = pipeline.resolve_selection_placeholders(
                selected_text, self.yaml_edit.toPlainText()
            ).strip()
            menu.addSeparator()
            create_menu = menu.addMenu("Create entity from selection")
            existing_menu = menu.addMenu("Add selection to existing entity")
            # Keep Python wrappers alive for the lifetime of the native menu.
            menu._entity_submenus = [create_menu, existing_menu]
            for entity_type in _ENTITY_TYPES:
                action = create_menu.addAction(
                    f"{entity_type}{' (default)' if entity_type == 'PERSON' else ''}"
                )
                action.triggered.connect(
                    lambda _checked=False, kind=entity_type, value=selected_text: (
                        self._create_entity_from_selection(value, kind)
                    )
                )
            try:
                entity_data = pipeline.read_keys(self.yaml_edit.toPlainText())
            except ValueError:
                entity_data = {}
            identity_count = 0
            for entity_type, entities in entity_data.items():
                if not isinstance(entities, list):
                    continue
                for entity in entities:
                    if not isinstance(entity, dict) or not entity.get("id"):
                        continue
                    identity_count += 1
                    entity_id = str(entity["id"])
                    variants = list(map(str, entity.get("variants", [])))
                    label = f"{entity_type} · {entity_id}"
                    if variants:
                        label += f" — {variants[0]}"
                    action = existing_menu.addAction(label)
                    action.triggered.connect(
                        lambda _checked=False, kind=str(entity_type), ident=entity_id, value=selected_text: (
                            self._add_selected_text_to_entity(value, kind, ident)
                        )
                    )
            existing_menu.setEnabled(identity_count > 0)
        for action in self._copy_menu(self._current_file()).actions():
            action.setParent(menu)
            menu.addAction(action)

    def _create_entity_from_selection(self, selected_text, entity_type="PERSON"):
        try:
            changed_yaml = pipeline.add_entity(
                self.yaml_edit.toPlainText(), selected_text, entity_type
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create identity", str(exc))
            return
        self._apply_manual_entity_yaml(
            changed_yaml, f"Created {entity_type} identity from selected text."
        )

    def _add_selected_text_to_entity(self, selected_text, entity_type, entity_id):
        try:
            changed_yaml = pipeline.add_entity_variant(
                self.yaml_edit.toPlainText(), entity_type, entity_id, selected_text
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not add identity variant", str(exc))
            return
        self._apply_manual_entity_yaml(
            changed_yaml, f"Added selected text to {entity_id}."
        )

    def _apply_manual_entity_yaml(self, changed_yaml, status):
        """Apply a review edit and save it to the draft keys file right away."""
        self._ensure_project()
        self.yaml_edit.setPlainText(changed_yaml)
        path = self._save_keys_now()
        if path is None:
            self._set_status(f"{status} Keys in memory only — save the project.")
            return
        self._set_status(f"{status} Saved to draft/{path.name}.")

    # ---------------------------------------------------------------- keys ---
    def _keys_path(self):
        """The draft keys file, or None for unsaved and legacy projects."""
        if self._project is None:
            return None
        return draft_entities_path(self._project)

    def _save_keys_now(self, text=None):
        """Write the draft keys file immediately. Returns its path, or None.

        An unsaved (or legacy) project has no keys file yet; its keys stay in
        memory and the project is marked dirty instead.
        """
        self._keys_save_timer.stop()
        if self._project is None or self._viewing_version is not None:
            return None
        text = self.yaml_edit.toPlainText() if text is None else text
        self._project.entity_config_yaml = text
        if self._keys_path() is None:
            self._set_dirty(True)
            self._update_keys_bar()
            return None
        try:
            path = save_draft_entities(self._project, text)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not save keys", str(exc))
            self._update_keys_bar()
            return None
        self._keys_saved_text = text
        self._update_keys_bar()
        return path

    def _autosave_keys(self):
        """Debounced autosave: only valid YAML replaces the file on disk."""
        if self._project is None or self._viewing_version is not None:
            return
        text = self.yaml_edit.toPlainText()
        if text == self._keys_saved_text:
            return
        if text.strip():
            try:
                pipeline.read_keys(text)
            except ValueError:
                self._update_keys_bar()
                return
        self._save_keys_now(text)
        if self._project_tree_panel_ready():
            self.project_tree_panel.update_active_badge(
                self._project, self._project.name, self._source_digest_cache
            )

    def _project_tree_panel_ready(self):
        return hasattr(self, "project_tree_panel")

    def _update_keys_bar(self):
        """Say which keys file is behind the entity table and the preview."""
        if not hasattr(self, "keys_bar"):
            return
        text = self.yaml_edit.toPlainText()
        count = pipeline.count_identities(text)
        workdir = self._project.workdir if self._project is not None else None
        if self._viewing_version is not None:
            path = self._version_keys_path(self._viewing_version)
            shown = (
                path.relative_to(workdir).as_posix()
                if workdir is not None and path.is_relative_to(workdir)
                else str(path)
            )
            self.keys_bar.set_source(
                f"Keys: {shown} · {_identities(count)} · read-only · current output",
                tooltip=str(path),
                folder=path.parent,
            )
            return
        if self._project is None:
            self.keys_bar.set_source(
                "Keys: none yet — add documents to detect entities."
            )
            return
        valid = True
        if text.strip():
            try:
                pipeline.read_keys(text)
            except ValueError:
                valid = False
        path = self._keys_path()
        if path is None:
            where = (
                "inside the legacy project file"
                if self._project.legacy
                else "in memory only — save the project to keep them"
            )
            self.keys_bar.set_source(
                f"Keys: {where} · {_identities(count)}", warn=not valid
            )
            return
        parts = [f"Keys: draft/{path.name}", _identities(count)]
        if not valid:
            parts.append("invalid YAML — not saved; preview uses the last valid keys")
        elif text == self._keys_saved_text:
            parts.append("saved ✓")
        else:
            parts.append("saving…")
        if self._keys_origin:
            parts.append(keys_origin_label(self._keys_origin))
        excluded = len(self._current_not_names())
        tooltip = str(path)
        if excluded:
            tooltip += f"\n{excluded} name(s) excluded in draft/not_names.json"
        self.keys_bar.set_source(
            " · ".join(parts), tooltip=tooltip, folder=path.parent, warn=not valid
        )

    @staticmethod
    def _version_keys_path(version_path):
        """A version's keys file; legacy versions kept it as output/config.yaml."""
        version_path = Path(version_path)
        path = version_path / ENTITIES_FILENAME
        if path.exists():
            return path
        legacy = sorted((version_path / "output").rglob("config.yaml"))
        return legacy[0] if legacy else path

    def _copy_document(self, f):
        text = self._display_text(f)
        if text is None:
            return
        QApplication.clipboard().setText(text)
        self._set_status(f"Copied {f.name} to clipboard.")

    def _copy_all(self):
        parts = [
            f"= {self._llm_title_token(index)}\n\n{self._display_text(f)}"
            for index, f in enumerate(self._llm_ordered_paths(), 1)
        ]
        if not parts:
            return
        QApplication.clipboard().setText("\n\n".join(parts) + "\n")
        self._set_status(f"Copied {len(parts)} document(s) to clipboard.")

    @staticmethod
    def _llm_title_token(index):
        """Assigned, non-identifying title for the *index*-th handoff document."""
        return f"#(DOC{index}V1)"

    def _llm_ordered_paths(self):
        """Pseudonymized documents in display order — the handoff numbering."""
        return [path for path in self._files if path in self._anonymized]

    def _llm_document(self, path, index):
        return {
            "id": f"document-{index:03d}",
            "title_token": self._llm_title_token(index),
            "content": self._display_text(path),
        }

    def _llm_documents(self):
        """Return safely labelled pseudonymized documents for LLM handoff.

        A source filename identifies the case as surely as the body text does —
        `John_Doe_kontrakt.pdf` defeats the entire point of pseudonymizing what
        is inside it. The name never enters this payload; documents are
        addressed by an assigned title token instead, and the real names stay in
        the project audit record.
        """
        return [
            self._llm_document(path, index)
            for index, path in enumerate(self._llm_ordered_paths(), 1)
        ]

    def _llm_metadata_markdown(self):
        metadata = self._project.metadata if self._project is not None else {}
        lines = ["# Project meta"]
        for key, value in metadata.items():
            if value in (None, "", []):
                continue
            rendered = (
                ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            )
            lines.append(f"- **{key.replace('_', ' ').title()}:** {rendered}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _llm_markdown(self):
        parts = []
        metadata = self._llm_metadata_markdown()
        if metadata:
            parts.append(metadata)
        parts.extend(
            f"# {document['title_token']}\n\n{document['content']}"
            for document in self._llm_documents()
        )
        return "\n\n".join(parts) + "\n"

    def _llm_json(self):
        payload = {
            "format": "did-pseudonymized-session",
            "version": 1,
            "rendering": self._output_mode,
            "metadata": self._project.metadata if self._project is not None else {},
            "documents": self._llm_documents(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _current_llm_document(self):
        path = self._current_file()
        if path not in self._anonymized:
            return None
        # Number from the full ordered set, not from 1, so a document keeps the
        # same token whether it is copied alone or alongside the others.
        ordered = self._llm_ordered_paths()
        return self._llm_document(path, ordered.index(path) + 1)

    def _copy_current_llm_markdown(self):
        document = self._current_llm_document()
        if document is None:
            return
        if not self._confirm_verification("Copy"):
            return
        parts = [f"# {document['title_token']}\n\n{document['content']}"]
        metadata = self._llm_metadata_markdown()
        if metadata:
            parts.insert(0, metadata)
        QApplication.clipboard().setText("\n\n".join(parts) + "\n")
        self._set_status("Copied current pseudonymized document as Markdown.")

    def _copy_current_llm_json(self):
        document = self._current_llm_document()
        if document is None:
            return
        if not self._confirm_verification("Copy"):
            return
        payload = {
            "format": "did-pseudonymized-session",
            "version": 1,
            "rendering": self._output_mode,
            "metadata": self._project.metadata if self._project is not None else {},
            "documents": [document],
        }
        QApplication.clipboard().setText(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        self._set_status("Copied current pseudonymized document as JSON.")

    def _copy_llm_markdown(self):
        documents = self._llm_documents()
        if not documents:
            return
        if not self._confirm_verification("Copy"):
            return
        QApplication.clipboard().setText(self._llm_markdown())
        self._set_status(
            f"Copied {len(documents)} pseudonymized document(s) as Markdown."
        )

    def _copy_llm_json(self):
        documents = self._llm_documents()
        if not documents:
            return
        if not self._confirm_verification("Copy"):
            return
        QApplication.clipboard().setText(self._llm_json())
        self._set_status(f"Copied {len(documents)} pseudonymized document(s) as JSON.")

    def _save_llm_zip(self, _checked=False, *, destination=None):
        documents = self._llm_documents()
        if not documents:
            return
        if not self._confirm_verification("Export"):
            return
        if destination is None:
            initial = self._settings.directory("exports")
            suggested = (
                f"{self._project.name if self._project else 'did'}-llm-session.zip"
            )
            destination, _selected_filter = QFileDialog.getSaveFileName(
                self,
                "Save safe LLM session package",
                str(Path(initial) / suggested) if initial else suggested,
                "ZIP archives (*.zip)",
            )
            if not destination:
                return
        destination = Path(destination)
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(
                destination, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("combined.md", self._llm_markdown())
                archive.writestr("session.json", self._llm_json())
                archive.writestr(
                    "README.txt",
                    "Privacy-safe documents prepared by DID.\n"
                    f"Rendering mode: {self._output_mode}.\n"
                    "Original filenames, entity configuration, and identity "
                    "mappings are intentionally excluded.\n"
                    "Each document is titled with an assigned #(DOC<n>V1) "
                    "token; the real filenames stay in the project.\n",
                )
                for document in documents:
                    archive.writestr(
                        f"documents/{document['id']}.md",
                        document["content"] + "\n",
                    )
        except OSError as exc:
            QMessageBox.warning(self, "Could not save LLM package", str(exc))
            return
        self._settings.set_directory("exports", destination.parent)
        self._set_status(
            f"Saved safe LLM session package with {len(documents)} document(s)."
        )

    def _display_text(self, f):
        """Pseudonymized text of ``f`` in the selected output mode, or None."""
        text = self._anonymized.get(f)
        if text is not None:
            if self._output_mode == "plain":
                text = pipeline.to_written_out(text)
            elif self._output_mode == "redacted":
                text = pipeline.to_redacted(text)
            elif self._output_mode == "synthetic":
                project_seed = (
                    self._project.project_id if self._project is not None else "did"
                )
                text = pipeline.to_synthetic(
                    text,
                    self.yaml_edit.toPlainText(),
                    self._language,
                    project_seed,
                )
        return text

    def _show_preview_find(self):
        self.preview_panel.show_find()

    def _hide_preview_find(self):
        self.preview_panel.hide_find()

    def _find_preview_text(self, _text=None, *, backward=False):
        return self.preview_panel.find_text(_text, backward=backward)

    def _focus_preview_entity(self):
        """Select the entity row represented by the token under the cursor."""
        matched = token_at_position(
            self.preview.toPlainText(), self.preview.textCursor().position()
        )
        if matched is None:
            return False
        entity_type, number, variant = matched
        return self.entity_panel.select_entity(entity_type, number, variant)

    def _version_source_candidates(self):
        """Source documents a published version could have been rendered from."""
        state = self._draft_view_state or {}
        files = [Path(item) for item in state.get("files") or []]
        if files:
            return files
        # Reopened projects (no draft pane yet) still reference their sources.
        if self._project is not None:
            return [Path(item) for item in self._project.source_paths]
        return []

    def _original_source_for(self, path):
        """The source document(s) a version output was rendered from, or None."""
        path = Path(path)
        candidates = self._version_source_candidates()
        if path.name == "combined.typ":
            return candidates or None
        stem = path.stem
        for marker in ("_pseudonymized", "_pseudo"):
            if stem.endswith(marker):
                stem = stem[: -len(marker)]
                break
        matches = [source for source in candidates if source.stem == stem]
        return [matches[0]] if len(matches) == 1 else None

    def _loaded_reading(self, source):
        """An already-read reading for *source*, or None. Never touches disk."""
        source = Path(source)
        draft_readings = (self._draft_view_state or {}).get("extracted") or {}
        for store in (draft_readings, self._readings):
            reading = store.get(source)
            if reading is not None:
                return reading
        return None

    def _ensure_original_loaded(self, path):
        """Read a version's missing sources off the UI thread.

        Selecting a document must never block: the draft's readings are almost
        always present, but a reopened project (or a version opened before
        detection finished) may not have them. Show what we have now and fill
        the preview in when the background read lands.
        """
        if self._viewing_version is None or self._draft_view_state is None:
            return
        sources = self._original_source_for(path)
        if not sources:
            return
        needed = [
            source
            for source in sources
            if self._loaded_reading(source) is None
            and source not in self._original_loads
        ]
        if not needed:
            return
        self._original_loads.update(needed)
        worker = TaskWorker(lambda: self._read_sources(needed))
        worker.finished.connect(
            lambda loaded, batch=needed: self._on_original_loaded(loaded, batch)
        )
        worker.error.connect(
            lambda _message, batch=needed: self._original_loads.difference_update(batch)
        )
        self._track(worker)
        worker.start()

    @staticmethod
    def _read_sources(sources):
        loaded = {}
        for source in sources:
            try:
                loaded[Path(source)] = pipeline.extract_one(source)
            except Exception:
                continue
        return loaded

    def _on_original_loaded(self, loaded, batch):
        self._original_loads.difference_update(batch)
        if not loaded or self._draft_view_state is None:
            return
        self._draft_view_state.setdefault("extracted", {}).update(loaded)
        self._refresh_actions()
        self._show_current_preview()

    def _can_show_original(self, path) -> bool:
        """Whether Original can show something, without loading any file."""
        if path is None:
            return False
        if self._viewing_version is None:
            return self._original_text(path) is not None
        sources = self._original_source_for(path)
        if not sources:
            return False
        draft_readings = (self._draft_view_state or {}).get("extracted") or {}
        return all(
            draft_readings.get(source) is not None or Path(source).exists()
            for source in sources
        )

    def _original_text(self, path) -> str | None:
        """Unanonymized extracted text for a draft source or a version output."""
        path = Path(path)
        if self._viewing_version is None:
            reading = self._readings.get(path)
            return reading.text if reading is not None else None
        sources = self._original_source_for(path)
        if not sources:
            return None
        if path.name == "combined.typ" or len(sources) > 1:
            parts = []
            for source in sources:
                reading = self._loaded_reading(source)
                if reading is not None:
                    parts.append(f"{source.name}\n\n{reading.text}")
            return "\n\n".join(parts) or None
        reading = self._loaded_reading(sources[0])
        return reading.text if reading is not None else None

    def _on_show_original(self, checked):
        self._show_original = bool(checked)
        self._show_current_preview()

    def _toggle_original_preview(self):
        self.original_button.setChecked(not self.original_button.isChecked())

    def _view_original_document(self, document_path, kind, version_path):
        document_path = Path(document_path)
        if kind == "version_document" and version_path:
            version_path = Path(version_path)
            if self._viewing_version != version_path:
                self._view_version(version_path)
        if document_path in self._files:
            self._select_document(document_path)
        if self.original_button.isChecked():
            self._show_current_preview()
        else:
            self.original_button.setChecked(True)

    def _show_current_preview(self):
        f = self._current_file()
        if f is None:
            self.preview.clear()
            self._set_preview_state("EMPTY")
            return
        if self._show_original:
            original = self._original_text(f)
            if original is not None:
                self.preview.setPlainText(original)
                self._set_preview_state("RAW")
                return
            # Version source not read yet: keep the UI responsive and fill in
            # the original once the background read lands.
            self._ensure_original_loaded(f)
        text = self._display_text(f)
        if text is not None:
            self.preview.setPlainText(text)
            if self._viewing_version is not None:
                self._set_preview_state("VERSION")
            else:
                state = {
                    "redacted": "REDACTED",
                    "synthetic": "SYNTHETIC",
                }.get(self._output_mode, "PSEUDONYMIZED")
                self._set_preview_state(state)
            return
        reading = self._readings.get(f) or (
            (self._draft_view_state or {}).get("extracted") or {}
        ).get(f)
        if reading is not None:
            self.preview.setPlainText(reading.text)
            self._set_preview_state("RAW")
            return
        # Nothing to show: never leave another document's text on screen.
        self.preview.clear()
        self._set_preview_state("EMPTY")

    def _on_save(self, mode):
        if not self._anonymized:
            return
        if not self._readings_publishable():
            return
        if not self._ensure_fresh_then(lambda: self._on_save(mode)):
            return
        if self._project is None or (
            self._project.project_file is None and not self._save_project()
        ):
            return
        if self.isVisible():
            config = pipeline.read_keys(self.yaml_edit.toPlainText())
            identity_count = sum(
                len(entities)
                for entities in config.values()
                if isinstance(entities, list)
            )
            action = (
                "Replace the current output"
                if list_versions(self._project)
                else "Write the project output"
            )
            summary = (
                f"{action}\n\nDocuments: {len(self._files)}"
                + f"\nDetected identities: {identity_count}"
                + f"\nWorkdir: {self._project.workdir}"
                + "\n\nThe output and shared variable files may contain "
                "original identifiers."
            )
            confirmed = QMessageBox.question(
                self,
                "Confirm output export",
                summary,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if confirmed != QMessageBox.StandardButton.Ok:
                return
        if not self._confirm_verification("Write output"):
            return
        self._start_version_export(
            label="", mode=mode, trigger="manual", failure_title="Export failed"
        )

    def _persist_language(self):
        """Store the selected language in the project file."""
        project = self._project
        if project is None:
            return False
        project.language = self._language
        if project.project_file is None:
            self._set_dirty(True)
            return False
        try:
            write_project(project)
        except ProjectError as exc:
            self._set_dirty(True)
            self._set_status(f"Could not store the language setting: {exc}")
            return False
        return True

    def _on_language(self, _index):
        lang = self.lang_combo.currentData()
        if not lang:
            return
        if lang == self._language:
            return
        self._language = lang
        self._persist_language()
        self._redetect_after_setting_change("Language")

    def _on_detection_profile(self, _index):
        profile = self.profile_combo.currentData()
        if not profile or profile == self._detection_profile:
            return
        self._detection_profile = profile
        if self._project is not None:
            self._project.detection_profile = profile
            if self._project.project_file is not None:
                try:
                    write_project(self._project)
                except ProjectError as exc:
                    self._set_dirty(True)
                    self._set_status(f"Could not store the detection profile: {exc}")
            else:
                self._set_dirty(True)
        self._redetect_after_setting_change("Detection profile")

    def _redetect_after_setting_change(self, setting):
        """Re-run detection with the new setting; the review is merged, not lost."""
        if self._viewing_version is not None:
            self._redetect_on_return = True
            self._set_status(
                f"{setting} saved — the draft is re-detected when you return to it."
            )
            return
        if self._readings:
            self._set_status(f"{setting} changed — re-detecting (review is kept)…")
            self._on_redetect()
        elif self._project is not None:
            self._set_status(f"{setting} saved with the project.")

    def _on_output_mode(self, _index):
        output_mode = self.output_combo.currentData()
        if not output_mode:
            return
        changed = output_mode != self._output_mode
        self._output_mode = output_mode
        if changed and self._project is not None:
            self._project.preview_format = output_mode
            self._set_dirty(True)
        self._show_current_preview()

    def _on_worker_error(self, message):
        self._pending_tree_navigation = None
        self._after_reapply = None
        self._force_review_merge = False
        review = self._pending_review_yaml
        self._pending_review_yaml = None
        if review and not self.yaml_edit.toPlainText().strip():
            # Detection failed after the review was cleared from view; put it
            # back (it is still on disk — autosave never saw the blank).
            self.yaml_edit.blockSignals(True)
            self.yaml_edit.setPlainText(review)
            self.yaml_edit.blockSignals(False)
            self._refresh_entity_table()
        self._set_busy(False)
        self._refresh_actions()
        self._set_preview_state("ERROR")
        self._set_status("Error.")
        QMessageBox.warning(self, "Error", message)

    # ------------------------------------------------------------ projects ---
    def _confirm_discard(self):
        if not self._dirty or not self.isVisible():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved project",
            "Save changes to the current project?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _reset_session(self):
        self._advance_session_generation()
        self._viewing_version = None
        self._draft_view_state = None
        self._original_loads.clear()
        self._version_yaml_overrides = {}
        self._version_snapshot_yaml = ""
        self._selected_file = None
        self._files = []
        self._readings = {}
        self._anonymized = {}
        self._anonymizer = None
        self._yaml_text = ""
        self._auto_version_pending = False
        self._pending_review_yaml = None
        self._after_reapply = None
        self._keys_origin = None
        self._keys_saved_text = None
        self._redetect_on_return = False
        self._keys_save_timer.stop()
        self._reapply_timer.stop()
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.setReadOnly(False)
        self.yaml_edit.clear()
        self.yaml_edit.blockSignals(False)
        self.preview.clear()
        self._set_preview_state("EMPTY")
        self._refresh_entity_table()
        self._update_keys_bar()
        self._refresh_file_list()
        self._refresh_actions()

    def _view_version(self, version_path):
        if self._project is None:
            return
        if self._viewing_version is not None:
            self._return_to_draft()
        if self._keys_save_timer.isActive():
            self._autosave_keys()
        self._reapply_timer.stop()
        self._draft_view_state = {
            "files": self._files,
            "extracted": self._readings,
            "anonymized": self._anonymized,
            "yaml": self.yaml_edit.toPlainText(),
            "selected": self._current_file(),
        }
        files = version_document_files(version_path)
        self._files = files
        self._readings = {}
        self._anonymized = {}
        for path in files:
            try:
                self._anonymized[path] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        snapshot = self._version_keys_path(version_path)
        yaml_text = snapshot.read_text(encoding="utf-8") if snapshot.exists() else ""
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.setPlainText(yaml_text)
        self.yaml_edit.setReadOnly(True)
        self.yaml_edit.blockSignals(False)
        self._viewing_version = version_path
        self._selected_file = files[0] if files else None
        self._refresh_entity_table()
        self._refresh_file_list()
        if self._selected_file is not None:
            self._select_document(self._selected_file)
        self._refresh_actions()
        self._set_preview_state("VERSION")
        self._update_keys_bar()
        self._set_status("Viewing the saved output · read-only.")

    def _return_to_draft(self):
        if self._viewing_version is None or self._draft_view_state is None:
            return
        state = self._draft_view_state
        self._viewing_version = None
        self._draft_view_state = None
        self._version_yaml_overrides = {}
        self._version_snapshot_yaml = ""
        self._files = state["files"]
        self._readings = state["extracted"]
        self._anonymized = state["anonymized"]
        self._selected_file = state["selected"]
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.setReadOnly(False)
        self.yaml_edit.setPlainText(state["yaml"])
        self.yaml_edit.blockSignals(False)
        self._refresh_entity_table()
        self._refresh_file_list()
        if self._selected_file is not None:
            self._select_document(self._selected_file)
        self._show_current_preview()
        self._refresh_actions()
        self._update_keys_bar()
        self._set_status("Viewing current draft.")
        if self._redetect_on_return:
            self._redetect_on_return = False
            self._redetect_after_setting_change("Settings")

    def _on_new_project(self, _checked=False, *, name=None, parent=None):
        if name is None and self.isVisible():
            name, accepted = QInputDialog.getText(
                self, "New project", "Project name:", text="Untitled project"
            )
            if not accepted:
                return
        name = (name or "Untitled project").strip()
        if not name:
            name = "Untitled project"
        if parent is None and self.isVisible():
            parent = QFileDialog.getExistingDirectory(
                self,
                "Choose parent directory for the project workdir",
                self._settings.directory("projects"),
            )
            if not parent:
                return
        if not self._confirm_discard():
            return
        self._reset_session()
        self._project = Project(
            name=name,
            language=self._toolbar_language(),
            detection_profile=self._detection_profile,
        )
        if parent is not None:
            try:
                create_project_workdir(self._project, parent, name)
            except ProjectError as exc:
                QMessageBox.warning(self, "Could not create project", str(exc))
                self._project = None
                return
            self._settings.add_recent_project(self._project.project_file)
            self._settings.set_directory("projects", parent)
            self._keys_saved_text = self._project.entity_config_yaml
        self._language = self._project.language
        self._detection_profile = self._project.detection_profile
        language_index = self.lang_combo.findData(self._language)
        self.lang_combo.setCurrentIndex(max(0, language_index))
        profile_index = self.profile_combo.findData(self._detection_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        self._set_dirty(True)
        self._refresh_actions()
        self._set_status(f"{name} — add documents to begin.")

    def _on_rename_project(self, _checked=False, *, name=None):
        if self._project is None:
            return
        if name is None:
            name, accepted = QInputDialog.getText(
                self,
                "Rename project",
                "Project name:",
                text=self._project.name,
            )
            if not accepted:
                return
        name = name.strip()
        if not name or name == self._project.name:
            return
        self._project.name = name
        self._set_dirty(True)
        self._set_status(f"Project renamed to “{name}”.")

    def _on_meta(self, _checked=False, *, metadata=None):
        if self._project is None:
            return
        if metadata is None:
            current = self._project.metadata
            dialog = QDialog(self)
            dialog.setWindowTitle("Project meta")
            form = QFormLayout(dialog)
            guidance = QLabel(
                "Add non-identifying context for this project and its LLM handoffs. "
                "All saved fields remain visible under Meta in the project tree."
            )
            guidance.setWordWrap(True)
            form.addRow(guidance)
            description = QLineEdit(str(current.get("description", "")))
            tags_value = current.get("tags", [])
            tags = QLineEdit(
                ", ".join(map(str, tags_value))
                if isinstance(tags_value, list)
                else str(tags_value)
            )
            instructions = QPlainTextEdit(str(current.get("instructions", "")))
            instructions.setMaximumHeight(90)
            standard_keys = {"description", "tags", "instructions"}
            additional = QPlainTextEdit(
                json.dumps(
                    {
                        key: value
                        for key, value in current.items()
                        if key not in standard_keys
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            additional.setMaximumHeight(130)
            additional.setToolTip(
                "Optional JSON object for any additional project-specific fields."
            )
            form.addRow("Description:", description)
            form.addRow("Tags:", tags)
            form.addRow("LLM instructions:", instructions)
            form.addRow("Additional fields (JSON):", additional)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                extra_metadata = json.loads(additional.toPlainText() or "{}")
                if not isinstance(extra_metadata, dict):
                    raise ValueError("Additional fields must be a JSON object.")
            except (json.JSONDecodeError, ValueError) as exc:
                QMessageBox.warning(self, "Invalid project meta", str(exc))
                return
            metadata = {
                **extra_metadata,
                "description": description.text().strip(),
                "tags": [tag.strip() for tag in tags.text().split(",") if tag.strip()],
                "instructions": instructions.toPlainText().strip(),
            }
        self._project.metadata = {
            str(key): value
            for key, value in dict(metadata).items()
            if value not in (None, "", [])
        }
        self._set_dirty(True)
        if self._project.project_file is not None:
            self._sync_project()
            try:
                write_project(self._project)
            except ProjectError as exc:
                QMessageBox.warning(self, "Could not save project meta", str(exc))
                return
            self._set_dirty(False)
        self._refresh_project_tree()
        self._set_status("Project meta saved and included in LLM handoffs.")

    # Compatibility for integrations using the former action name.
    def _on_case_details(self, _checked=False, *, metadata=None):
        self._on_meta(_checked, metadata=metadata)

    def _on_open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open DID project",
            self._settings.directory("projects"),
            f"DID projects (*{PROJECT_SUFFIX});;YAML files (*.yaml *.yml)",
        )
        if path:
            self._open_project_path(path)

    def _open_project_path(self, path):
        if not self._confirm_discard():
            return False
        try:
            project = read_project(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not open project", str(exc))
            return False
        saved_entity_config = project.entity_config_yaml
        if project.legacy and self.isVisible():
            answer = QMessageBox.question(
                self,
                "Convert legacy project",
                "This project uses the legacy standalone format. Convert it "
                "non-destructively into a project workdir?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                parent = QFileDialog.getExistingDirectory(
                    self,
                    "Choose parent directory for the converted workdir",
                    str(project.project_file.parent),
                )
                if not parent:
                    return False
                try:
                    convert_legacy_project(project, parent)
                except ProjectError as exc:
                    QMessageBox.warning(self, "Conversion failed", str(exc))
                    return False
        self._loading_project = True
        self._reset_session()
        self._project = project
        self._language = project.language
        self._detection_profile = project.detection_profile
        language_index = self.lang_combo.findData(project.language)
        self.lang_combo.setCurrentIndex(max(0, language_index))
        profile_index = self.profile_combo.findData(project.detection_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        output_index = self.output_combo.findData(project.preview_format)
        self.output_combo.setCurrentIndex(max(0, output_index))
        self.yaml_edit.setPlainText(project.entity_config_yaml)
        self._keys_saved_text = project.entity_config_yaml
        self._keys_origin = "saved_review" if project.entity_config_yaml else None
        self._loading_project = False
        if project.project_file not in self._settings.recent_projects():
            self._settings.add_recent_project(project.project_file)
        self._settings.set_directory("projects", project.project_file.parent)
        self._set_dirty(False)
        project.entity_config_yaml = saved_entity_config
        existing = [source for source in project.source_paths if source.exists()]
        missing = len(project.source_paths) - len(existing)
        self._update_keys_bar()
        if existing:
            self._load_paths(existing, collected=True)
        else:
            self._refresh_file_list()
            # Rebuilding a multi-project tree can change Qt's current item and
            # synchronously clear the draft pane.  The project being opened is
            # authoritative: restore its reviewed entities immediately even
            # when it currently has no source documents to extract.
            self.yaml_edit.blockSignals(True)
            self.yaml_edit.setPlainText(saved_entity_config)
            self.yaml_edit.blockSignals(False)
            self._refresh_entity_table()
            self._refresh_actions()
        if missing:
            self._set_status(f"Project opened with {missing} missing source file(s).")
        else:
            self._set_status(f"Opened project ‘{project.name}’.")
        return True

    def _on_save_project(self):
        self._save_project()

    def _on_save_project_as(self):
        self._save_project(save_as=True)

    def _save_project(self, *, save_as=False):
        if self._project is None:
            return False
        destination = self._project.project_file
        if save_as or destination is None:
            initial = self._settings.directory("projects")
            parent = QFileDialog.getExistingDirectory(
                self,
                "Choose parent directory for the project workdir",
                initial,
            )
            if not parent:
                return False
            try:
                saved = create_project_workdir(
                    self._project, parent, self._project.name
                )
            except ProjectError as exc:
                QMessageBox.warning(self, "Could not save project", str(exc))
                return False
            destination = saved
        self._sync_project()
        if self._project.name == "Untitled project":
            # Canonical workdirs all use the same filename; name after the folder.
            self._project.name = (
                destination.parent.name
                if destination.name == CANONICAL_PROJECT_FILENAME
                else destination.name.removesuffix(PROJECT_SUFFIX)
            )
        try:
            saved = write_project(self._project, destination)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not save project", str(exc))
            return False
        self._settings.add_recent_project(saved)
        self._settings.set_directory("projects", saved.parent)
        self._keys_saved_text = self._project.entity_config_yaml
        self._set_dirty(False)
        self._set_status(f"Project saved to {saved}.")
        self._create_pending_import_version()
        return True

    def _rebuild_recent_menu(self):
        self.recent_menu.clear()
        recent = self._settings.recent_projects()
        if not recent:
            empty = self.recent_menu.addAction("No recent projects")
            empty.setEnabled(False)
            return
        for path in recent:
            action = self.recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.setEnabled(path.exists())
            action.triggered.connect(
                lambda _checked=False, recent_path=path: self._open_project_path(
                    recent_path
                )
            )
        self.recent_menu.addSeparator()
        clear = self.recent_menu.addAction("Clear Recent Projects")
        clear.triggered.connect(self._settings.clear_recent_projects)

    # --------------------------------------------------------- qt overrides ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            project_files = [path for path in paths if path.endswith(PROJECT_SUFFIX)]
            if len(project_files) == 1 and len(paths) == 1:
                self._open_project_path(project_files[0])
            else:
                self._add_paths(paths)

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_W
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        if self._export_worker is not None:
            # Never abandon a half-written version; it finishes in seconds.
            self._set_status("Finishing the version being written…")
            self._export_worker.wait()
        detect_process.shutdown()
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        super().closeEvent(event)
