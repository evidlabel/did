"""HEADLESS-gated smoke tests for the gdid GUI.

Run with: ``HEADLESS=1 QT_QPA_PLATFORM=offscreen pytest tests/test_gdid_gui.py``.
Skipped on developer machines unless HEADLESS=1 or CI=true (mirrors evid).
The slow spaCy-backed Anonymizer is replaced by a fake factory throughout.
"""

import os

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(
    os.environ.get("CI") != "true" and os.environ.get("HEADLESS") != "1",
    reason="GUI tests require headless env (set HEADLESS=1)",
)


class FakeAnonymizer:
    """spaCy-free stand-in matching the Anonymizer surface gdid uses."""

    def __init__(self, language="en", detection_profile="thorough"):
        self.language = language
        self.detection_profile = detection_profile

    def detect_entities(self, texts):
        self._texts = list(texts)

    def generate_yaml(self):
        return 'PERSON:\n  - id: "P1"\n    variants:\n      - "John Doe"\n'

    def load_replacements(self, config):
        self._config = config

    def anonymize(self, text):
        return text.replace("John Doe", "#(P1V1)"), {}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make_window(qapp):
    from gdid.gui.window import MainWindow

    return MainWindow(anonymizer_factory=FakeAnonymizer)


class MemorySettings:
    def __init__(self, recent=None):
        self.recent = list(recent or [])
        self.directories = {}

    def recent_projects(self):
        return list(self.recent)

    def add_recent_project(self, path):
        path = type(path)(path)
        self.recent = [path, *(item for item in self.recent if item != path)]

    def remove_recent_project(self, path):
        path = type(self.recent[0])(path) if self.recent else path
        self.recent = [item for item in self.recent if item != path]

    def clear_recent_projects(self):
        self.recent.clear()

    def directory(self, key):
        return self.directories.get(key, "")

    def set_directory(self, key, path):
        self.directories[key] = str(path)


# ------------------------------------------------------------------- window ---
def test_window_builds(qapp):
    w = _make_window(qapp)
    assert "DID" in w.windowTitle()
    assert w.anon_action.isEnabled() is False
    assert w.save_button.isEnabled() is False
    w.close()


def test_every_preview_key_has_a_non_plain_text_color(qapp):
    from PySide6.QtGui import QFont, QTextDocument

    from gdid.gui.highlighter import TOKEN_RE, TypstTokenHighlighter
    from gdid.gui.theme import token_colors
    from gdid.pipeline import PLACEHOLDER_WORDS

    w = _make_window(qapp)
    colors = token_colors()
    plain_text_color = w.preview.palette().color(w.preview.foregroundRole()).name()

    assert set(colors) == set(PLACEHOLDER_WORDS)
    assert all(
        color.casefold() != plain_text_color.casefold() for color in colors.values()
    )
    assert set(w._highlighter._formats) == set(PLACEHOLDER_WORDS)
    for prefix in PLACEHOLDER_WORDS:
        assert TOKEN_RE.fullmatch(f"#({prefix}1V1)")
    assert isinstance(w._highlighter, TypstTokenHighlighter)

    document = QTextDocument(". - #(O7V1) Facebook")
    highlighter = TypstTokenHighlighter(document)
    highlighter.rehighlight()
    rendered_ranges = document.firstBlock().layout().formats()
    assert len(rendered_ranges) == 1
    assert rendered_ranges[0].start == len(". - ")
    assert rendered_ranges[0].length == len("#(O7V1)")
    assert rendered_ranges[0].format.background().style() == Qt.BrushStyle.NoBrush
    assert rendered_ranges[0].format.fontWeight() == 400

    # Moving a token to the right must not leave its old colour on the inserted
    # list marker or surrounding whitespace.
    document.setPlainText(". #(O7V1) Facebook")
    highlighter.rehighlight()
    document.setPlainText(". - #(O7V1) Facebook")
    highlighter.rehighlight()
    rendered_ranges = document.firstBlock().layout().formats()
    colored_ranges = [
        item
        for item in rendered_ranges
        if item.format.foreground().style() != Qt.BrushStyle.NoBrush
    ]
    assert len(colored_ranges) == 1
    assert colored_ranges[0].start == len(". - ")
    assert colored_ranges[0].length == len("#(O7V1)")
    assert w.preview.font().styleStrategy() & QFont.StyleStrategy.PreferNoShaping
    w.close()


def test_help_menu_links_docs_github_license_and_about(qapp):
    w = _make_window(qapp)
    help_menu = w.help_menu
    labels = [
        action.text().replace("&", "")
        for action in help_menu.actions()
        if not action.isSeparator()
    ]

    assert labels == ["Documentation", "DID on GitHub", "MIT License", "About DID"]
    assert w.documentation_action.shortcut().toString() == "F1"
    w.close()


def test_projects_sidebar_lists_recent_projects(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    project_path = save_project(
        Project(name="Research notes"), tmp_path / "research.did-project.yaml"
    )
    settings = MemorySettings([project_path])
    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=settings)

    assert w.project_tree.topLevelItemCount() == 1
    assert w.project_tree.topLevelItem(0).text(0) == "○ Research notes"
    assert w.project_tree.topLevelItem(0).toolTip(0).endswith(str(project_path))
    w.close()


def test_projects_sidebar_prunes_missing_and_disambiguates_names(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    first = save_project(Project(name="Case"), tmp_path / "client-a" / "project")
    second = save_project(Project(name="Case"), tmp_path / "client-b" / "project")
    missing = tmp_path / "gone" / "project.did-project.yaml"
    settings = MemorySettings([first, missing, second])

    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=settings)

    assert w.project_tree.topLevelItemCount() == 2
    assert w.project_tree.topLevelItem(0).text(0) == "○ Case — client-a"
    assert w.project_tree.topLevelItem(1).text(0) == "○ Case — client-b"
    assert missing not in settings.recent
    w.close()


