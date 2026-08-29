"""Persistent project model for the gdid GUI.

Project files contain user choices and the reviewed entity configuration, but
never extracted source text or generated pseudonymized documents.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ruamel.yaml import YAML

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
PROJECT_SUFFIX = ".did-project.yaml"
CANONICAL_PROJECT_FILENAME = "project.did-project.yaml"
NOT_NAMES_FILENAME = "not_names.json"
ENTITIES_FILENAME = "entities.yaml"
README_FILENAME = "README.md"
SOURCE_REGISTRY_FILENAME = "sources.json"


class ProjectError(ValueError):
    """Raised when a project file cannot be parsed or validated."""


@dataclass
class Project:
    """Serializable definition of a DID document project."""

    schema_version: int = SCHEMA_VERSION
    project_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Untitled project"
    project_file: Path | None = field(default=None, repr=False, compare=False)
    source_paths: list[Path] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    language: str = "da"
    detection_profile: str = "thorough"
    entity_config_yaml: str = ""
    preview_format: str = "typst"
    export_format: str = "typst"
    export_mode: str = "multi"
    export_destination: Path | None = None
    legacy: bool = field(default=False, repr=False, compare=False)

    @property
    def workdir(self) -> Path | None:
        return self.project_file.parent if self.project_file is not None else None


@dataclass(frozen=True)
class ProjectVersion:
    number: int
    version_id: str
    label: str
    path: Path
    created_at: str = ""
    manifest: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VersionStage:
    number: int
    version_id: str
    label: str
    path: Path
    final_path: Path


def _stored_path(path: Path, base: Path) -> str:
    """Return a portable relative path when ``path`` is below ``base``."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)


def _loaded_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def project_to_data(project: Project, destination: Path) -> dict:
    """Convert ``project`` to its stable on-disk representation."""
    base = destination.parent
    export_destination = project.export_destination
    return {
        "schema_version": project.schema_version,
        "project_id": project.project_id,
        "name": project.name,
        "metadata": project.metadata,
        "sources": [_stored_path(path, base) for path in project.source_paths],
        "processing": {
            "language": project.language,
            "detection_profile": project.detection_profile,
        },
        "draft": {
            "entities": "draft/entities.yaml",
            "not_names": "draft/not_names.json",
        },
        "ui": {"preview_format": project.preview_format},
        "export": {
            "format": project.export_format,
            "mode": project.export_mode,
            "destination": (
                _stored_path(export_destination, base)
                if export_destination is not None
                else None
            ),
        },
    }


def _require_mapping(data, label):
    if not isinstance(data, dict):
        raise ProjectError(f"{label} must be a mapping.")
    return data


def project_from_data(data: dict, path: Path) -> Project:
    """Validate decoded project data and build a :class:`Project`."""
    data = _require_mapping(data, "Project")
    version = data.get("schema_version")
    if version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        raise ProjectError(
            f"Unsupported project schema version {version!r}; "
            f"this version of DID supports {SCHEMA_VERSION}."
        )
    sources = data.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(p, str) for p in sources):
        raise ProjectError("sources must be a list of paths.")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ProjectError("metadata must be a mapping.")
    processing = _require_mapping(data.get("processing", {}), "processing")
    entities = _require_mapping(data.get("entities", {}), "entities")
    ui = _require_mapping(data.get("ui", {}), "ui")
    export = _require_mapping(data.get("export", {}), "export")
    language = processing.get("language", "da")
    if language not in {"da", "en"}:
        raise ProjectError(f"Unsupported project language: {language!r}.")
    mode = export.get("mode", "multi")
    if mode not in {"multi", "single"}:
        raise ProjectError(f"Unsupported export mode: {mode!r}.")
    base = path.parent
    destination = export.get("destination")
    return Project(
        schema_version=version,
        project_id=str(data.get("project_id") or uuid4()),
        name=str(data.get("name") or path.name.removesuffix(PROJECT_SUFFIX)),
        project_file=path,
        source_paths=[_loaded_path(item, base) for item in sources],
        metadata=dict(metadata),
        language=language,
        detection_profile=str(processing.get("detection_profile", "thorough")),
        entity_config_yaml=str(entities.get("yaml") or ""),
        preview_format=str(ui.get("preview_format", "typst")),
        export_format=str(export.get("format", "typst")),
        export_mode=mode,
        export_destination=(
            _loaded_path(destination, base) if isinstance(destination, str) else None
        ),
        legacy=version == LEGACY_SCHEMA_VERSION,
    )


