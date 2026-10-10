"""Qt-free project-tree facts: sync marks and which version files are documents.

The window used to compute these inside the PySide tree. The web GUI asks the
same questions of the project on disk.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

_ORIGIN_LABELS = {
    "saved_review": "saved review",
    "merged": "saved review merged with new detections",
    "fresh": "fresh detection",
    "edited": "reviewed draft",
}

SYNC_MARK = {
    "synced": "✓",
    "out_of_sync": "↻",
    "pending": "○",
    "missing": "!",
}


def version_document_files(version_path):
    """User-facing version documents, excluding identity support files."""
    output_dir = Path(version_path) / "output"
    hidden = {"config.yaml", "shared_vars.typ", "shared_fakevars.typ"}
    if not output_dir.is_dir():
        return []
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name not in hidden
    )


def source_digest(path, cache: dict):
    """Hash a source once per size/mtime tuple."""
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


def keys_origin_label(origin) -> str:
    return _ORIGIN_LABELS.get(str(origin or ""), str(origin or "unknown"))