def test_selecting_recent_project_keeps_its_sidebar_position(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    first = save_project(Project(name="First"), tmp_path / "first")
    second = save_project(Project(name="Second"), tmp_path / "second")
    settings = MemorySettings([first, second])
    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=settings)

    w._on_project_tree_item_clicked(w.project_tree.topLevelItem(1), 0)

    assert w._project.name == "Second"
    assert w.project_tree.topLevelItem(0).text(0) == "○ First"
    assert "Second" in w.project_tree.topLevelItem(1).text(0)
    assert (
        w.project_tree.indexOfTopLevelItem(
            w.project_tree.currentItem()
            if w.project_tree.currentItem().parent() is None
            else w.project_tree.currentItem().parent()
        )
        == 1
    )
    w.close()


def test_selecting_only_startup_project_does_not_create_dummy(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, create_project_workdir

    project_path = create_project_workdir(
        Project(name="Existing case"), tmp_path, "case"
    )
    settings = MemorySettings([project_path])
    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=settings)

    assert w._project is None
    w._on_project_tree_item_clicked(w.project_tree.topLevelItem(0), 0)

    assert w._project is not None
    assert w._project.name == "Existing case"
    assert w.project_tree.topLevelItemCount() == 1
    assert "Untitled" not in w.project_tree.topLevelItem(0).text(0)
    assert settings.recent_projects() == [project_path]
    w.close()


def test_yaml_signal_without_active_project_cannot_create_dummy(qapp):
    w = _make_window(qapp)

    w.yaml_edit.setPlainText('PERSON:\n  - id: "PERSON_1"\n    variants: ["Jane"]\n')

    assert w._project is None
    assert w._dirty is False
    w.close()


def test_project_can_be_named_and_renamed(qapp):
    from gdid.gui.window import MainWindow

    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=MemorySettings())
    w._on_new_project(name="Client A")
    assert w._project.name == "Client A"
    assert "Client A" in w.project_tree.topLevelItem(0).text(0)

    w._on_rename_project(name="Client B")
    assert w._project.name == "Client B"
    assert "Client B" in w.windowTitle()
    assert "Client B" in w.project_tree.topLevelItem(0).text(0)
    w.close()


def test_theme_applies(qapp):
    from gdid.gui.theme import apply_theme

    apply_theme(qapp)  # must not raise


def test_load_paths_populates_file_list(qapp, tmp_path):
    from gdid.project import Project

    a = tmp_path / "a.md"
    b = tmp_path / "b.txt"
    a.write_text("John Doe was here.")
    b.write_text("And John Doe again.")
    w = _make_window(qapp)
    w._project = Project(source_paths=[a, b])
    w._load_paths([str(a), str(b)])
    project = w.project_tree.topLevelItem(0)
    assert project.child(0).childCount() == 2
    w.close()


def test_on_anonymized_wires_up_state(qapp, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("John Doe.")
    w = _make_window(qapp)
    w._files = [a]
    w._selected_file = a
    w._extracted = {a: "John Doe."}
    w._refresh_actions()
    assert w.anon_action.isEnabled() is True

    fake = FakeAnonymizer()
    w._on_anonymized(fake, fake.generate_yaml(), {a: "#(P1V1)."})
    assert "PERSON" in w.yaml_edit.toPlainText()
    assert w.save_button.isEnabled() is True
    w.close()


def test_anonymized_result_immediately_refreshes_preview(qapp, tmp_path):
    from gdid.project import Project

    a = tmp_path / "a.md"
    a.write_text("John Doe.")
    w = _make_window(qapp)
    w._files = [a]
    w._project = Project(source_paths=[a])
    w._selected_file = a
    w._extracted = {a: "John Doe."}
    w._refresh_file_list()

    fake = FakeAnonymizer()
    w._on_anonymized(fake, fake.generate_yaml(), {a: "#(P1V1)."})

    assert w._current_file() == a
    assert w.preview.toPlainText() == "#(P1V1)."
    assert "PSEUDONYMIZED" in w.preview_state.text()
    w.close()


def test_project_tree_shows_draft_documents_and_missing_sources(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    present = tmp_path / "verdict.md"
    present.write_text("Reviewed source", encoding="utf-8")
    missing = tmp_path / "missing-opinion.md"
    project_path = save_project(
        Project(name="Appeal", source_paths=[present, missing]), tmp_path / "appeal"
    )
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([project_path]),
    )

    project = w.project_tree.topLevelItem(0)
    draft = project.child(0)
    assert draft.text(0) == "○ Draft"
    assert [draft.child(i).text(0) for i in range(draft.childCount())] == [
        "○ verdict.md",
        "! missing-opinion.md",
    ]
    assert draft.child(1).isDisabled()
    w.close()


def test_project_tree_versions_hide_identity_support_files(qapp, tmp_path):
    import json

    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    project_path = save_project(Project(name="Appeal"), tmp_path / "appeal")
    version = project_path.parent / "versions" / "v001"
    output = version / "output"
    output.mkdir(parents=True)
    (output / "verdict_pseudonymized.typ").write_text("safe", encoding="utf-8")
    (output / "config.yaml").write_text("PERSON: []", encoding="utf-8")
    (output / "shared_vars.typ").write_text("#let P1 = x", encoding="utf-8")
    (output / "shared_fakevars.typ").write_text("#let P1 = fake", encoding="utf-8")
    (version / "manifest.json").write_text(
        json.dumps({"number": 1, "version_id": "v001"}), encoding="utf-8"
    )
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([project_path]),
    )

    version_item = w.project_tree.topLevelItem(0).child(1)
    assert version_item.text(0) == "✓ v001"
    assert version_item.childCount() == 1
    assert version_item.child(0).text(0) == "✓ verdict_pseudonymized.typ"
    assert version_item.text(1) == "PSEUDO/FAKE"
    w.close()


