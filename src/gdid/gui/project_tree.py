"""Project & document tree panel: structure, sync badges, and rebuild.

MainWindow remains the orchestrator for navigation (open project, view version,
edit meta, remove documents) and context-menu actions that mutate project state.
This module owns the tree widget, item roles, sync-state helpers, and rebuild
logic driven by a snapshot of session state.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..project import (
    PROJECT_SUFFIX,
    ProjectError,
    list_versions,
    load_not_names,
)
from ..project import load_project as read_project

ROLE_PROJECT_KIND = Qt.ItemDataRole.UserRole + 20
ROLE_PROJECT_PATH = Qt.ItemDataRole.UserRole + 21
ROLE_VERSION_PATH = Qt.ItemDataRole.UserRole + 22
ROLE_DOCUMENT_PATH = Qt.ItemDataRole.UserRole + 23
ROLE_ACTIVE_PROJECT = Qt.ItemDataRole.UserRole + 24

TREE_STATE_STYLE = {
    "synced": ("✓", "#4caf50"),
    "out_of_sync": ("↻", "#f5a623"),
    "pending": ("○", "#9e9e9e"),
    "missing": ("!", "#ef5350"),
}

_MODE_COLORS = {
    "PSEUDO/FAKE": "#ab8cff",
    "PSEUDO": "#42a5f5",
    "RAW": "#ef5350",
    "PENDING": "#9e9e9e",
    "META": "#8e9aaf",
    "EXCLUDED": "#f5a623",
    "DO NOT PSEUDONYMIZE": "#f5a623",
}

_MODE_TOOLTIPS = {
    "PSEUDO/FAKE": "Pseudonymized output with local fake-value rendering support.",
    "PSEUDO": "Pseudonymized output.",
    "RAW": "Extracted source text; contains personal data.",
    "PENDING": "Not pseudonymized yet.",
    "META": "Project metadata included in LLM handoffs.",
    "EXCLUDED": "Reviewed detections excluded from pseudonymization.",
    "DO NOT PSEUDONYMIZE": "Persisted project exclusion; this text is retained unchanged.",
}


def tree_item_key(item):
    kind = item.data(0, ROLE_PROJECT_KIND)
    project_path = item.data(0, ROLE_PROJECT_PATH) or "unsaved"
    version_path = item.data(0, ROLE_VERSION_PATH) or ""
    document_path = item.data(0, ROLE_DOCUMENT_PATH) or ""
    return kind, project_path, version_path, document_path


def iter_tree_items(tree: QTreeWidget):
    def descend(item):
        yield item
        for child_index in range(item.childCount()):
            yield from descend(item.child(child_index))

    for index in range(tree.topLevelItemCount()):
        yield from descend(tree.topLevelItem(index))


def version_document_files(version_path):
    """Return user-facing version documents, excluding identity support files."""
    output_dir = Path(version_path) / "output"
    hidden = {"config.yaml", "shared_vars.typ", "shared_fakevars.typ"}
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name not in hidden
    )


def source_digest(path, cache: dict):
    """Hash a source once per size/mtime tuple for inexpensive sync checks."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in cache:
        digest = sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        cache[key] = digest.hexdigest()
    return cache[key]


def manifest_source_record(version, source):
    source = Path(source).expanduser().resolve()
    for record in version.manifest.get("sources", []):
        if not isinstance(record, dict) or not record.get("path"):
            continue
        try:
            recorded = Path(record["path"]).expanduser().resolve()
        except (OSError, TypeError):
            continue
        if recorded == source:
            return record
    return None


def source_sync_state(source, latest_version, cache: dict):
    source = Path(source)
    if not source.exists():
        return "missing"
    if latest_version is None:
        return "pending"
    record = manifest_source_record(latest_version, source)
    digest = source_digest(source, cache)
    if record is None or not digest:
        return "out_of_sync"
    return "synced" if record.get("sha256") == digest else "out_of_sync"


