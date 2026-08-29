import json
from pathlib import Path

import pytest

from gdid.project import (
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
    load_project,
    save_not_names,
    save_project,
)


def test_project_round_trip_uses_relative_paths(tmp_path):
    source = tmp_path / "documents" / "letter.md"
    source.parent.mkdir()
    source.write_text("private", encoding="utf-8")
    project = Project(
        name="Case",
        source_paths=[source],
        metadata={"matter_type": "Appeal", "tags": ["evidence"]},
        language="en",
        entity_config_yaml='PERSON:\n  - id: "P1"\n',
        export_destination=tmp_path / "output",
    )

    project_path = save_project(project, tmp_path / "case.did-project.yaml")
    serialized = project_path.read_text(encoding="utf-8")
    loaded = load_project(project_path)

    assert "documents/letter.md" in serialized
    assert str(source) not in serialized
    assert "private" not in serialized
    assert loaded.name == "Case"
    assert loaded.source_paths == [source]
    assert loaded.metadata == {"matter_type": "Appeal", "tags": ["evidence"]}
    assert loaded.entity_config_yaml == project.entity_config_yaml
    assert loaded.export_destination == tmp_path / "output"
    registry = json.loads(
        (tmp_path / "draft" / "sources.json").read_text(encoding="utf-8")
    )
    assert registry["local_only"] is True
    assert registry["sources"][0]["original_name"] == "letter.md"
    assert registry["sources"][0]["path"] == str(source)


def test_external_source_remains_absolute(tmp_path):
    external = Path("/outside/case.md")
    project_path = save_project(
        Project(source_paths=[external]), tmp_path / "case.did-project.yaml"
    )
    assert str(external) in project_path.read_text(encoding="utf-8")
    assert load_project(project_path).source_paths == [external]


def test_untitled_project_requires_destination():
    with pytest.raises(ProjectError, match="destination"):
        save_project(Project())


def test_invalid_schema_is_rejected(tmp_path):
    project_path = tmp_path / "future.did-project.yaml"
    project_path.write_text("schema_version: 999\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="schema version"):
        load_project(project_path)


def test_invalid_language_is_rejected(tmp_path):
    project_path = tmp_path / "bad.did-project.yaml"
    project_path.write_text(
        "schema_version: 1\nprocessing:\n  language: xx\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectError, match="language"):
        load_project(project_path)


def test_not_names_round_trip_in_project_workdir(tmp_path):
    project = Project(name="Case")
    save_project(project, tmp_path / "case.did-project.yaml")

    exclusion_path = save_not_names(
        project, ["Virksomhedstype", "Kilder", "virksomhedstype"]
    )

    assert exclusion_path == tmp_path / "draft" / "not_names.json"
    assert load_not_names(project) == ["Kilder", "Virksomhedstype"]


def test_not_names_require_saved_project():
    with pytest.raises(ProjectError, match="Save the project"):
        save_not_names(Project(), ["Kilder"])


def test_canonical_workdir_and_immutable_version(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("John Doe", encoding="utf-8")
    project = Project(
        name="Client Case",
        source_paths=[source],
        entity_config_yaml="PERSON: []\n",
    )
    project_file = create_project_workdir(project, tmp_path, "Client Case")

    assert project_file == tmp_path / "client-case" / "project.did-project.yaml"
    assert (project.workdir / "draft" / "entities.yaml").exists()
    assert (project.workdir / "README.md").exists()

    stage = begin_version(project, "Strict review")
    (stage.path / "output" / "document.typ").write_text("#(P1V1)", encoding="utf-8")
    version = finalize_version(
        project,
        stage,
        processing={"language": "en", "detection_profile": "thorough"},
        export={"format": "typst", "mode": "multi"},
        app_version="test",
    )

    assert version.version_id == "v001-strict-review"
    assert version.path.exists()
    assert not stage.path.exists()
    assert list_versions(project) == [version]
    assert version.manifest["sources"][0]["sha256"]
    assert version.manifest["files"]


def test_aborted_version_does_not_consume_number(tmp_path):
    project = Project(name="Case", entity_config_yaml="PERSON: []\n")
    create_project_workdir(project, tmp_path, "Case")

    first = begin_version(project)
    abort_version(first)
    replacement = begin_version(project)

    assert replacement.number == 1
    abort_version(replacement)


def test_delete_project_workdir_removes_read_only_versions(tmp_path):
    project = Project(name="Case", entity_config_yaml="PERSON: []\n")
    create_project_workdir(project, tmp_path, "Case")
    version = project.workdir / "versions" / "v001"
    output = version / "output"
    output.mkdir(parents=True)
    archived = output / "document.md"
    archived.write_text("#(P1V1)", encoding="utf-8")
    archived.chmod(0o400)
    output.chmod(0o500)
    version.chmod(0o500)
    workdir = project.workdir

    delete_project_workdir(project)

    assert not workdir.exists()


def test_legacy_conversion_is_non_destructive(tmp_path):
    legacy_path = tmp_path / "legacy.did-project.yaml"
    legacy_path.write_text(
        "schema_version: 1\n"
        'project_id: "legacy-id"\n'
        'name: "Legacy Case"\n'
        "sources: []\n"
        "processing:\n  language: da\n"
        "entities:\n  yaml: |\n    PERSON: []\n"
        "export:\n  mode: multi\n",
        encoding="utf-8",
    )
    project = load_project(legacy_path)
    assert project.legacy is True

    converted = convert_legacy_project(project, tmp_path / "converted")

    assert legacy_path.exists()
    assert converted.name == "project.did-project.yaml"
    assert load_project(converted).legacy is False