def load_project(path) -> Project:
    """Load and validate a project file."""
    project_path = Path(path).expanduser().resolve()
    try:
        data = YAML(typ="safe").load(project_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectError(f"Could not read project: {exc}") from exc
    except Exception as exc:
        raise ProjectError(f"Invalid project YAML: {exc}") from exc
    project = project_from_data(data, project_path)
    if project.schema_version == SCHEMA_VERSION:
        draft = _require_mapping(data.get("draft", {}), "draft")
        entities_path = _loaded_path(
            str(draft.get("entities", "draft/entities.yaml")), project_path.parent
        )
        try:
            project.entity_config_yaml = (
                entities_path.read_text(encoding="utf-8")
                if entities_path.exists()
                else ""
            )
        except OSError as exc:
            raise ProjectError(f"Could not read draft entities: {exc}") from exc
    return project


def save_project(project: Project, path=None) -> Path:
    """Atomically save a project and return the destination path."""
    requested = path or project.project_file
    if requested is None:
        raise ProjectError("A destination is required for an untitled project.")
    destination = Path(requested)
    if destination.exists() and destination.is_dir():
        destination = destination / CANONICAL_PROJECT_FILENAME
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    (destination.parent / "draft").mkdir(exist_ok=True)
    (destination.parent / "versions").mkdir(exist_ok=True)
    project.schema_version = SCHEMA_VERSION
    project.legacy = False
    yaml = YAML()
    yaml.default_flow_style = False
    stream = io.StringIO()
    yaml.dump(project_to_data(project, destination), stream)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(stream.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, destination)
    except OSError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise ProjectError(f"Could not save project: {exc}") from exc
    project.project_file = destination
    _atomic_write_text(
        destination.parent / "draft" / ENTITIES_FILENAME,
        project.entity_config_yaml,
    )
    exclusions = destination.parent / "draft" / NOT_NAMES_FILENAME
    if not exclusions.exists():
        _atomic_write_text(
            exclusions,
            json.dumps({"schema_version": 1, "not_names": []}, indent=2) + "\n",
        )
    _save_source_registry(project)
    readme = destination.parent / README_FILENAME
    if not readme.exists():
        _atomic_write_text(
            readme,
            "# DID project workdir\n\n"
            "- `project.did-project.yaml`: project metadata and source references\n"
            "- `draft/`: mutable review configuration; may contain identifiers\n"
            "  - `sources.json`: local-only original filenames and paths\n"
            "- `versions/`: immutable exported anonymization versions\n\n"
            "Original source documents are referenced, not copied here.\n",
        )
    _restrict_workdir(destination.parent, destination)
    return destination


def _save_source_registry(project: Project) -> Path:
    """Store the local-only source-name/path audit record in the draft."""
    if project.workdir is None:
        raise ProjectError("Save the project before storing its source registry.")
    path = project.workdir / "draft" / SOURCE_REGISTRY_FILENAME
    existing = {}
    if path.exists():
        try:
            old_data = json.loads(path.read_text(encoding="utf-8"))
            existing = {
                str(item.get("path")): item
                for item in old_data.get("sources", [])
                if isinstance(item, dict) and item.get("path")
            }
        except (OSError, json.JSONDecodeError, AttributeError):
            existing = {}
    now = datetime.now(UTC).isoformat()
    sources = []
    for index, source in enumerate(project.source_paths, 1):
        resolved = source.expanduser().resolve()
        previous = existing.get(str(resolved), {})
        sources.append(
            {
                "id": f"document-{index:03d}",
                "original_name": resolved.name,
                "path": str(resolved),
                "added_at": previous.get("added_at", now),
                "exists": resolved.exists(),
            }
        )
    _atomic_write_text(
        path,
        json.dumps(
            {
                "schema_version": 1,
                "local_only": True,
                "sources": sources,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return path


def create_project_workdir(project: Project, parent, folder_name: str) -> Path:
    """Create and save a canonical project workdir below ``parent``."""
    cleaned = "".join(
        char.lower() if char.isalnum() else "-" for char in folder_name.strip()
    ).strip("-")
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    if not cleaned:
        cleaned = "did-project"
    workdir = Path(parent).expanduser().resolve() / cleaned
    if workdir.exists() and any(workdir.iterdir()):
        raise ProjectError(f"Project directory is not empty: {workdir}")
    workdir.mkdir(parents=True, exist_ok=True)
    return save_project(project, workdir)


def delete_project_workdir(project: Project) -> None:
    """Delete a canonical project workdir, including read-only versions."""
    if (
        project.project_file is None
        or project.project_file.name != CANONICAL_PROJECT_FILENAME
        or project.workdir is None
    ):
        raise ProjectError("Only canonical DID project workdirs can be deleted.")
    workdir = project.workdir.resolve()
    # Published versions are deliberately owner-read-only. Once deletion has
    # been explicitly confirmed, restore owner permissions within this one
    # project so rmtree can unlink their contents.
    _restrict_permissions(workdir)
    try:
        shutil.rmtree(workdir)
    except OSError as exc:
        raise ProjectError(f"Could not delete project workdir: {exc}") from exc


def convert_legacy_project(project: Project, parent, folder_name=None) -> Path:
    """Non-destructively convert a loaded v1 project into a schema-v2 workdir."""
    if not project.legacy:
        raise ProjectError("Only legacy projects need conversion.")
    exclusions = load_not_names(project)
    name = folder_name or project.name
    project.project_file = None
    destination = create_project_workdir(project, parent, name)
    save_not_names(project, exclusions)
    return destination


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def not_names_path(project: Project) -> Path:
    """Return the exclusion file inside the saved project's work directory."""
    if project.project_file is None:
        raise ProjectError("Save the project before storing name exclusions.")
    if project.schema_version >= SCHEMA_VERSION and not project.legacy:
        return project.project_file.parent / "draft" / NOT_NAMES_FILENAME
    return project.project_file.parent / NOT_NAMES_FILENAME


def load_not_names(project: Project) -> list[str]:
    """Load case-insensitively deduplicated name exclusions for ``project``."""
    path = not_names_path(project)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Could not read {NOT_NAMES_FILENAME}: {exc}") from exc
    names = data.get("not_names", []) if isinstance(data, dict) else []
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ProjectError(f"{NOT_NAMES_FILENAME} contains an invalid not_names list.")
    deduplicated = {}
    for name in names:
        cleaned = name.strip()
        if cleaned:
            deduplicated.setdefault(cleaned.casefold(), cleaned)
    return list(deduplicated.values())


def save_not_names(project: Project, names) -> Path:
    """Atomically store name exclusions in the project's work directory."""
    path = not_names_path(project)
    deduplicated = {}
    for name in names:
        cleaned = str(name).strip()
        if cleaned:
            deduplicated.setdefault(cleaned.casefold(), cleaned)
    data = {
        "schema_version": 1,
        "not_names": sorted(deduplicated.values(), key=str.casefold),
    }
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ProjectError(f"Could not save {NOT_NAMES_FILENAME}: {exc}") from exc
    return path


def _version_slug(label: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in label.strip())
    return "-".join(part for part in slug.split("-") if part)[:48]


def list_versions(project: Project) -> list[ProjectVersion]:
    """Return valid completed versions ordered by sequence number."""
    if project.workdir is None:
        return []
    versions_dir = project.workdir / "versions"
    if not versions_dir.exists():
        return []
    versions = []
    for path in versions_dir.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            number = int(manifest["number"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
            continue
        versions.append(
            ProjectVersion(
                number=number,
                version_id=str(manifest.get("version_id", path.name)),
                label=str(manifest.get("label", "")),
                path=path,
                created_at=str(manifest.get("created_at", "")),
                manifest=manifest,
            )
        )
    return sorted(versions, key=lambda version: version.number)


def begin_version(project: Project, label="") -> VersionStage:
    """Allocate a hidden staging directory for the next immutable version."""
    if project.workdir is None:
        raise ProjectError("Save the project before exporting a version.")
    existing = list_versions(project)
    number = max((version.number for version in existing), default=0) + 1
    clean_label = label.strip()
    slug = _version_slug(clean_label)
    version_id = f"v{number:03d}" + (f"-{slug}" if slug else "")
    versions_dir = project.workdir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    final_path = versions_dir / version_id
    if final_path.exists():
        raise ProjectError(f"Version already exists: {final_path}")
    stage_path = versions_dir / f".{version_id}.staging-{uuid4().hex}"
    stage_path.mkdir()
    (stage_path / "output").mkdir()
    shutil.copy2(
        project.workdir / "draft" / ENTITIES_FILENAME,
        stage_path / ENTITIES_FILENAME,
    )
    exclusions = not_names_path(project)
    if exclusions.exists():
        shutil.copy2(exclusions, stage_path / NOT_NAMES_FILENAME)
    return VersionStage(number, version_id, clean_label, stage_path, final_path)


def _file_record(path: Path, base: Path | None = None) -> dict:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path.relative_to(base) if base is not None else path),
        "sha256": digest.hexdigest(),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def finalize_version(
    project: Project,
    stage: VersionStage,
    *,
    processing: dict,
    export: dict,
    app_version: str,
) -> ProjectVersion:
    """Write an audit manifest and atomically publish a staged version."""
    sources = []
    for path in project.source_paths:
        if path.exists() and path.is_file():
            sources.append(_file_record(path))
        else:
            sources.append({"path": str(path), "status": "missing"})
    version_files = [
        _file_record(path, stage.path)
        for path in sorted(stage.path.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    manifest = {
        "schema_version": 1,
        "project_id": project.project_id,
        "number": stage.number,
        "version_id": stage.version_id,
        "label": stage.label,
        "created_at": datetime.now(UTC).isoformat(),
        "app_version": app_version,
        "metadata": project.metadata,
        "processing": processing,
        "export": export,
        "sources": sources,
        "files": version_files,
        "sensitive_files": [
            ENTITIES_FILENAME,
            NOT_NAMES_FILENAME,
            "output/shared_vars.typ",
        ],
    }
    _atomic_write_text(
        stage.path / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    try:
        os.replace(stage.path, stage.final_path)
    except OSError as exc:
        raise ProjectError(f"Could not publish version: {exc}") from exc
    _restrict_permissions(stage.final_path, read_only=True)
    return ProjectVersion(
        stage.number,
        stage.version_id,
        stage.label,
        stage.final_path,
        manifest["created_at"],
        manifest,
    )


def abort_version(stage: VersionStage) -> None:
    shutil.rmtree(stage.path, ignore_errors=True)


def _restrict_permissions(root: Path, *, read_only=False) -> None:
    """Best-effort owner-only permissions for project-controlled sensitive data."""
    try:
        directory_mode = 0o500 if read_only else 0o700
        file_mode = 0o400 if read_only else 0o600
        root.chmod(directory_mode if root.is_dir() else file_mode)
        if root.is_dir():
            for path in root.rglob("*"):
                path.chmod(directory_mode if path.is_dir() else file_mode)
    except OSError:
        pass


def _restrict_workdir(workdir: Path, project_file: Path) -> None:
    """Protect mutable project files without changing archived version modes."""
    try:
        workdir.chmod(0o700)
        for path in (
            project_file,
            workdir / README_FILENAME,
        ):
            if path.exists():
                path.chmod(0o600)
        draft = workdir / "draft"
        if draft.exists():
            _restrict_permissions(draft)
        versions = workdir / "versions"
        if versions.exists():
            versions.chmod(0o700)
    except OSError:
        pass