def test_project_tree_refresh_preserves_document_and_expansion(qapp, tmp_path):
    from gdid.project import Project

    source = tmp_path / "verdict.md"
    source.write_text("John Doe", encoding="utf-8")
    w = _make_window(qapp)
    w._project = Project(source_paths=[source])
    w._files = [source]
    w._selected_file = source
    w._refresh_project_tree()
    project = w.project_tree.topLevelItem(0)
    draft = project.child(0)
    project.setExpanded(True)
    draft.setExpanded(True)

    w._refresh_project_tree()

    project = w.project_tree.topLevelItem(0)
    draft = project.child(0)
    assert project.isExpanded()
    assert draft.isExpanded()
    assert w._current_file() == source
    assert w.project_tree.currentItem().text(0).endswith("verdict.md")
    w.close()


def test_clicking_pseudo_badge_keeps_document_focused(qapp, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from gdid.project import Project

    source = tmp_path / "verdict.md"
    source.write_text("John Doe", encoding="utf-8")
    w = _make_window(qapp)
    w._project = Project(source_paths=[source])
    w._files = [source]
    w._selected_file = source
    w._extracted = {source: "John Doe"}
    w._anonymized = {source: "#(P1V1)"}
    w._refresh_project_tree()
    project = w.project_tree.topLevelItem(0)
    project.setExpanded(True)
    draft = project.child(0)
    draft.setExpanded(True)
    document = draft.child(0)
    w.show()
    qapp.processEvents()

    row = w.project_tree.visualItemRect(document)
    mode_x = w.project_tree.columnViewportPosition(1) + 12
    QTest.mouseClick(
        w.project_tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(mode_x, row.center().y()),
    )
    qapp.processEvents()

    assert w._current_file() == source
    assert w.project_tree.currentItem().text(0).endswith("verdict.md")
    assert w.project_tree.hasFocus()
    w.close()


def test_project_tree_marks_published_sources_synced_then_out_of_sync(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import (
        Project,
        begin_version,
        create_project_workdir,
        finalize_version,
    )

    source = tmp_path / "verdict.md"
    source.write_text("Reviewed source", encoding="utf-8")
    project = Project(name="Appeal", source_paths=[source], entity_config_yaml="{}\n")
    create_project_workdir(project, tmp_path / "projects", project.name)
    stage = begin_version(project)
    (stage.path / "output" / "verdict.typ").write_text("safe", encoding="utf-8")
    finalize_version(
        project,
        stage,
        processing={"language": "da", "detection_profile": "thorough"},
        export={"format": "typst", "mode": "multi"},
        app_version="test",
    )
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([project.project_file]),
    )

    project_item = w.project_tree.topLevelItem(0)
    document = project_item.child(0).child(0)
    assert project_item.text(0) == "✓ Appeal"
    assert document.text(0) == "✓ verdict.md"
    assert document.foreground(0).color().name() == "#4caf50"

    source.write_text("Changed source", encoding="utf-8")
    w._refresh_project_tree()
    project_item = w.project_tree.topLevelItem(0)
    document = project_item.child(0).child(0)
    assert project_item.text(0) == "↻ Appeal"
    assert document.text(0) == "↻ verdict.md"
    assert document.foreground(0).color().name() == "#f5a623"
    w.close()


def test_disclosure_click_does_not_activate_project(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    first = save_project(Project(name="First"), tmp_path / "first")
    second = save_project(Project(name="Second"), tmp_path / "second")
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([first, second]),
    )
    item = w.project_tree.topLevelItem(1)
    w._on_project_tree_pressed(item, 0)
    item.setExpanded(not item.isExpanded())
    w._on_project_tree_item_clicked(item, 0)

    assert w._project is None
    w.close()


def test_tree_add_documents_opens_target_project_before_picker(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    first = save_project(Project(name="First"), tmp_path / "first")
    second = save_project(Project(name="Second"), tmp_path / "second")
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([first, second]),
    )
    picker_calls = []
    w._on_open = lambda: picker_calls.append(w._project.name)

    w._add_documents_to_tree_project(str(second.resolve()))

    assert w._project.name == "Second"
    assert picker_calls == ["Second"]
    w.close()


def test_add_documents_requires_tree_selection_when_saved_projects_exist(
    qapp, tmp_path
):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, save_project

    first = save_project(Project(name="First"), tmp_path / "first")
    second = save_project(Project(name="Second"), tmp_path / "second")
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([first, second]),
    )
    opened = []
    w._open_project_path = lambda path: opened.append(path) or True

    assert w._prepare_tree_project_for_documents() is False
    assert "Select a project" in w.status_label.text()
    assert opened == []

    w.project_tree.setCurrentItem(w.project_tree.topLevelItem(1))
    assert w._prepare_tree_project_for_documents() is True
    assert opened == [str(second.resolve())]
    w.close()


def test_stale_worker_result_cannot_replace_selected_project_entities(qapp):
    from PySide6.QtCore import QObject, Signal

    class ResultEmitter(QObject):
        finished = Signal(object, str, object)
        error = Signal(str)

    w = _make_window(qapp)
    emitter = ResultEmitter()
    stale_yaml = 'PERSON:\n  - id: "PERSON_1"\n    variants: ["Old case"]\n'
    accepted = []
    w._connect_worker(emitter, lambda *_args: accepted.append(stale_yaml))
    w._advance_session_generation()

    emitter.finished.emit(FakeAnonymizer(), stale_yaml, {})

    assert accepted == []
    w.close()


