"""Document path helpers for the GUI (pick files, collect additions, tree select).

MainWindow stays responsible for extract/anonymize and project lifecycle after
paths are chosen. This module owns file-dialog filters and pure path collection.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QTreeWidget

from .. import pipeline
from .project_tree import ROLE_DOCUMENT_PATH, ROLE_PROJECT_PATH, select_document_item

DOCUMENT_FILE_FILTER = "Documents (*.pdf *.docx *.md *.txt *.zip);;All files (*)"
NO_DOCUMENTS_STATUS = "No supported documents found (.pdf, .docx, .md, .txt)."
NO_NEW_DOCUMENTS_STATUS = "No new supported documents found (.pdf, .docx, .md, .txt)."


def choose_document_paths(parent, start_directory: str = "") -> list[Path]:
    """Show the multi-file document picker; return selected paths (possibly empty)."""
    paths, _ = QFileDialog.getOpenFileNames(
        parent,
        "Add documents",
        start_directory or "",
        DOCUMENT_FILE_FILTER,
    )
    return [Path(path) for path in paths]


def collect_document_additions(
    paths, existing_files
) -> tuple[list[Path], list[Path], list[Path]]:
    """Expand *paths* and return ``(additions, all_files, temp_dirs)``.

    *additions* are newly discovered inputs not already in *existing_files*.
    *all_files* is the expanded list from *paths* only (not merged with existing).
    """
    files, temp_dirs = pipeline.collect_inputs(paths)
    existing = {Path(path) for path in existing_files}
    additions = [path for path in files if path not in existing]
    return additions, files, temp_dirs


def resolve_document_paths(
    paths, *, collected: bool = False
) -> tuple[list[Path], list[Path]]:
    """Return ``(files, temp_dirs)`` for load; *collected* skips re-expansion."""
    if collected:
        return [Path(path) for path in paths], []
    return pipeline.collect_inputs(paths)


def saved_project_paths_from_tree(tree: QTreeWidget) -> list[str]:
    """Return non-empty project paths from top-level tree items."""
    paths = []
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        project_path = item.data(0, ROLE_PROJECT_PATH)
        if project_path:
            paths.append(project_path)
    return paths


def prepare_tree_project_for_documents(
    *,
    has_active_project: bool,
    tree: QTreeWidget,
    open_project_path,
    set_status,
) -> bool:
    """Activate the selected saved project before showing the source picker.

    Returns False when the user must select a project first (or open fails).
    """
    if has_active_project:
        return True
    if not saved_project_paths_from_tree(tree):
        return True
    selected = tree.currentItem()
    project_path = selected.data(0, ROLE_PROJECT_PATH) if selected is not None else None
    if not project_path:
        set_status("Select a project in the tree before adding documents.")
        return False
    return bool(open_project_path(project_path))


def select_document_in_tree(tree: QTreeWidget, path: Path) -> bool:
    """Highlight *path* in the project tree if present."""
    return select_document_item(tree, path)


# Re-export role used by document-path lookups for callers that need it.
__all__ = [
    "DOCUMENT_FILE_FILTER",
    "NO_DOCUMENTS_STATUS",
    "NO_NEW_DOCUMENTS_STATUS",
    "ROLE_DOCUMENT_PATH",
    "choose_document_paths",
    "collect_document_additions",
    "prepare_tree_project_for_documents",
    "resolve_document_paths",
    "saved_project_paths_from_tree",
    "select_document_in_tree",
]
