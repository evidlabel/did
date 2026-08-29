"""Streamlined single-flow main window for gdid.

Open (or drag-drop) documents → text is extracted in the background → one
**Anonymize** action detects entities and pseudonymizes off-thread → the YAML
config is editable and re-applies live → **Save** writes Typst output. All real
work is delegated to :mod:`gdid.pipeline` via the workers in :mod:`gdid.gui.workers`.
"""

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

from .. import pipeline
from ..project import (
    CANONICAL_PROJECT_FILENAME,
    PROJECT_SUFFIX,
    Project,
    ProjectError,
    abort_version,
    begin_version,
    convert_legacy_project,
    create_project_workdir,
    delete_project_workdir,
    finalize_version,
    list_versions,
    load_not_names,
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
    ROLE_VARIANT,
    ROLE_VARIANTS,
    EntityPanel,
)
from .preview import PreviewPanel, token_at_position
from .project_tree import (
    ROLE_ACTIVE_PROJECT,
    ROLE_DOCUMENT_PATH,
    ROLE_PROJECT_KIND,
    ROLE_PROJECT_PATH,
    ROLE_VERSION_PATH,
    ProjectTreePanel,
    version_document_files,
)
from .settings import AppSettings
from .workers import AnonymizeWorker, ExtractWorker, PseudoWorker

# Re-export under prior private names so existing window code and tests keep
# working without a noisy rename across the project-tree helpers.
_ROLE_ENTITY_ID = ROLE_ENTITY_ID
_ROLE_VARIANT = ROLE_VARIANT
_ROLE_ENTITY_TYPE = ROLE_ENTITY_TYPE
_ROLE_VARIANTS = ROLE_VARIANTS
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