def test_opening_project_immediately_replaces_right_pane_state(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, create_project_workdir

    first = create_project_workdir(
        Project(
            name="First",
            entity_config_yaml=(
                'PERSON:\n  - id: "PERSON_1"\n    variants: ["First case"]\n'
            ),
        ),
        tmp_path,
        "first",
    )
    second = create_project_workdir(Project(name="Second"), tmp_path, "second")
    w = MainWindow(
        anonymizer_factory=FakeAnonymizer,
        settings=MemorySettings([first, second]),
    )
    assert w._open_project_path(first)
    assert w.entity_table.rowCount() == 1

    assert w._open_project_path(second)

    assert w._project.name == "Second"
    assert w.entity_table.rowCount() == 0
    assert w.preview.toPlainText() == ""
    assert "No document" in w.preview_state.text()
    w.close()


def test_entity_details_show_full_untruncated_variants(qapp):
    w = _make_window(qapp)
    long_variant = "Virksomhedstype med en meget lang og informativ beskrivelse"
    w.yaml_edit.setPlainText(
        f'PERSON:\n  - id: "PERSON_1"\n    variants:\n      - "{long_variant}"\n'
    )

    assert long_variant in w.entity_details.toPlainText()
    assert w.entity_table.item(0, 2).toolTip() == long_variant
    w.close()


def test_entity_table_prioritizes_variants_and_compacts_repeated_id(qapp):
    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        'PERSON:\n  - id: "PERSON_42"\n    variants: ["Jane Doe"]\n'
    )

    assert w.entity_table.horizontalHeaderItem(1).text() == "#"
    assert w.entity_table.item(0, 1).text() == "42"
    assert w.entity_table.item(0, 1).toolTip() == "PERSON_42"
    header = w.entity_table.horizontalHeader()
    assert header.sectionSize(0) == 104
    assert header.sectionSize(1) == 52
    assert header.sectionResizeMode(2) == header.ResizeMode.Stretch
    w.close()


def test_entity_table_shows_reported_confidence(qapp):
    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["John Doe"]\n'
        "    confidence: 0.873\n"
    )

    assert w.entity_table.columnCount() == 4
    assert w.entity_table.horizontalHeaderItem(3).text() == "Confidence"
    assert w.entity_table.item(0, 3).text() == "87%"
    assert "Detection confidence: 87%" in w.entity_details.toPlainText()
    w.close()


def test_entity_type_filter_can_show_one_type_or_all(qapp):
    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["John Doe"]\n'
        "ORGANIZATION:\n"
        '  - id: "ORGANIZATION_1"\n'
        '    variants: ["Acme"]\n'
    )

    w.entity_type_filter.setCurrentIndex(w.entity_type_filter.findData("ORGANIZATION"))
    assert w.entity_table.isRowHidden(0)
    assert not w.entity_table.isRowHidden(1)

    w.entity_type_filter.setCurrentIndex(w.entity_type_filter.findData("ALL"))
    assert not w.entity_table.isRowHidden(0)
    assert not w.entity_table.isRowHidden(1)
    w.close()


def test_entity_right_click_menu_exposes_review_actions(qapp):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        'PERSON:\n  - id: "PERSON_1"\n    variants: ["Heading text"]\n'
    )

    w._set_dirty(False)
    w.show()
    qapp.processEvents()
    # Native popup grabs are unsupported by Qt's offscreen plugin. Suppress
    # only the platform popup while testing the real mouse-release route.
    w._suppress_context_menu_popup = True
    target = w.entity_table.visualItemRect(w.entity_table.item(0, 2)).center()
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(target),
        QPointF(w.entity_table.viewport().mapToGlobal(target)),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.entity_table.mouseReleaseEvent(release)
    qapp.processEvents()
    menu = w._entity_context_menu
    labels = w._last_entity_context_labels

    assert w._entity_context_menu_popup_count == 1
    assert "Add variant…" in labels
    assert "Change type" in labels
    assert "Do not pseudonymize" in labels
    inspection_menu = w._build_entity_context_menu(0)
    change_type = inspection_menu._submenus[0]
    assert {action.text() for action in change_type.actions()} >= {
        "PERSON",
        "ORGANIZATION",
        "LOCATION",
    }

    if menu is not None:
        menu.close()
    w.close()


def test_entity_right_click_menu_labels_multi_person_exclusion(qapp):
    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["Heading one"]\n'
        '  - id: "PERSON_2"\n'
        '    variants: ["Heading two"]\n'
    )
    for row in (0, 1):
        for column in range(w.entity_table.columnCount()):
            w.entity_table.item(row, column).setSelected(True)

    menu = w._build_entity_context_menu(1)
    labels = [action.text() for action in menu.actions()]

    assert "Do not pseudonymize (2 selected)" in labels
    w.close()


def test_mark_not_name_persists_and_removes_identity(qapp, tmp_path):
    from gdid.project import Project, load_not_names, save_project

    w = _make_window(qapp)
    project = Project(name="Review")
    save_project(project, tmp_path / "review.did-project.yaml")
    w._project = project
    w.yaml_edit.setPlainText(
        'PERSON:\n  - id: "PERSON_1"\n    variants:\n'
        '      - "Virksomhedstype"\n'
        '      - "Virksomhedstypen"\n'
    )

    w._mark_not_name(0)

    assert load_not_names(project) == ["Virksomhedstype", "Virksomhedstypen"]
    assert w.entity_table.rowCount() == 0
    assert "PERSON: []" in w.yaml_edit.toPlainText()
    w.close()


def test_mark_not_name_supports_multiple_selected_people_and_persists(qapp, tmp_path):
    from gdid import pipeline
    from gdid.project import Project, load_not_names, load_project, save_project

    w = _make_window(qapp)
    project = Project(name="Review")
    save_project(project, tmp_path / "review")
    w._project = project
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["Acme heading"]\n'
        '  - id: "PERSON_2"\n'
        '    variants: ["Opinion label"]\n'
        "ORGANIZATION:\n"
        '  - id: "ORGANIZATION_1"\n'
        '    variants: ["Real Organization"]\n'
    )
    for row in (0, 1):
        for column in range(w.entity_table.columnCount()):
            w.entity_table.item(row, column).setSelected(True)

    w._mark_not_names()

    assert load_not_names(project) == ["Acme heading", "Opinion label"]
    data = pipeline.parse_yaml(w.yaml_edit.toPlainText())
    assert data["PERSON"] == []
    assert data["ORGANIZATION"][0]["variants"] == ["Real Organization"]
    stored = load_project(project.project_file)
    assert "Acme heading" not in stored.entity_config_yaml
    assert "Real Organization" in stored.entity_config_yaml
    assert "2 identities" in w.status_label.text()
    project_item = next(
        w.project_tree.topLevelItem(index)
        for index in range(w.project_tree.topLevelItemCount())
        if w.project_tree.topLevelItem(index).text(0).endswith("Review")
    )
    excluded_group = next(
        project_item.child(0).child(index)
        for index in range(project_item.child(0).childCount())
        if "Excluded detections" in project_item.child(0).child(index).text(0)
    )
    assert excluded_group.text(0) == "Excluded detections (2)"
    assert [
        excluded_group.child(index).text(0)
        for index in range(excluded_group.childCount())
    ] == ["⊘ Acme heading", "⊘ Opinion label"]
    assert excluded_group.child(0).text(1) == "DO NOT PSEUDONYMIZE"
    w.close()