def draft_sync_state(project, latest_version, cache: dict):
    if latest_version is None:
        return "pending"
    source_states = [
        source_sync_state(source, latest_version, cache)
        for source in project.source_paths
    ]
    if "missing" in source_states or "out_of_sync" in source_states:
        return "out_of_sync"
    manifest = latest_version.manifest
    if len(manifest.get("sources", [])) != len(project.source_paths):
        return "out_of_sync"
    entity_record = next(
        (
            record
            for record in manifest.get("files", [])
            if isinstance(record, dict) and record.get("path") == "entities.yaml"
        ),
        None,
    )
    entity_digest = sha256(project.entity_config_yaml.encode("utf-8")).hexdigest()
    if entity_record is None or entity_record.get("sha256") != entity_digest:
        return "out_of_sync"
    processing = manifest.get("processing", {})
    export = manifest.get("export", {})
    if (
        processing.get("language") != project.language
        or processing.get("detection_profile") != project.detection_profile
        or export.get("format") != project.export_format
        or export.get("mode") != project.export_mode
        or manifest.get("metadata", {}) != project.metadata
    ):
        return "out_of_sync"
    return "synced"


def set_tree_state(item, state, *, tooltip=None):
    symbol, color = TREE_STATE_STYLE[state]
    item.setText(0, f"{symbol} {item.text(0)}")
    item.setForeground(0, QBrush(QColor(color)))
    if tooltip:
        item.setToolTip(0, tooltip)


def set_tree_mode(item, mode):
    item.setText(1, mode)
    item.setForeground(1, QBrush(QColor(_MODE_COLORS.get(mode, "#9e9e9e"))))
    item.setToolTip(1, _MODE_TOOLTIPS.get(mode, mode))


def select_document_item(tree: QTreeWidget, path: Path) -> bool:
    """Select the tree item for *path*. Returns True if found."""
    path = Path(path)
    for item in iter_tree_items(tree):
        value = item.data(0, ROLE_DOCUMENT_PATH)
        if value and Path(value) == path:
            tree.blockSignals(True)
            tree.setCurrentItem(item)
            parent = item.parent()
            if parent is not None:
                parent.setExpanded(True)
            tree.blockSignals(False)
            return True
    return False