class MainWindow(QMainWindow):
    """Main gdid window."""

    def __init__(self, *, anonymizer_factory=Anonymizer, settings=None):
        super().__init__()
        self._factory = anonymizer_factory
        self._settings = settings or AppSettings()
        self._project: Project | None = None
        self._dirty = False
        self._loading_project = False
        self._apply_saved_config = False
        self._viewing_version = None
        self._draft_view_state = None
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
        self._extracted: dict[Path, str] = {}
        self._anonymized: dict[Path, str] = {}
        self._anonymizer = None
        self._yaml_text = ""
        self._temp_dirs: list[Path] = []
        self._workers: list = []
        self._language = "da"
        self._detection_profile = "thorough"
        self._output_mode = "typst"
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
        self.anon_action.triggered.connect(self._on_anonymize)
        tb.addAction(self.anon_action)

        self.save_button = QToolButton(self)
        self.save_button.setText("Create version")
        self.save_button.setToolTip(
            "Create an immutable pseudonymized version in this project’s workdir."
        )
        self.save_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        save_menu = QMenu(self.save_button)
        self.save_multi_action = save_menu.addAction("Multi (separate files)")
        self.save_multi_action.triggered.connect(lambda: self._on_save("multi"))
        self.save_single_action = save_menu.addAction("Single (combined)")
        self.save_single_action.triggered.connect(lambda: self._on_save("single"))
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
        tabs = QTabWidget()
        tabs.addTab(self.entity_panel, "Entity review")
        tabs.addTab(self.yaml_edit, "Advanced YAML")
        entities_box = self._titled("Detected entities", tabs)
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
        self.anon_action.setEnabled(bool(self._extracted))
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
            extracted=self._extracted,
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
            if kind in {"version", "version_document"}:
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
        if item is None:
            return
        menu = QMenu(self)
        kind = item.data(0, ROLE_PROJECT_KIND)
        document_path = item.data(0, ROLE_DOCUMENT_PATH)
        if kind in {"draft_document", "version_document"}:
            f = Path(document_path) if document_path else None
            menu = self._copy_menu(f)
            if kind == "draft_document":
                menu.addSeparator()
                remove = menu.addAction("Remove document from project")
                project_path = item.data(0, ROLE_PROJECT_PATH)
                active_path = (
                    str(self._project.project_file.resolve())
                    if self._project is not None
                    and self._project.project_file is not None
                    else ""
                )
                remove.setEnabled(project_path == active_path)
                remove.triggered.connect(
                    lambda: self._remove_tree_document(project_path, f)
                )
            menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            return
        if kind == "version":
            path = Path(item.data(0, ROLE_VERSION_PATH))
            reveal = menu.addAction("Reveal version folder")
            reveal.triggered.connect(lambda: self._reveal_path(path))
            copy = menu.addAction("Copy version to…")
            copy.triggered.connect(lambda: self._copy_version(path))
            menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            return
        if kind == "draft":
            project_path = item.data(0, ROLE_PROJECT_PATH)
            add_documents = menu.addAction("Add documents…")
            add_documents.triggered.connect(
                lambda: self._add_documents_to_tree_project(project_path)
            )
            menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            return
        if kind in {"meta", "meta_entry"}:
            project_path = item.data(0, ROLE_PROJECT_PATH)
            edit_meta = menu.addAction("Edit project meta…")
            edit_meta.triggered.connect(
                lambda: self._edit_tree_project_meta(project_path)
            )
            menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            return
        if kind == "version_document":
            return
        if item.data(0, ROLE_ACTIVE_PROJECT):
            project_path = item.data(0, ROLE_PROJECT_PATH)
            add_documents = menu.addAction("Add documents…")
            add_documents.triggered.connect(
                lambda: self._add_documents_to_tree_project(project_path)
            )
            menu.addSeparator()
            rename = menu.addAction("Rename project…")
            rename.triggered.connect(self._on_rename_project)
            menu.addSeparator()
            delete = menu.addAction("Delete project…")
            delete.triggered.connect(lambda: self._delete_project(project_path))
            menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            return
        path = item.data(0, ROLE_PROJECT_PATH)
        if not path:
            return
        add_documents = menu.addAction("Add documents…")
        add_documents.triggered.connect(
            lambda: self._add_documents_to_tree_project(path)
        )
        menu.addSeparator()
        remove = menu.addAction("Remove from recent projects")
        remove.triggered.connect(lambda: self._remove_recent_project(path))
        delete = menu.addAction("Delete project…")
        delete.triggered.connect(lambda: self._delete_project(path))
        menu.exec(self.project_tree.viewport().mapToGlobal(pos))

    def _remove_recent_project(self, path):
        self._settings.remove_recent_project(path)
        self._refresh_project_list()

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
                "Use ‘Remove from recent projects’ for legacy project files.",
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
            self, "Copy version to directory", str(Path.home())
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

    def _ensure_project(self):
        if self._project is None:
            self._project = Project()
            self._set_dirty(True)
        return self._project

    def _sync_project(self):
        if self._project is None:
            return
        self._project.source_paths = list(self._files)
        self._project.language = self._language
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
        item = self.entity_table.item(row, 2)
        if item is None:
            return
        entity_type = item.data(_ROLE_ENTITY_TYPE)
        entity_id = item.data(_ROLE_ENTITY_ID)
        if not entity_type or not entity_id:
            return
        if variant is None:
            variant, accepted = QInputDialog.getText(
                self,
                "Add identity variant",
                f"Variant for {entity_type} · {entity_id}:",
            )
            if not accepted:
                return
        variant = str(variant).strip()
        if not variant:
            return
        try:
            changed_yaml = pipeline.add_entity_variant(
                self.yaml_edit.toPlainText(), entity_type, entity_id, variant
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not add variant", str(exc))
            return
        self.yaml_edit.setPlainText(changed_yaml)
        project = self._ensure_project()
        project.entity_config_yaml = changed_yaml
        if project.project_file is not None:
            self._sync_project()
            try:
                write_project(project)
            except ProjectError as exc:
                QMessageBox.warning(
                    self, "Variant added but draft could not be saved", str(exc)
                )
                return
        self._set_status(f"Added variant “{variant}” to {entity_id} · draft saved.")

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
        self.yaml_edit.setPlainText(changed_yaml)
        project = self._ensure_project()
        project.entity_config_yaml = changed_yaml
        if project.project_file is not None:
            self._sync_project()
            try:
                write_project(project)
            except ProjectError as exc:
                QMessageBox.warning(
                    self, "Entity changed but draft could not be saved", str(exc)
                )
                return
        count = len(entity_ids)
        self._set_status(
            f"Changed {count} identit{'y' if count == 1 else 'ies'} "
            f"to {target_type} · draft saved."
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
        self._project.entity_config_yaml = filtered_yaml
        try:
            write_project(self._project)
        except ProjectError as exc:
            QMessageBox.warning(
                self,
                "Exclusions saved but draft could not be updated",
                str(exc),
            )
            self._set_dirty(True)
            return
        self._set_dirty(False)
        self._refresh_project_tree()
        identity_count = len(identity_ids)
        self._set_status(
            f"Marked {identity_count} identit{'y' if identity_count == 1 else 'ies'} "
            f"({len(variants)} variant(s)) as ‘Do not pseudonymize’ · saved to {path.name}."
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
        self.yaml_edit.setPlainText(merged_yaml)
        moved = f"variant “{variant}”" if variant else f"identity {source_id}"
        self._set_status(f"Merged {moved} into {target_id}.")

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
        self._files = files
        if self._selected_file not in files:
            self._selected_file = files[0]
        self._extracted = {}
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
        self._extracted.pop(selected, None)
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
        self._extracted = extracted
        self._set_busy(False)
        self._set_status(
            f"{len(self._extracted)} document(s) extracted — detecting entities…"
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

    def _on_anonymize(self):
        if not self._extracted:
            return
        self._advance_session_generation()
        self._set_busy(True, "Detecting entities…")
        worker = AnonymizeWorker(
            self._extracted,
            self._language,
            detection_profile=self._detection_profile,
            anonymizer_factory=self._factory,
            not_names=self._current_not_names(),
        )
        self._connect_worker(worker, self._on_anonymized)
        self._track(worker)
        worker.start()

    def _on_anonymized(self, anonymizer, yaml_str, anonymized, report=None):
        effective_yaml = yaml_str
        reapply = False
        if self._apply_saved_config and self._project is not None:
            effective_yaml = self._project.entity_config_yaml
            reapply = True
        self._apply_saved_config = False
        if self._project is not None and self._project.project_file is not None:
            try:
                exclusions = load_not_names(self._project)
                filtered_yaml = pipeline.exclude_not_names(effective_yaml, exclusions)
            except (ProjectError, ValueError) as exc:
                self._on_worker_error(str(exc))
                return
            reapply = reapply or filtered_yaml != effective_yaml
            effective_yaml = filtered_yaml
        if reapply:
            self._anonymizer = anonymizer
            self._yaml_text = effective_yaml
            self.yaml_edit.blockSignals(True)
            self.yaml_edit.setPlainText(effective_yaml)
            self.yaml_edit.blockSignals(False)
            self._refresh_entity_table()
            if self._project is not None:
                self._project.entity_config_yaml = effective_yaml
            worker = PseudoWorker(
                anonymizer,
                effective_yaml,
                self._extracted,
                not_names=self._current_not_names(),
            )
            self._connect_worker(worker, self._on_reapplied)
            self._track(worker)
            worker.start()
            return
        self._anonymizer = anonymizer
        self._yaml_text = yaml_str
        self._anonymized = anonymized
        self._set_verification(report)
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.setPlainText(yaml_str)
        self.yaml_edit.blockSignals(False)
        self._set_busy(False)
        n_entities = yaml_str.count("- id:") if yaml_str else 0
        self._set_status(
            self._verification_status(
                f"{len(self._files)} document(s) · ~{n_entities} entities · "
                "ready to save."
            )
        )
        self._refresh_file_list()
        self._refresh_actions()
        self._show_current_preview()
        self._set_preview_state("PSEUDONYMIZED")
        self._refresh_entity_table()
        if self._project is not None:
            self._project.entity_config_yaml = yaml_str
            self._set_dirty(True)
        self._create_pending_import_version()
        self._finish_pending_tree_navigation()

    def _on_yaml_changed(self):
        valid = self._refresh_entity_table()
        if not self._loading_project and self._project is not None:
            self._project.entity_config_yaml = self.yaml_edit.toPlainText()
            self._set_dirty(True)
        # Live re-apply only makes sense once we have an anonymizer.
        if self._anonymizer is not None and valid:
            self._reapply_timer.start()

    def _reapply_config(self):
        if self._anonymizer is None or not self._extracted:
            return
        self._advance_session_generation()
        yaml_text = self.yaml_edit.toPlainText()
        worker = PseudoWorker(
            self._anonymizer,
            yaml_text,
            self._extracted,
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
        self._set_status(self._verification_status("Config re-applied."))
        self._create_pending_import_version()
        self._finish_pending_tree_navigation()

    def _create_pending_import_version(self):
        """Snapshot a successfully anonymized document import as a new version."""
        if not self._auto_version_pending:
            return
        if (
            self._project is None
            or self._project.project_file is None
            or not self._anonymized
        ):
            return
        self._auto_version_pending = False
        mode = self._project.export_mode or "multi"
        self._project.export_destination = self._project.workdir / "versions"
        self._sync_project()
        stage = None
        try:
            write_project(self._project)
            stage = begin_version(self._project)
            files = [path for path in self._files if path in self._anonymized]
            pipeline.save_version_outputs(
                files,
                self._anonymizer,
                self.yaml_edit.toPlainText(),
                mode,
                stage.path,
            )
            version = finalize_version(
                self._project,
                stage,
                processing={
                    "language": self._language,
                    "detection_profile": self._detection_profile,
                    "trigger": "document_import",
                    "verification": self._verification_record(),
                },
                export={
                    "format": self._project.export_format,
                    "mode": mode,
                },
                app_version=__version__,
            )
        except Exception as exc:
            if stage is not None:
                abort_version(stage)
            QMessageBox.warning(
                self,
                "Automatic version failed",
                f"The draft was anonymized, but its version could not be created: {exc}",
            )
            return
        self._set_dirty(False)
        self._refresh_project_list()
        self._set_status(f"Anonymized import saved as immutable {version.version_id}.")

    # ------------------------------------------------------------ clipboard ---
    def _current_file(self):
        return self._selected_file if self._selected_file in self._files else None

    def _copy_menu(self, f):
        """Build the copy context menu for document ``f`` (may be None).

        Entries are enabled only for pseudonymized documents so raw text can
        never leak to the clipboard.
        """
        menu = QMenu(self)
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
                entity_data = pipeline.parse_yaml(self.yaml_edit.toPlainText())
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
        """Apply and persist a review edit made from the document preview."""
        self.yaml_edit.setPlainText(changed_yaml)
        project = self._ensure_project()
        project.entity_config_yaml = changed_yaml
        if project.project_file is not None:
            self._sync_project()
            try:
                write_project(project)
            except ProjectError as exc:
                QMessageBox.warning(
                    self, "Identity changed but draft could not be saved", str(exc)
                )
                return
        self._set_status(f"{status} Draft saved.")

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
        entity_type, possible_ids = matched
        return self.entity_panel.select_entity(entity_type, possible_ids)

    def _show_current_preview(self):
        f = self._current_file()
        if f is None:
            return
        text = self._display_text(f)
        if text is not None:
            self.preview.setPlainText(text)
            state = {
                "redacted": "REDACTED",
                "synthetic": "SYNTHETIC",
            }.get(self._output_mode, "PSEUDONYMIZED")
            self._set_preview_state(state)
        elif f in self._extracted:
            self.preview.setPlainText(self._extracted[f])
            self._set_preview_state("RAW")

    def _on_save(self, mode):
        if not self._anonymized:
            return
        if self._project is None or (
            self._project.project_file is None and not self._save_project()
        ):
            return
        label = ""
        if self.isVisible():
            label, accepted = QInputDialog.getText(
                self,
                "Export anonymization version",
                "Optional version label:",
            )
            if not accepted:
                return
            next_number = (
                max(
                    (version.number for version in list_versions(self._project)),
                    default=0,
                )
                + 1
            )
            config = pipeline.parse_yaml(self.yaml_edit.toPlainText())
            identity_count = sum(
                len(entities)
                for entities in config.values()
                if isinstance(entities, list)
            )
            summary = (
                f"Create immutable v{next_number:03d}"
                + (f" · {label.strip()}" if label.strip() else "")
                + f"\n\nDocuments: {len(self._files)}"
                + f"\nDetected identities: {identity_count}"
                + f"\nWorkdir: {self._project.workdir}"
                + "\n\nThe version snapshot and shared variable files may contain "
                "original identifiers."
            )
            confirmed = QMessageBox.question(
                self,
                "Confirm version export",
                summary,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if confirmed != QMessageBox.StandardButton.Ok:
                return
        if not self._confirm_verification("Create version"):
            return
        self._project.export_mode = mode
        self._project.export_destination = self._project.workdir / "versions"
        self._sync_project()
        try:
            write_project(self._project)
            stage = begin_version(self._project, label)
        except ProjectError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        files = [f for f in self._files if f in self._anonymized]
        try:
            pipeline.save_version_outputs(
                files,
                self._anonymizer,
                self.yaml_edit.toPlainText(),
                mode,
                stage.path,
            )
            version = finalize_version(
                self._project,
                stage,
                processing={
                    "language": self._language,
                    "detection_profile": self._detection_profile,
                    "verification": self._verification_record(),
                },
                export={
                    "format": self._project.export_format,
                    "mode": mode,
                },
                app_version=__version__,
            )
        except Exception as exc:
            abort_version(stage)
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self._set_dirty(False)
        self._refresh_project_list()
        self._set_status(
            f"Created immutable {version.version_id} with {len(files)} document(s)."
        )

    def _on_language(self, _index):
        lang = self.lang_combo.currentData()
        if not lang:
            return
        if lang == self._language:
            return
        self._language = lang
        if self._project is not None:
            self._project.language = lang
            self._set_dirty(True)
        if self._anonymized or self._anonymizer is not None:
            self._anonymized = {}
            self._anonymizer = None
            self.yaml_edit.blockSignals(True)
            self.yaml_edit.clear()
            self.yaml_edit.blockSignals(False)
            if self._project is not None:
                self._project.entity_config_yaml = ""
            self._set_status("Language changed — run Anonymize again.")
            self._refresh_file_list()
            self._refresh_actions()

    def _on_detection_profile(self, _index):
        profile = self.profile_combo.currentData()
        if not profile or profile == self._detection_profile:
            return
        self._detection_profile = profile
        if self._project is not None:
            self._project.detection_profile = profile
            self._set_dirty(True)
        if self._anonymized or self._anonymizer is not None:
            self._anonymized = {}
            self._anonymizer = None
            self._set_status("Detection profile changed — run Anonymize again.")
            self._refresh_file_list()
            self._refresh_actions()

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
        self._set_busy(False)
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
        self._selected_file = None
        self._files = []
        self._extracted = {}
        self._anonymized = {}
        self._anonymizer = None
        self._yaml_text = ""
        self._auto_version_pending = False
        self.yaml_edit.blockSignals(True)
        self.yaml_edit.clear()
        self.yaml_edit.blockSignals(False)
        self.preview.clear()
        self._set_preview_state("EMPTY")
        self._refresh_entity_table()
        self._refresh_file_list()
        self._refresh_actions()

    def _view_version(self, version_path):
        if self._project is None:
            return
        if self._viewing_version is not None:
            self._return_to_draft()
        self._draft_view_state = {
            "files": self._files,
            "extracted": self._extracted,
            "anonymized": self._anonymized,
            "yaml": self.yaml_edit.toPlainText(),
            "selected": self._current_file(),
        }
        files = version_document_files(version_path)
        self._files = files
        self._extracted = {}
        self._anonymized = {}
        for path in files:
            try:
                self._anonymized[path] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        snapshot = version_path / "entities.yaml"
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
        self._set_status(f"Viewing {version_path.name} · read-only.")

    def _return_to_draft(self):
        if self._viewing_version is None or self._draft_view_state is None:
            return
        state = self._draft_view_state
        self._viewing_version = None
        self._draft_view_state = None
        self._files = state["files"]
        self._extracted = state["extracted"]
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
        self._set_status("Viewing current draft.")

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
        self._project = Project(name=name)
        if parent is not None:
            try:
                create_project_workdir(self._project, parent, name)
            except ProjectError as exc:
                QMessageBox.warning(self, "Could not create project", str(exc))
                self._project = None
                return
            self._settings.add_recent_project(self._project.project_file)
            self._settings.set_directory("projects", parent)
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
        self._loading_project = False
        if project.project_file not in self._settings.recent_projects():
            self._settings.add_recent_project(project.project_file)
        self._settings.set_directory("projects", project.project_file.parent)
        self._set_dirty(False)
        project.entity_config_yaml = saved_entity_config
        existing = [source for source in project.source_paths if source.exists()]
        missing = len(project.source_paths) - len(existing)
        self._apply_saved_config = bool(saved_entity_config and existing)
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
            self._project.name = destination.name.removesuffix(PROJECT_SUFFIX)
        try:
            saved = write_project(self._project, destination)
        except ProjectError as exc:
            QMessageBox.warning(self, "Could not save project", str(exc))
            return False
        self._settings.add_recent_project(saved)
        self._settings.set_directory("projects", saved.parent)
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
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        super().closeEvent(event)