def test_merge_entity_rows_combines_people(qapp):
    from gdid import pipeline

    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["J. Doe"]\n'
        '  - id: "PERSON_2"\n'
        '    variants: ["John Doe"]\n'
    )

    w._merge_entity_rows(0, 1)
    people = pipeline.parse_yaml(w.yaml_edit.toPlainText())["PERSON"]

    assert len(people) == 1
    assert people[0]["variants"] == ["John Doe", "J. Doe"]
    w.close()


def test_change_type_supports_multiple_selection_and_persists(qapp, tmp_path):
    from gdid import pipeline
    from gdid.project import Project, load_project, save_project

    w = _make_window(qapp)
    project = Project(name="Review")
    save_project(project, tmp_path / "review")
    w._project = project
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n'
        '    variants: ["Acme"]\n'
        '  - id: "PERSON_2"\n'
        '    variants: ["Example Ltd"]\n'
    )
    for row in (0, 1):
        for column in range(w.entity_table.columnCount()):
            w.entity_table.item(row, column).setSelected(True)

    w._change_selected_entity_type("ORGANIZATION")

    data = pipeline.parse_yaml(w.yaml_edit.toPlainText())
    assert data["PERSON"] == []
    assert [entity["id"] for entity in data["ORGANIZATION"]] == [
        "ORGANIZATION_1",
        "ORGANIZATION_2",
    ]
    stored = load_project(project.project_file)
    assert "ORGANIZATION_1" in stored.entity_config_yaml
    w.close()


def test_add_variant_updates_identity_and_persists(qapp, tmp_path):
    from gdid import pipeline
    from gdid.project import Project, load_project, save_project

    w = _make_window(qapp)
    project = Project(name="Review")
    save_project(project, tmp_path / "review")
    w._project = project
    w.yaml_edit.setPlainText(
        'PERSON:\n  - id: "PERSON_1"\n    variants: ["John Doe"]\n'
    )

    w._add_entity_variant(0, "J. Doe")

    people = pipeline.parse_yaml(w.yaml_edit.toPlainText())["PERSON"]
    assert people[0]["variants"] == ["John Doe", "J. Doe"]
    stored = load_project(project.project_file)
    assert "J. Doe" in stored.entity_config_yaml
    w.close()


def test_version_view_is_read_only_and_returns_to_draft(qapp, tmp_path):
    from gdid.project import (
        Project,
        begin_version,
        create_project_workdir,
        finalize_version,
    )

    project = Project(name="Case", entity_config_yaml="PERSON: []\n")
    create_project_workdir(project, tmp_path, "Case")
    stage = begin_version(project)
    (stage.path / "output" / "archived.typ").write_text("#(P1V1)", encoding="utf-8")
    version = finalize_version(
        project,
        stage,
        processing={"language": "da", "detection_profile": "thorough"},
        export={"format": "typst", "mode": "multi"},
        app_version="test",
    )
    w = _make_window(qapp)
    w._project = project
    w._files = []
    w._view_version(version.path)

    assert w.yaml_edit.isReadOnly() is True
    assert w.save_button.isEnabled() is False
    assert "VERSION" in w.preview_state.text()

    w._return_to_draft()
    assert w.yaml_edit.isReadOnly() is False
    assert w._viewing_version is None
    w.close()


# ------------------------------------------------------------------ workers ---
def test_extract_worker_emits_finished(qapp, tmp_path):
    from gdid.gui.workers import ExtractWorker

    a = tmp_path / "a.md"
    b = tmp_path / "b.txt"
    a.write_text("alpha")
    b.write_text("beta")

    results = []
    errors = []
    worker = ExtractWorker([a, b])
    worker.finished.connect(results.append, Qt.ConnectionType.DirectConnection)
    worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    worker.wait(5000)

    assert errors == []
    assert len(results) == 1
    extracted = results[0]
    assert "alpha" in extracted[a]
    assert "beta" in extracted[b]


def test_anonymize_worker_emits_finished(qapp):
    from gdid.gui.workers import AnonymizeWorker

    texts = {"d1": "John Doe met X.", "d2": "Hi John Doe."}
    payloads = []
    errors = []
    worker = AnonymizeWorker(texts, "en", anonymizer_factory=FakeAnonymizer)
    worker.finished.connect(
        lambda *a: payloads.append(a), Qt.ConnectionType.DirectConnection
    )
    worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    worker.wait(5000)

    assert errors == []
    assert len(payloads) == 1
    anonymizer, yaml_str, anonymized, report = payloads[0]
    assert isinstance(anonymizer, FakeAnonymizer)
    assert "PERSON" in yaml_str
    assert anonymized["d1"] == "#(P1V1) met X."
    assert report.is_clean


def test_anonymize_worker_verification_failure_does_not_break_the_run(qapp):
    """A stand-in anonymizer cannot do the deep sweep; that must not error.

    Verification inspects the output — it is not part of producing it, so its
    failure degrades to the model-free tiers instead of failing the run.
    """
    from gdid.gui.workers import AnonymizeWorker

    payloads = []
    errors = []
    worker = AnonymizeWorker(
        {"d": "John Doe."}, "en", anonymizer_factory=FakeAnonymizer
    )
    worker.finished.connect(
        lambda *a: payloads.append(a), Qt.ConnectionType.DirectConnection
    )
    worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    worker.wait(5000)

    assert errors == []
    assert payloads[0][3].deep is False