class ProjectTreePanel(QWidget):
    """Sidebar: New/Open actions and the projects & documents tree."""

    newProjectClicked = Signal()
    openProjectClicked = Signal()
    contextMenuRequested = Signal(object)  # QPoint in tree viewport coords
    itemPressed = Signal(object, int)
    itemClicked = Signal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 105)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(290)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.contextMenuRequested.emit)
        self.tree.itemPressed.connect(self.itemPressed.emit)
        self.tree.itemClicked.connect(self.itemClicked.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>Projects &amp; documents</b>"))
        actions = QHBoxLayout()
        self.new_button = QPushButton("New")
        self.new_button.clicked.connect(self.newProjectClicked.emit)
        actions.addWidget(self.new_button)
        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(self.openProjectClicked.emit)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)
        layout.addWidget(self.tree)
        self.setMinimumWidth(300)
        self.setMaximumWidth(460)

    def items(self):
        yield from iter_tree_items(self.tree)

    def refresh(
        self,
        *,
        active_project,
        recent_project_paths,
        selected_file,
        viewing_version,
        draft_view_state,
        anonymized,
        extracted,
        source_digest_cache,
        remove_missing_recent=None,
    ):
        """Rebuild the logical project tree without reading source contents."""
        tree = self.tree
        expanded = {tree_item_key(item) for item in self.items() if item.isExpanded()}
        scroll_value = tree.verticalScrollBar().value()
        tree.blockSignals(True)
        tree.clear()
        active_path = (
            active_project.project_file.resolve()
            if active_project is not None and active_project.project_file is not None
            else None
        )

        recent_entries = []
        for path in recent_project_paths:
            resolved = path.expanduser().resolve()
            if not resolved.exists():
                if remove_missing_recent is not None:
                    remove_missing_recent(resolved)
                continue
            label = resolved.name.removesuffix(PROJECT_SUFFIX)
            try:
                project = read_project(resolved)
                label = project.name
            except ProjectError:
                project = None
            recent_entries.append((resolved, label, project))
        label_counts = {}
        for _path, label, _project in recent_entries:
            label_counts[label] = label_counts.get(label, 0) + 1

        projects = []
        if active_project is not None and active_path is None:
            projects.append((None, active_project.name, active_project, True))
        active_added = active_path is None and active_project is not None
        for resolved, label, loaded_project in recent_entries:
            if active_path is not None and resolved == active_path:
                projects.append((resolved, label, active_project, True))
                active_added = True
                continue
            display_label = label
            if label_counts[label] > 1:
                display_label = f"{label} — {resolved.parent.name}"
            projects.append((resolved, display_label, loaded_project, False))
        if active_project is not None and not active_added:
            projects.append((active_path, active_project.name, active_project, True))

        desired_item = None
        active_container = None
        for path, label, project, is_active in projects:
            project_item = QTreeWidgetItem([label, ""])
            project_item.setData(0, ROLE_PROJECT_KIND, "project")
            project_item.setData(0, ROLE_PROJECT_PATH, str(path or ""))
            project_item.setData(0, ROLE_ACTIVE_PROJECT, is_active)
            location = str(path) if path is not None else "Unsaved project"
            tree.addTopLevelItem(project_item)
            if project is None:
                continue

            versions = list_versions(project)
            latest_version = versions[-1] if versions else None
            draft_state = draft_sync_state(project, latest_version, source_digest_cache)
            state_text = (
                "No published version"
                if latest_version is None
                else f"Current with {latest_version.version_id}"
                if draft_state == "synced"
                else f"Draft differs from {latest_version.version_id}"
            )
            set_tree_state(
                project_item, draft_state, tooltip=f"{state_text} · {location}"
            )
            if is_active:
                font = project_item.font(0)
                font.setBold(True)
                project_item.setFont(0, font)

            meta = QTreeWidgetItem(["Meta", ""])
            meta.setData(0, ROLE_PROJECT_KIND, "meta")
            meta.setData(0, ROLE_PROJECT_PATH, str(path or ""))
            meta.setToolTip(0, "Project metadata included in LLM handoffs")
            set_tree_mode(meta, "META")
            if project.metadata:
                for key, value in project.metadata.items():
                    rendered = (
                        ", ".join(map(str, value))
                        if isinstance(value, list)
                        else str(value)
                    )
                    summary = rendered.replace("\n", " ")
                    if len(summary) > 72:
                        summary = summary[:69].rstrip() + "…"
                    entry = QTreeWidgetItem(
                        [f"{str(key).replace('_', ' ').title()}: {summary}", ""]
                    )
                    entry.setData(0, ROLE_PROJECT_KIND, "meta_entry")
                    entry.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                    entry.setToolTip(0, rendered)
                    meta.addChild(entry)
            else:
                empty_meta = QTreeWidgetItem(["Add project meta…", ""])
                empty_meta.setData(0, ROLE_PROJECT_KIND, "meta_entry")
                empty_meta.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                meta.addChild(empty_meta)

            if is_active and viewing_version is not None and draft_view_state:
                draft_anonymized = draft_view_state["anonymized"]
                draft_extracted = draft_view_state["extracted"]
            elif is_active:
                draft_anonymized = anonymized
                draft_extracted = extracted
            else:
                draft_anonymized = {}
                draft_extracted = {}

            draft = QTreeWidgetItem(["Draft", ""])
            draft.setData(0, ROLE_PROJECT_KIND, "draft")
            draft.setData(0, ROLE_PROJECT_PATH, str(path or ""))
            set_tree_state(draft, draft_state, tooltip=state_text)
            draft_mode = (
                "PSEUDO"
                if draft_anonymized or draft_state == "synced"
                else "RAW"
                if draft_extracted
                else "PENDING"
            )
            set_tree_mode(draft, draft_mode)
            project_item.addChild(draft)
            for source in project.source_paths:
                source = Path(source)
                exists = source.exists()
                sync_state = source_sync_state(
                    source, latest_version, source_digest_cache
                )
                if source in draft_anonymized:
                    mode, state = "PSEUDO", "Pseudonymized draft"
                elif source in draft_extracted:
                    mode, state = "RAW", "Raw text ready"
                elif sync_state == "synced":
                    mode, state = "PSEUDO", "Matches latest version"
                elif not exists:
                    mode, state = "PENDING", "Missing source"
                else:
                    mode, state = (
                        "PENDING",
                        "Not loaded" if not is_active else "Waiting",
                    )
                document = QTreeWidgetItem([source.name, ""])
                document.setData(0, ROLE_PROJECT_KIND, "draft_document")
                document.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                document.setData(0, ROLE_DOCUMENT_PATH, str(source))
                set_tree_state(document, sync_state, tooltip=f"{state} · {source}")
                set_tree_mode(document, mode)
                if not exists:
                    document.setDisabled(True)
                draft.addChild(document)
                if is_active and viewing_version is None and source == selected_file:
                    desired_item = document
            try:
                exclusions = load_not_names(project) if project.project_file else []
            except ProjectError:
                exclusions = []
            if exclusions:
                excluded_group = QTreeWidgetItem(
                    [f"Excluded detections ({len(exclusions)})", ""]
                )
                excluded_group.setData(0, ROLE_PROJECT_KIND, "excluded_group")
                excluded_group.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                excluded_group.setForeground(0, QBrush(QColor("#f5a623")))
                excluded_group.setToolTip(
                    0, "Reviewed items stored in draft/not_names.json"
                )
                set_tree_mode(excluded_group, "EXCLUDED")
                draft.addChild(excluded_group)
                for exclusion in exclusions:
                    excluded = QTreeWidgetItem([f"⊘ {exclusion}", ""])
                    excluded.setData(0, ROLE_PROJECT_KIND, "excluded_detection")
                    excluded.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                    excluded.setForeground(0, QBrush(QColor("#f5a623")))
                    excluded.setToolTip(
                        0,
                        "Marked ‘Do not pseudonymize’; retained in the project audit trail.",
                    )
                    set_tree_mode(excluded, "DO NOT PSEUDONYMIZE")
                    excluded_group.addChild(excluded)
            for version in versions:
                version_label = f"v{version.number:03d}"
                if version.label:
                    version_label += f" · {version.label}"
                version_item = QTreeWidgetItem([version_label, ""])
                version_item.setData(0, ROLE_PROJECT_KIND, "version")
                version_item.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                version_item.setData(0, ROLE_VERSION_PATH, str(version.path))
                set_tree_state(
                    version_item,
                    "synced",
                    tooltip=f"Completed immutable version · {version.created_at}",
                )
                has_fake_values = (
                    version.path / "output" / "shared_fakevars.typ"
                ).exists()
                version_mode = "PSEUDO/FAKE" if has_fake_values else "PSEUDO"
                set_tree_mode(version_item, version_mode)
                project_item.addChild(version_item)
                output_dir = version.path / "output"
                for output in version_document_files(version.path):
                    document = QTreeWidgetItem(
                        [output.relative_to(output_dir).as_posix(), ""]
                    )
                    document.setData(0, ROLE_PROJECT_KIND, "version_document")
                    document.setData(0, ROLE_PROJECT_PATH, str(path or ""))
                    document.setData(0, ROLE_VERSION_PATH, str(version.path))
                    document.setData(0, ROLE_DOCUMENT_PATH, str(output))
                    set_tree_state(
                        document,
                        "synced",
                        tooltip=f"Completed read-only output · {output}",
                    )
                    set_tree_mode(document, version_mode)
                    version_item.addChild(document)
                    if (
                        is_active
                        and viewing_version == version.path
                        and output == selected_file
                    ):
                        desired_item = document
                if is_active and viewing_version == version.path:
                    active_container = version_item
            project_item.addChild(meta)
            if is_active and project.metadata:
                meta.setExpanded(True)
            if is_active:
                active_container = active_container or draft
                project_item.setExpanded(True)
                active_container.setExpanded(True)
                if desired_item is None:
                    desired_item = active_container

        if tree.topLevelItemCount() == 0:
            empty = QTreeWidgetItem(["No saved projects"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setToolTip(0, "Create a project or open an existing project file.")
            tree.addTopLevelItem(empty)

        for item in self.items():
            if tree_item_key(item) in expanded:
                item.setExpanded(True)
        if desired_item is not None:
            tree.setCurrentItem(desired_item)
        tree.blockSignals(False)
        tree.verticalScrollBar().setValue(scroll_value)

    def update_active_badge(self, project, name: str, source_digest_cache: dict):
        """Refresh the active top-level project's sync badge without a full rebuild."""
        if project is None:
            return False
        expected_path = (
            str(project.project_file.resolve())
            if project.project_file is not None
            else ""
        )
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if (
                item.data(0, ROLE_ACTIVE_PROJECT)
                and item.data(0, ROLE_PROJECT_PATH) == expected_path
            ):
                versions = list_versions(project)
                latest = versions[-1] if versions else None
                state = draft_sync_state(project, latest, source_digest_cache)
                state_text = (
                    f"Current with {latest.version_id}"
                    if state == "synced"
                    else f"Draft differs from {latest.version_id}"
                    if latest is not None
                    else "No published version"
                )
                item.setText(0, name)
                set_tree_state(
                    item,
                    state,
                    tooltip=f"{state_text} · {expected_path or 'Unsaved project'}",
                )
                return True
        return False