def test_anonymize_worker_systemexit_emits_error_instead_of_crashing(qapp):
    """spaCy's missing-model download calls sys.exit; that must not kill QThread."""
    from gdid.gui.workers import AnonymizeWorker

    class ExplodingAnonymizer:
        def __init__(self, **_kwargs):
            raise SystemExit(1)

    errors = []
    worker = AnonymizeWorker({"d": "x"}, "en", anonymizer_factory=ExplodingAnonymizer)
    worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    finished = worker.wait(5000)

    assert finished
    assert errors, "SystemExit escaped QThread.run"
    assert "uv sync --extra models" in errors[0]


def test_pseudo_worker_emits_a_verification_report(qapp):
    from gdid.gui.workers import PseudoWorker

    payloads = []
    worker = PseudoWorker(
        FakeAnonymizer(),
        'PERSON:\n  - id: "P1"\n    variants:\n      - "John Doe"\n',
        {"d": "John Doe met Doesagen."},
    )
    worker.finished.connect(
        lambda *a: payloads.append(a), Qt.ConnectionType.DirectConnection
    )
    worker.start()
    worker.wait(5000)

    anonymized, report = payloads[0]
    assert anonymized["d"] == "#(P1V1) met Doesagen."
    # The surname survived inside the compound; the shallow tiers still catch it.
    assert [f.text for f in report.leaks] == ["Doe"]


def test_pseudo_worker_reports_bad_yaml(qapp):
    from gdid.gui.workers import PseudoWorker

    errors = []
    worker = PseudoWorker(FakeAnonymizer(), "key: [unclosed", {"d": "John Doe"})
    worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    worker.wait(5000)
    assert errors and "Invalid YAML" in errors[0]


# ---------------------------------------------------------------- clipboard ---
def _window_with_anonymized(qapp, tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("John Doe one.")
    b.write_text("John Doe two.")
    w = _make_window(qapp)
    w._files = [a, b]
    w._selected_file = a
    w._extracted = {a: "John Doe one.", b: "John Doe two."}
    w._anonymized = {a: "#(P1V1) one.", b: "#(P1V1) two."}
    w._refresh_file_list()
    return w, a, b


def test_copy_document_sets_clipboard(qapp, tmp_path):
    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    w._copy_document(a)
    assert qapp.clipboard().text() == "#(P1V1) one."
    w.close()


def test_copy_all_concatenates_with_headings(qapp, tmp_path):
    w, a, b = _window_with_anonymized(qapp, tmp_path)
    w._copy_all()
    text = qapp.clipboard().text()
    # Headings are assigned title tokens: a source filename is identifying, and
    # this text is meant to be pasted straight into an LLM session.
    assert "= #(DOC1V1)\n\n#(P1V1) one." in text
    assert "= #(DOC2V1)\n\n#(P1V1) two." in text
    assert text.index("#(DOC1V1)") < text.index("#(DOC2V1)")
    assert a.name not in text
    assert b.name not in text
    w.close()


def test_copy_menu_enabled_states(qapp, tmp_path):
    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    menu = w._copy_menu(a)
    copy_one, copy_all = (act for act in menu.actions() if not act.isSeparator())
    assert copy_one.isEnabled() is True
    assert copy_all.isEnabled() is True
    w.close()


def test_copy_menu_disabled_before_anonymize(qapp, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("John Doe.")
    w = _make_window(qapp)
    w._files = [a]
    w._extracted = {a: "John Doe."}
    menu = w._copy_menu(a)
    copy_one, copy_all = (act for act in menu.actions() if not act.isSeparator())
    assert copy_one.isEnabled() is False
    assert copy_all.isEnabled() is False
    w.close()


def test_file_list_has_custom_context_menu(qapp):
    w = _make_window(qapp)
    assert w.project_tree.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    w.close()


def test_delete_project_removes_workdir_and_active_session(qapp, tmp_path):
    from gdid.gui.window import MainWindow
    from gdid.project import Project, create_project_workdir

    project_path = create_project_workdir(Project(name="Disposable"), tmp_path, "case")
    settings = MemorySettings([project_path])
    w = MainWindow(anonymizer_factory=FakeAnonymizer, settings=settings)
    assert w._open_project_path(project_path)

    assert w._delete_project(project_path, confirmed=True)
    assert not project_path.parent.exists()
    assert settings.recent_projects() == []
    assert w._project is None
    w.close()


def test_preview_context_menu_includes_copy_actions(qapp, tmp_path):
    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w._show_current_preview()
    menu = w.preview.build_context_menu()
    labels = [act.text() for act in menu.actions()]
    assert any("Copy document" in t for t in labels)
    assert any("Copy all documents" in t for t in labels)
    w.close()


def test_preview_find_bar_searches_and_wraps_current_document(qapp):
    w = _make_window(qapp)
    w.preview.setPlainText("Alpha beta alpha")
    w._set_preview_state("RAW")
    w._show_preview_find()
    w.preview_find.setText("alpha")

    assert w.preview_find_bar.isHidden() is False
    assert w.preview.textCursor().selectedText().casefold() == "alpha"
    assert w._find_preview_text()
    assert w.preview.textCursor().selectionStart() == 11
    assert w._find_preview_text()
    assert w.preview.textCursor().selectionStart() == 0

    w._hide_preview_find()
    assert w.preview_find_bar.isHidden()
    w.close()


@pytest.mark.parametrize("preview_text", ["See #(P2V1) here", "See [PERSON 2] here"])
def test_clicking_preview_token_selects_matching_entity(qapp, preview_text):
    w = _make_window(qapp)
    w.yaml_edit.setPlainText(
        "PERSON:\n"
        '  - id: "PERSON_1"\n    variants: ["Jane Doe"]\n'
        '  - id: "PERSON_2"\n    variants: ["John Doe"]\n'
    )
    w.preview.setPlainText(preview_text)
    cursor = w.preview.textCursor()
    cursor.setPosition(preview_text.index("2"))
    w.preview.setTextCursor(cursor)

    assert w.entity_table.currentRow() == 1
    assert w.entity_table.item(1, 2).isSelected()
    assert "John Doe" in w.entity_details.toPlainText()
    w.close()


def test_preview_selection_can_create_default_person(qapp, tmp_path):
    from gdid import pipeline

    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w.preview.setPlainText("An overlooked Jane Doe appears here.")
    cursor = w.preview.textCursor()
    cursor.setPosition(14)
    cursor.setPosition(22, cursor.MoveMode.KeepAnchor)
    w.preview.setTextCursor(cursor)

    menu = w.preview.build_context_menu()
    create_action = next(
        action
        for action in menu.actions()
        if action.text() == "Create entity from selection"
    )
    person_action = create_action.menu().actions()[0]
    assert person_action.text() == "PERSON (default)"
    person_action.trigger()

    people = pipeline.parse_yaml(w.yaml_edit.toPlainText())["PERSON"]
    assert any(entity["variants"] == ["Jane Doe"] for entity in people)
    w._set_dirty(False)
    w.close()


def test_preview_selection_can_be_added_to_existing_entity(qapp, tmp_path):
    from gdid import pipeline

    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w.yaml_edit.setPlainText(
        'PERSON:\n  - id: "PERSON_1"\n    variants: ["John Doe"]\n'
    )
    w.preview.setPlainText("J. Doe")
    w.preview.selectAll()
    menu = w.preview.build_context_menu()
    existing_action = next(
        action
        for action in menu.actions()
        if action.text() == "Add selection to existing entity"
    )
    existing_action.menu().actions()[0].trigger()

    people = pipeline.parse_yaml(w.yaml_edit.toPlainText())["PERSON"]
    assert people[0]["variants"] == ["John Doe", "J. Doe"]
    w._set_dirty(False)
    w.close()


def test_preview_selection_resolves_placeholder_before_adding_variant(qapp, tmp_path):
    from gdid import pipeline

    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w.yaml_edit.setPlainText('PERSON:\n  - id: "PERSON_1"\n    variants: ["John"]\n')
    w.preview.setPlainText("#(P1V1) Doe")
    w.preview.selectAll()
    menu = w.preview.build_context_menu()
    existing_action = next(
        action
        for action in menu.actions()
        if action.text() == "Add selection to existing entity"
    )
    existing_action.menu().actions()[0].trigger()

    people = pipeline.parse_yaml(w.yaml_edit.toPlainText())["PERSON"]
    assert people[0]["variants"] == ["John", "John Doe"]
    assert "#(P1V1) Doe" not in w.yaml_edit.toPlainText()
    w._set_dirty(False)
    w.close()


# -------------------------------------------------------------- output mode ---
def test_output_mode_toggle_changes_preview(qapp, tmp_path):
    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w.yaml_edit.setPlainText(FakeAnonymizer().generate_yaml())
    w._show_current_preview()
    assert "#(P1V1)" in w.preview.toPlainText()
    w.output_combo.setCurrentIndex(1)  # written-out mode
    assert "[PERSON 1]" in w.preview.toPlainText()
    w.output_combo.setCurrentIndex(0)  # back to Typst
    assert "#(P1V1)" in w.preview.toPlainText()
    w.output_combo.setCurrentIndex(2)  # irreversible redaction
    assert w.preview.toPlainText() == "[REDACTED] one."
    assert w.preview_state.text() == "REDACTED"
    w.output_combo.setCurrentIndex(3)  # stable synthetic identity
    synthetic = w.preview.toPlainText()
    assert "#(P1V1)" not in synthetic
    assert "one." in synthetic
    assert w.preview_state.text() == "SYNTHETIC · FAKER"
    w.output_combo.setCurrentIndex(0)
    w.output_combo.setCurrentIndex(3)
    assert w.preview.toPlainText() == synthetic
    w.close()


def test_copy_follows_output_mode(qapp, tmp_path):
    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    w.output_combo.setCurrentIndex(1)
    w._copy_document(a)
    assert qapp.clipboard().text() == "[PERSON 1] one."
    w._copy_all()
    assert "[PERSON 1] two." in qapp.clipboard().text()
    w.output_combo.setCurrentIndex(2)
    w._copy_document(a)
    assert qapp.clipboard().text() == "[REDACTED] one."
    w.close()


def test_llm_handoff_copies_current_or_all_without_source_names(qapp, tmp_path):
    import json

    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    w._selected_file = a

    w._copy_current_llm_markdown()
    assert qapp.clipboard().text() == "# #(DOC1V1)\n\n#(P1V1) one.\n"

    w._copy_llm_json()
    payload = json.loads(qapp.clipboard().text())
    assert payload["rendering"] == "typst"
    assert len(payload["documents"]) == 2
    assert payload["documents"][0]["id"] == "document-001"
    assert payload["documents"][0]["title_token"] == "#(DOC1V1)"
    # A filename identifies the case as surely as the body does. It must not
    # reach the handoff under any key.
    assert "original_name" not in payload["documents"][0]
    assert a.name not in qapp.clipboard().text()
    assert str(a.parent) not in qapp.clipboard().text()
    w.close()


def test_llm_zip_contains_only_safe_session_files(qapp, tmp_path):
    import zipfile

    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    destination = tmp_path / "handoff.zip"

    w._save_llm_zip(destination=destination)

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert names == {
            "README.txt",
            "combined.md",
            "session.json",
            "documents/document-001.md",
            "documents/document-002.md",
        }
        contents = "\n".join(archive.read(name).decode("utf-8") for name in names)
    assert str(a.parent) not in contents
    assert "entities.yaml" not in contents
    # README.txt promises original filenames are excluded; hold it to that,
    # across the entry names as well as the payloads.
    assert a.name not in contents
    assert not any(a.name in name for name in names)
    w.close()


def _leaky_window(qapp, tmp_path):
    """A window whose pseudonymized output still contains a known surname."""
    from gdid import pipeline

    w, a, _ = _window_with_anonymized(qapp, tmp_path)
    w._anonymized = {p: "#(P1V1) met Doesagen." for p in w._anonymized}
    w._yaml_text = 'PERSON:\n  - id: "P1"\n    variants:\n      - "John Doe"\n'
    w._set_verification(pipeline.verify_outputs(w._yaml_text, w._anonymized))
    return w, a


def test_export_is_cancelled_when_verification_finds_a_leak(
    qapp, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    w, _ = _leaky_window(qapp, tmp_path)
    assert w._verification.leaks

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    destination = tmp_path / "blocked.zip"
    w._save_llm_zip(destination=destination)

    assert not destination.exists()
    assert w._verification_acknowledged is False
    w.close()


def test_export_proceeds_once_the_reviewer_acknowledges(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w, _ = _leaky_window(qapp, tmp_path)

    # Click the accept-role button rather than Cancel.
    def _accept(box):
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
                box.setDefaultButton(button)
                button.click()
                return

    monkeypatch.setattr(QMessageBox, "exec", _accept)
    destination = tmp_path / "acknowledged.zip"
    w._save_llm_zip(destination=destination)

    assert destination.exists()
    assert w._verification_acknowledged is True

    # A second export does not ask again for the same output.
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: pytest.fail("should not re-prompt")
    )
    w._save_llm_zip(destination=tmp_path / "again.zip")
    assert (tmp_path / "again.zip").exists()
    w.close()


def test_acknowledgement_is_dropped_when_the_output_changes(qapp, tmp_path):
    """An override applies only to output the reviewer actually saw."""
    w, _ = _leaky_window(qapp, tmp_path)
    w._verification_acknowledged = True

    w._set_verification(w._verification)

    assert w._verification_acknowledged is False
    w.close()


def test_clean_output_never_prompts(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    from gdid import pipeline

    w._set_verification(
        pipeline.verify_outputs(
            'PERSON:\n  - id: "P1"\n    variants:\n      - "John Doe"\n',
            w._anonymized,
        )
    )
    assert w._verification.is_clean

    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: pytest.fail("should not prompt")
    )
    destination = tmp_path / "clean.zip"
    w._save_llm_zip(destination=destination)
    assert destination.exists()
    w.close()


def test_project_meta_is_saved_visible_and_included_in_llm_json(qapp, tmp_path):
    import json

    from gdid.gui.window import _ROLE_ACTIVE_PROJECT, _ROLE_PROJECT_KIND
    from gdid.project import Project, create_project_workdir, load_project

    w, _, _ = _window_with_anonymized(qapp, tmp_path)
    w._project = Project(name="Case")
    create_project_workdir(w._project, tmp_path / "projects", "Case")

    w._on_case_details(
        metadata={
            "matter_type": "Appeal",
            "jurisdiction": "Denmark",
            "tags": ["employment", "evidence"],
        }
    )
    w._copy_llm_json()

    assert load_project(w._project.project_file).metadata["matter_type"] == "Appeal"
    assert json.loads(qapp.clipboard().text())["metadata"]["jurisdiction"] == "Denmark"
    project_item = next(
        w.project_tree.topLevelItem(index)
        for index in range(w.project_tree.topLevelItemCount())
        if w.project_tree.topLevelItem(index).data(0, _ROLE_ACTIVE_PROJECT)
    )
    meta = next(
        project_item.child(index)
        for index in range(project_item.childCount())
        if project_item.child(index).data(0, _ROLE_PROJECT_KIND) == "meta"
    )
    assert meta.text(0) == "Meta"
    assert meta.text(1) == "META"
    values = [meta.child(index).text(0) for index in range(meta.childCount())]
    assert "Matter Type: Appeal" in values
    assert "Jurisdiction: Denmark" in values
    w.close()


def test_dark_theme_combo_width_fits_items(qapp):
    from gdid.gui.theme import set_dark_theme

    w = _make_window(qapp)
    set_dark_theme(w)
    for combo in (w.lang_combo, w.output_combo):
        fm = combo.fontMetrics()
        longest = max((combo.itemText(i) for i in range(combo.count())), key=len)
        needed = fm.horizontalAdvance(longest) + 24  # text + arrow allowance
        assert combo.sizeHint().width() >= needed
    w.close()


# --------------------------------------------------------------------- save ---
def test_successful_document_import_automatically_creates_version(
    qapp, tmp_path, monkeypatch
):
    from gdid import pipeline
    from gdid.project import Project, create_project_workdir

    source = tmp_path / "a.md"
    source.write_text("John Doe.", encoding="utf-8")

    def fake_export(input_path, anonymizer, main_path, **kwargs):
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_text("#(P1V1)\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "export_to_typst", fake_export)
    w = _make_window(qapp)
    w._project = Project(name="Case")
    create_project_workdir(w._project, tmp_path / "projects", "Case")
    w._files = [source]
    w._extracted = {source: "John Doe."}
    w._auto_version_pending = True
    fake = FakeAnonymizer()

    w._on_anonymized(fake, fake.generate_yaml(), {source: "#(P1V1)."})

    version = w._project.workdir / "versions" / "v001"
    assert (version / "output" / "a_pseudonymized.typ").exists()
    assert (version / "manifest.json").exists()
    assert w._auto_version_pending is False
    w.close()


def test_save_action_writes_outputs(qapp, tmp_path, monkeypatch):
    from gdid import pipeline
    from gdid.project import Project

    a = tmp_path / "a.md"
    a.write_text("John Doe.")
    out = tmp_path / "out"
    out.mkdir()

    def fake_export(input_path, anonymizer, main_path, **kwargs):
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_text("#(P1V1)\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "export_to_typst", fake_export)
    monkeypatch.setattr(
        "gdid.gui.window.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(out)),
    )

    w = _make_window(qapp)
    w._project = Project()
    w._files = [a]
    w._anonymized = {a: "#(P1V1)."}
    w._anonymizer = FakeAnonymizer()
    w.yaml_edit.setPlainText("PERSON: []")
    w._refresh_actions()
    w._on_save("multi")

    version = out / "untitled-project" / "versions" / "v001"
    assert (version / "output" / "a_pseudonymized.typ").exists()
    assert (version / "output" / "config.yaml").exists()
    assert (version / "manifest.json").exists()
    w.close()
