"""In-memory review session for the web GUI.

This is the Qt ``MainWindow`` without the widgets: one open project, its
readings, the review YAML, and the rendered texts. Long work (extract, detect,
export) runs on a background thread. A result whose generation is no longer
current is dropped, so a slow detection cannot overwrite a project the
reviewer has since left.
"""

from __future__ import annotations

import copy
import json
import shutil
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

from did import __version__
from did.core.anonymizer import MODELS_INSTALL_HINT, Anonymizer
from did.core.entity_types import ENTITY_TYPES

from .. import detect_process, pipeline
from .. import handoff as handoff_mod
from ..project import (
    ENTITIES_FILENAME,
    Project,
    ProjectError,
    abort_version,
    begin_version,
    create_project_workdir,
    delete_project_workdir,
    finalize_version,
    list_versions,
    load_not_names,
    load_project,
    save_draft_entities,
    save_not_names,
    save_project,
)
from ..reveal import open_in_file_manager
from ..syncstate import (
    SYNC_MARK,
    draft_sync_state,
    keys_origin_label,
    source_sync_state,
    version_document_files,
)
from .settings import GuiSettings

LANGUAGES = ("da", "en", "sv")
PROFILES = ("thorough", "balanced")
FORMATS = ("typst", "plain", "redacted", "synthetic")
REVIEW_TYPES = tuple(
    entity.config_key
    for entity in ENTITY_TYPES
    if entity.config_key != "DOCUMENT_TITLE"
)
_TYPE_PREFIX = {entity.config_key: entity.prefix for entity in ENTITY_TYPES}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class NeedsAck(ApiError):
    """Export or handoff while the sweep still has findings."""

    def __init__(self, payload: dict):
        super().__init__("Output verification needs a decision.", 409)
        self.payload = payload


def _mark_key(path) -> str:
    path = Path(path)
    try:
        if path.exists():
            return str(path.resolve())
    except OSError:
        pass
    return str(path)


def _project_file(path) -> Path:
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        path = path / "project.did-project.yaml"
    return path


def _collect_additions(paths, existing):
    files, temp_dirs = pipeline.collect_inputs(paths, allow_unknown=True)
    have = {Path(path) for path in existing}
    additions = [path for path in files if path not in have]
    return additions, temp_dirs


def entity_rows(yaml_text: str) -> tuple[list[dict], str | None]:
    """Table rows from review YAML. The second value is a parse error."""
    if not str(yaml_text or "").strip():
        return [], None
    try:
        data = pipeline.read_keys(yaml_text)
    except ValueError as exc:
        return [], str(exc)
    rows = []
    if not isinstance(data, dict):
        return rows, None
    for entity_type, entities in data.items():
        if not isinstance(entities, list) or entity_type.startswith("_"):
            continue
        position = 0
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            position += 1
            variants = [str(item) for item in entity.get("variants") or []]
            entity_id = str(entity.get("id") or "")
            prefix = _TYPE_PREFIX.get(str(entity_type), "")
            confidence = entity.get("confidence")
            rows.append(
                {
                    "id": entity_id,
                    "type": str(entity_type),
                    "number": position,
                    "variant": None,
                    "variants": variants,
                    "confidence": float(confidence) if confidence is not None else None,
                    "token": f"{prefix}{position}",
                    "child": False,
                }
            )
            if entity_type == "PERSON" and len(variants) > 1:
                for index, variant in enumerate(variants, 1):
                    rows.append(
                        {
                            "id": entity_id,
                            "type": str(entity_type),
                            "number": position,
                            "variant": variant,
                            "variant_index": index,
                            "variants": [variant],
                            "confidence": None,
                            "token": f"V{index}",
                            "child": True,
                        }
                    )
    return rows, None


def _finding_payload(report) -> dict:
    if report is None:
        return {"ran": False, "clean": True, "acknowledged": False, "findings": []}
    findings = []
    for finding in (*report.leaks, *report.suspected):
        findings.append(
            {
                "severity": "leak" if finding in report.leaks else "suspect",
                "text": finding.text,
                "document": finding.document,
                "line": finding.line,
            }
        )
    return {
        "ran": True,
        "clean": report.is_clean,
        "summary": report.summary(),
        "acknowledged": False,
        "counts": report.counts(),
        "findings": findings[:12],
        "more": max(0, len(report.leaks) + len(report.suspected) - 12),
    }


class Session:
    def __init__(self, settings: GuiSettings | None = None, *, factory=None):
        self.settings = settings or GuiSettings()
        self.factory = factory or Anonymizer
        self.lock = threading.RLock()
        self.generation = 0
        self.project: Project | None = None
        self.files: list[Path] = []
        self.readings: dict = {}
        self.anonymized: dict = {}
        self.anonymizer = None
        self.yaml_text = ""
        self.language = "da"
        self.detection_profile = "thorough"
        self.output_mode = "typst"
        self.show_original = False
        self.selected: Path | None = None
        self.viewing_version: Path | None = None
        self.draft_view: dict | None = None
        self.verification = None
        self.verification_acknowledged = False
        self.keys_origin = None
        self.busy: dict | None = None
        self.status = "Open a project or create one."
        self.error: str | None = None
        self.temp_dirs: list[Path] = []
        self.auto_version = False
        self.force_merge = False
        self._pending_redetect = False
        self._digest_cache: dict = {}
        self._sync_marks: dict[str, str] = {}
        self._draft_mark: str | None = None
        self._edit_epoch = 0
        self._preview_busy: dict | None = None
        self._preview_ready = threading.Event()
        self._preview_ready.set()
        self._view = None
        self._view_key = None
        self._preview_epoch = 0
        # Process lifetime. Switching projects clears documents, not these, so
        # reopening the same unchanged files skips extraction and detection.
        # Source text stays in this process; it is not written to the project.
        self._extract_cache: dict = {}
        self._detect_cache: dict = {}
        self._settled_cache: dict = {}

    # ---------------------------------------------------------------- jobs ---
    def _start_job(self, message: str, work, apply) -> None:
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.busy = {"message": message, "generation": generation}
            self.status = message
            self.error = None

        def target():
            try:
                result = work()
                error = None
            except SystemExit:
                result = None
                error = (
                    "Detection aborted: a spaCy model is missing and cannot be "
                    f"downloaded. Install with: {MODELS_INSTALL_HINT}"
                )
            except Exception as exc:
                result = None
                error = str(exc)
            # Analysis already finished in work(), off this lock. apply() only
            # stores the result, so a state poll is not waiting on detection.
            with self.lock:
                if generation != self.generation:
                    if self.busy and self.busy.get("generation") == generation:
                        self.busy = None
                    return
                if error:
                    self.auto_version = False
                    self.force_merge = False
                    self.status = "Error."
                    self.error = error
                else:
                    apply(result)
                self._view = None
                self._view_key = None
                if (
                    self.generation == generation
                    and self.busy
                    and self.busy.get("generation") == generation
                ):
                    self.busy = None

        threading.Thread(target=target, name="did-gui", daemon=True).start()

    def _texts(self) -> dict:
        return {path: reading.text for path, reading in self.readings.items()}

    def _not_names(self) -> list[str]:
        if self.project is None or self.project.project_file is None:
            return []
        try:
            return load_not_names(self.project)
        except ProjectError:
            return []

    def _detect(self, texts, language, profile, not_names):
        factory = self.factory
        if factory is Anonymizer:
            from did.core.model_install import ensure_spacy_models

            ensure_spacy_models(profile or "thorough", [language])
            detect_process.ensure_started()
            yaml_str, anonymized, report = detect_process.run_detection(
                texts, language, profile or "thorough", not_names
            )
            anonymizer = Anonymizer.for_regex_only(language, profile or "thorough")
            anonymizer.load_replacements(pipeline.read_keys(yaml_str))
            return anonymizer, yaml_str, anonymized, report
        anonymizer, yaml_str = pipeline.detect_to_yaml(
            texts.values(),
            language,
            detection_profile=profile,
            anonymizer_factory=factory,
        )
        anonymized = pipeline.pseudonymize_all(anonymizer, yaml_str, texts)
        report = pipeline.verify_outputs(
            yaml_str, anonymized, not_names, anonymizer=anonymizer
        )
        return anonymizer, yaml_str, anonymized, report

    def _touch_preview(self) -> None:
        """Drop the cached page payload. Caller holds the lock."""
        self._preview_epoch += 1
        self._view = None
        self._view_key = None

    def _extract_key(self, path):
        path = Path(path)
        try:
            stat = path.stat()
            resolved = str(path.resolve())
        except OSError:
            return None
        return (resolved, stat.st_mtime_ns, stat.st_size)

    def _extract_hit(self, path):
        """A cached reading for this path, or None. Stat only; no byte read."""
        key = self._extract_key(path)
        if key is None:
            return None
        reading = self._extract_cache.get(key)
        if reading is None:
            return None
        if reading.path != Path(path):
            reading = replace(reading, path=Path(path))
        return reading

    def _remember_readings(self, readings) -> None:
        with self.lock:
            for path, reading in readings.items():
                key = self._extract_key(path)
                if key is not None:
                    self._extract_cache[key] = reading

    def _detect_key(self, readings, language, profile, not_names):
        items = []
        for path, reading in readings.items():
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = str(path)
            items.append((resolved, reading.content_hash))
        return (
            tuple(sorted(items)),
            language or "da",
            profile or "thorough",
            tuple(not_names or ()),
        )

    def _settled_hit(self, readings, review, language, profile, not_names):
        """Cached anonymized result for this set, or None.

        A forced re-detect always misses. Detection is cross-document, so a
        partial set is a miss too.
        """
        if self.force_merge or not readings:
            return None
        if any(path not in readings for path in self.files):
            return None
        key = (
            self._detect_key(readings, language, profile, not_names),
            review or "",
            False,
        )
        return self._settled_cache.get(key)

    def _remember_detection(self, result) -> None:
        """Cache a published detection. Caller holds the lock."""
        readings = result.get("readings") or {}
        if not readings or result.get("anonymizer") is None:
            return
        anonymizer = copy.copy(result["anonymizer"])
        detect_key = self._detect_key(
            readings,
            result.get("language"),
            result.get("profile"),
            result.get("not_names"),
        )
        self._detect_cache[detect_key] = (
            anonymizer,
            result.get("yaml_str"),
            result.get("anonymized_fresh"),
            result.get("report_fresh"),
        )
        payload = {
            "anonymizer": anonymizer,
            "settled": result["settled"],
            "marks": result.get("marks") or {},
            "draft_mark": result.get("draft_mark"),
            "cache": result.get("cache") or {},
        }
        review = result.get("review") or ""
        force = bool(result.get("force"))
        self._settled_cache[(detect_key, review, force)] = payload
        produced = (
            (result["settled"].get("yaml") or "") if result.get("settled") else ""
        )
        if produced != review:
            self._settled_cache[(detect_key, produced, False)] = payload

    def _refresh_cached_marks(self) -> None:
        """Keep cached tree marks aligned with the latest measured sync state.

        Detection is cached before the automatic version write, so the first
        stored marks say the sources are still pending. The export job measures
        again once that version exists.
        """
        if not self.readings:
            return
        try:
            detect_key = self._detect_key(
                self.readings,
                self.language,
                self.detection_profile,
                self._not_names(),
            )
        except (OSError, AttributeError):
            return
        marks = dict(self._sync_marks)
        cache = dict(self._digest_cache)
        for key, payload in self._settled_cache.items():
            if key[0] == detect_key:
                payload["marks"] = marks
                payload["draft_mark"] = self._draft_mark
                payload["cache"] = cache

    def _use_settled(self, readings, hit) -> None:
        """Show a cached anonymized result. Caller holds the lock. No detection."""
        self.readings = dict(readings)
        self._digest_cache.update(hit.get("cache") or {})
        self._sync_marks = dict(hit.get("marks") or {})
        self._publish_settled(hit["anonymizer"], hit["settled"])

    def _read_documents(self, paths):
        """Read paths off the GUI thread and cache the readings.

        The real anonymizer reads in the child process. The scripted stand-in
        stays in this worker so tests do not spawn a process.
        """
        if self.factory is Anonymizer:
            detect_process.ensure_started()
            raw = detect_process.run_job(
                detect_process.extract_documents,
                ([str(path) for path in paths],),
            )
            readings = {Path(key): value for key, value in raw.items()}
        else:
            readings = {Path(path): pipeline.extract_one(path) for path in paths}
        self._remember_readings(readings)
        return readings

    def _analyze(
        self,
        anonymizer,
        yaml_str,
        anonymized,
        report,
        review,
        readings,
        texts,
        not_names,
        force,
        language,
        profile,
    ):
        """Merge and re-pseudonymize off the GUI thread. The real path is the child."""
        if self.factory is Anonymizer:
            detect_process.ensure_started()
            return detect_process.run_job(
                detect_process.settle_detected,
                (
                    language,
                    profile,
                    yaml_str,
                    anonymized,
                    report,
                    review,
                    readings,
                    texts,
                    list(not_names),
                    bool(force),
                ),
            )
        return pipeline.settle_detection(
            anonymizer,
            yaml_str,
            anonymized,
            report,
            review,
            readings,
            texts,
            not_names,
            force,
        )

    def _draft_only(self) -> str:
        """Sync mark from the project already in memory. Source hashes must already be cached."""
        if self.project is None:
            return "pending"
        try:
            versions = list_versions(self.project)
        except ProjectError:
            return "pending"
        latest = versions[-1] if versions else None
        try:
            return draft_sync_state(self.project, latest, self._digest_cache)
        except ProjectError:
            return "pending"

    def _measure_sync(self, project, files):
        """Hash sources for the tree marks. Worker only; the page never calls this.

        The returned cache is merged by the caller under the session lock. This
        method does not write ``self._digest_cache``, so two workers can measure
        at once. With the real anonymizer the bytes are read in the child, where
        they cannot hold this process's GIL.
        """
        with self.lock:
            cache = dict(self._digest_cache)
        if self.factory is Anonymizer and files:
            detect_process.ensure_started()
            extra = detect_process.run_job(
                detect_process.hash_sources, ([str(path) for path in files],)
            )
            if isinstance(extra, dict):
                cache.update(extra)
        latest = None
        if project is not None and project.project_file is not None:
            try:
                versions = list_versions(project)
            except ProjectError:
                versions = []
            latest = versions[-1] if versions else None
        marks = {}
        for source in files:
            key = _mark_key(source)
            marks[key] = source_sync_state(source, latest, cache)
        draft = "pending"
        if project is not None:
            try:
                draft = draft_sync_state(project, latest, cache)
            except ProjectError:
                draft = "pending"
        return marks, draft, cache

    def _publish_settled(self, anonymizer, settled) -> None:
        """Store a finished analysis. Caller holds the lock. No detection here."""
        self.force_merge = False
        self.anonymizer = anonymizer
        self.yaml_text = settled["yaml"]
        self.anonymized = settled["anonymized"]
        self.keys_origin = settled["origin"]
        self._set_verification(settled["report"])
        if self.project is not None:
            self.project.entity_config_yaml = settled["yaml"]
            self.project.source_paths = list(self.files)
            if self.project.project_file is not None:
                save_draft_entities(self.project, settled["yaml"])
                save_project(self.project)
        self.status = settled["status"]
        if self.selected not in self.files:
            self.selected = self.files[0] if self.files else None
        self._draft_mark = self._draft_only()
        self._touch_preview()
        if self.auto_version:
            self.auto_version = False
            try:
                self._export(self.project.export_mode or "multi", "document_import")
            except (ApiError, ProjectError) as exc:
                self.status = str(exc)
                self.error = str(exc)

    def _finish_detection(
        self,
        anonymizer,
        yaml_str,
        anonymized,
        report,
        readings,
        texts,
        review,
        language,
        profile,
    ):
        """Settle against the review as it stands now, then pack a publishable result."""
        while True:
            with self.lock:
                epoch = self._edit_epoch
                review_now = self.yaml_text or review
                force = self.force_merge
                names = self._not_names()
                project = self.project
                files = list(readings) or list(self.files)
            settled = self._analyze(
                anonymizer,
                yaml_str,
                anonymized,
                report,
                review_now,
                readings,
                texts,
                names,
                force,
                language,
                profile,
            )
            with self.lock:
                if self._edit_epoch == epoch:
                    break
        try:
            anonymizer.load_replacements(pipeline.read_keys(settled["yaml"]))
        except (ValueError, AttributeError):
            pass
        marks, draft_mark, cache = self._measure_sync(project, files)
        return {
            "readings": readings,
            "anonymizer": anonymizer,
            "yaml_str": yaml_str,
            "anonymized_fresh": anonymized,
            "report_fresh": report,
            "settled": settled,
            "marks": marks,
            "draft_mark": draft_mark,
            "cache": cache,
            "epoch": epoch,
            "review": review_now,
            "force": force,
            "language": language,
            "profile": profile,
            "not_names": list(names),
        }

    def _load_paths(self, paths, review: str) -> None:
        """Show source text as soon as it is read, then anonymize it.

        A full cache hit publishes the anonymized text on this thread (a dict
        lookup). A miss reads in a worker, shows the source with an UNANON
        marker, and detects after that. spaCy stays off this thread.
        """
        paths = [Path(path) for path in paths]
        with self.lock:
            self.generation += 1
            self.busy = None
            self.error = None
            self.files = list(paths)
            if self.selected not in self.files:
                self.selected = self.files[0] if self.files else None
            fresh = {}
            missing = []
            for path in paths:
                hit = self._extract_hit(path)
                if hit is None:
                    missing.append(path)
                else:
                    fresh[path] = hit
            self.readings = fresh
            self.anonymized = {}
            self.anonymizer = None
            self.verification = None
            self.verification_acknowledged = False
            self._touch_preview()
            if not missing:
                hit = self._settled_hit(
                    fresh,
                    self.yaml_text or review,
                    self.language,
                    self.detection_profile,
                    self._not_names(),
                )
                if hit is not None:
                    self._use_settled(fresh, hit)
                    return
        if missing:
            self._start_extract(missing, review)
            return
        self._start_detect()

    def _start_extract(self, missing, review: str) -> None:
        def work():
            return self._read_documents(missing)

        def apply(result):
            for path, reading in result.items():
                self.readings[Path(path)] = reading
            self._touch_preview()
            if any(path not in self.readings for path in self.files):
                self.status = "Could not read every document."
                return
            hit = self._settled_hit(
                self.readings,
                self.yaml_text or review,
                self.language,
                self.detection_profile,
                self._not_names(),
            )
            if hit is not None:
                self._use_settled(self.readings, hit)
                return
            # The new job bumps generation, so this job does not clear its busy flag.
            self._start_detect()

        self._start_job("Reading documents…", work, apply)

    def _start_detect(self) -> None:
        with self.lock:
            if not self.readings or any(
                path not in self.readings for path in self.files
            ):
                return
            readings = dict(self.readings)
            texts = self._texts()
            review = self.yaml_text
            language = self.language
            profile = self.detection_profile
            not_names = self._not_names()
            force = self.force_merge
            cached = None
            if not force:
                cached = self._detect_cache.get(
                    self._detect_key(readings, language, profile, not_names)
                )

        def work():
            if cached is None:
                anonymizer, yaml_str, anonymized, report = self._detect(
                    texts, language, profile, not_names
                )
            else:
                anonymizer, yaml_str, anonymized, report = cached
                anonymizer = copy.copy(anonymizer)
            return self._finish_detection(
                anonymizer,
                yaml_str,
                anonymized,
                report,
                readings,
                texts,
                review,
                language,
                profile,
            )

        def apply(result):
            self._digest_cache.update(result.get("cache") or {})
            self._sync_marks = result["marks"]
            self._draft_mark = result["draft_mark"]
            if result["epoch"] != self._edit_epoch:
                self.status = "Detection finished during a review edit."
                return
            self._remember_detection(result)
            self._publish_settled(result["anonymizer"], result["settled"])

        self._start_job("Detecting entities…", work, apply)

    # --------------------------------------------------------------- verify ---
    def _set_verification(self, report) -> None:
        self.verification = report
        self.verification_acknowledged = False

    def _verification_status(self, message: str) -> str:
        report = self.verification
        if report is None or report.is_clean:
            return message
        return f"{message}  {report.summary()}"

    def _verification_record(self) -> dict:
        report = self.verification
        if report is None:
            return {"status": "not_run"}
        return {
            "status": "clean" if report.is_clean else "findings",
            "acknowledged": self.verification_acknowledged,
            **report.counts(),
        }

    def _require_ack(self, action: str) -> None:
        report = self.verification
        if report is None or report.is_clean or self.verification_acknowledged:
            return
        payload = _finding_payload(report)
        payload["acknowledged"] = False
        payload["action"] = action
        payload["needs_ack"] = True
        raise NeedsAck(payload)

    def acknowledge(self) -> None:
        with self.lock:
            self.verification_acknowledged = True
            self.status = "Verification acknowledged for this output."

    # -------------------------------------------------------------- display ---
    def _display_text(self, path) -> str | None:
        text = self.anonymized.get(path)
        if text is None:
            return None
        if self.output_mode == "plain":
            return pipeline.to_written_out(text)
        if self.output_mode == "redacted":
            return pipeline.to_redacted(text)
        if self.output_mode == "synthetic":
            seed = self.project.project_id if self.project is not None else "did"
            return pipeline.to_synthetic(text, self.yaml_text, self.language, seed)
        return text

    def _version_sources(self, path: Path):
        state = self.draft_view or {}
        candidates = [Path(item) for item in state.get("files") or []]
        if not candidates and self.project is not None:
            candidates = [Path(item) for item in self.project.source_paths]
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
        source = Path(source)
        draft = (self.draft_view or {}).get("readings") or {}
        return draft.get(source) or self.readings.get(source)

    def _original_text(self, path: Path) -> str | None:
        if self.viewing_version is None:
            reading = self.readings.get(path)
            return reading.text if reading is not None else None
        sources = self._version_sources(path)
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

    def _preview(self) -> dict:
        path = self.selected if self.selected in self.files else None
        if path is None:
            return {
                "text": "",
                "state": "EMPTY",
                "name": "",
                "original": False,
                "can_original": False,
            }
        original = self._original_text(path)
        can_original = original is not None or (
            self.viewing_version is not None and bool(self._version_sources(path))
        )
        if self.show_original and original is not None:
            return {
                "text": original,
                "state": "RAW",
                "name": path.name,
                "original": True,
                "can_original": True,
            }
        rendered = self._display_text(path)
        if rendered is not None:
            if self.viewing_version is not None:
                state = "VERSION"
            else:
                state = {
                    "redacted": "REDACTED",
                    "synthetic": "SYNTHETIC",
                }.get(self.output_mode, "PSEUDONYMIZED")
            return {
                "text": rendered,
                "state": state,
                "name": path.name,
                "original": False,
                "can_original": can_original,
            }
        reading = self.readings.get(path)
        if reading is not None and self.viewing_version is None:
            # Source text is on screen, and it is not anonymized yet. The
            # Original toggle stays off; UNANON is only a marker.
            return {
                "text": reading.text,
                "state": "UNANON",
                "name": path.name,
                "original": False,
                "can_original": True,
            }
        return {
            "text": "",
            "state": "EMPTY",
            "name": path.name,
            "original": False,
            "can_original": can_original,
        }

    # ----------------------------------------------------------------- tree ---
    def _document_mode(self, source, anonymized, extracted, sync_state) -> str:
        if source in anonymized:
            return "PSEUDO"
        if source in extracted:
            return "UNANON"
        if sync_state == "synced":
            return "PSEUDO"
        return "PENDING"

    def _project_node(self, project: Project, *, active: bool) -> dict:
        versions = []
        try:
            versions = list_versions(project)
        except ProjectError:
            versions = []
        latest = versions[-1] if versions else None
        if active:
            project.entity_config_yaml = self.yaml_text or project.entity_config_yaml
        missing_source = any(
            not Path(source).exists() for source in project.source_paths
        )
        if missing_source and latest is not None:
            sync = "out_of_sync"
        elif active and self._draft_mark:
            sync = self._draft_mark
        else:
            sync = "pending"
        anonymized = self.anonymized if active and self.viewing_version is None else {}
        extracted = self.readings if active else {}
        if active and self.viewing_version is not None and self.draft_view:
            anonymized = self.draft_view.get("anonymized") or {}
            extracted = self.draft_view.get("readings") or {}
        documents = []
        for source in project.source_paths:
            source = Path(source)
            source_sync = self._stored_source_mark(source, latest if active else None)
            documents.append(
                {
                    "path": str(source),
                    "name": source.name,
                    "sync": source_sync,
                    "mark": SYNC_MARK.get(source_sync, ""),
                    "mode": self._document_mode(
                        source, anonymized, extracted, source_sync
                    ),
                    "selected": active
                    and self.viewing_version is None
                    and source == self.selected,
                    "missing": not source.exists(),
                }
            )
        output = None
        if latest is not None:
            has_fake = (latest.path / "output" / "shared_fakevars.typ").exists()
            mode = "PSEUDO/FAKE" if has_fake else "PSEUDO"
            files = []
            for output_path in version_document_files(latest.path):
                files.append(
                    {
                        "path": str(output_path),
                        "name": output_path.name,
                        "mode": mode,
                        "selected": active
                        and self.viewing_version == latest.path
                        and output_path == self.selected,
                    }
                )
            output = {
                "path": str(latest.path),
                "mode": mode,
                "mark": SYNC_MARK["synced"],
                "created_at": latest.created_at,
                "files": files,
            }
        try:
            excluded = load_not_names(project) if project.project_file else []
        except ProjectError:
            excluded = []
        parent = project.workdir.parent.name if project.workdir is not None else ""
        return {
            "path": str(project.project_file) if project.project_file else "",
            "name": project.name,
            "parent": parent,
            "active": active,
            "sync": sync,
            "mark": SYNC_MARK.get(sync, ""),
            "documents": documents,
            "output": output,
            "excluded": excluded,
            "metadata": project.metadata,
            "language": project.language,
        }

    def _recent_nodes(self) -> list[dict]:
        nodes = []
        active_path = (
            self.project.project_file.resolve()
            if self.project and self.project.project_file
            else None
        )
        ordered: list[Path] = []
        if active_path is not None and active_path.is_file():
            ordered.append(active_path)
        recent_text = []
        for raw in self.settings.recent_projects():
            path = _project_file(raw)
            recent_text.append(str(path))
            if path not in ordered:
                ordered.append(path)
        kept = []
        for path in ordered:
            if not path.is_file():
                continue
            if str(path) in recent_text:
                kept.append(str(path))
            if active_path is not None and path == active_path:
                nodes.append(self._project_node(self.project, active=True))
                continue
            try:
                nodes.append(self._project_node(load_project(path), active=False))
            except ProjectError:
                continue
        if kept != self.settings.recent:
            self.settings.recent = kept
            self.settings._save()
        names: dict[str, int] = {}
        for node in nodes:
            names[node["name"]] = names.get(node["name"], 0) + 1
        for node in nodes:
            if names[node["name"]] > 1 and node["parent"]:
                node["label"] = f"{node['name']} ({node['parent']})"
            else:
                node["label"] = node["name"]
        return nodes

    def _stored_source_mark(self, source, latest) -> str:
        """A tree mark that does not read the document. Hashing happens on the worker."""
        source = Path(source)
        if not source.exists():
            return "missing"
        stored = self._sync_marks.get(_mark_key(source))
        if stored and stored != "missing":
            return stored
        if latest is None:
            return "pending"
        return stored or "pending"

    def _keys_location(self) -> tuple[str, str]:
        if self.viewing_version is not None:
            folder = self.viewing_version
            return str(folder / ENTITIES_FILENAME), str(folder)
        if self.project is not None and self.project.workdir is not None:
            folder = self.project.workdir / "draft"
            return str(folder / ENTITIES_FILENAME), str(folder)
        return "", ""

    def _keys_bar(self) -> dict:
        count = pipeline.count_identities(self.yaml_text)
        path, folder = self._keys_location()
        base = {"count": count, "path": path, "folder": folder}
        if self.project is None:
            base["label"] = "Keys: none yet — add documents to detect entities."
            return base
        if self.viewing_version is not None:
            base["label"] = (
                f"Keys: {ENTITIES_FILENAME} · {count} identities · read-only"
            )
            return base
        origin = keys_origin_label(self.keys_origin) if self.keys_origin else ""
        parts = [f"Keys: draft/{ENTITIES_FILENAME}", f"{count} identities"]
        if origin:
            parts.append(origin)
        base["label"] = " · ".join(parts)
        base["origin"] = self.keys_origin
        return base

    def state(self) -> dict:
        with self.lock:
            # Busy and status change while a job runs. The rest of the payload
            # is cached so a poll does not hash documents or rebuild the review.
            if self._view is None or self._view_key != self._view_fingerprint():
                self._view = self._compose_state()
                self._view_key = self._view_fingerprint()
            payload = dict(self._view)
            payload["busy"] = self.busy or self._preview_busy
            payload["status"] = self.status
            payload["error"] = self.error
            return payload

    def _source_presence(self):
        """Stat sources so a deleted file drops the cached tree. No byte reads."""
        paths = []
        if self.project is not None:
            paths.extend(self.project.source_paths)
        for path in self.files:
            if path not in paths:
                paths.append(path)
        presence = []
        for path in paths:
            path = Path(path)
            try:
                presence.append((str(path), path.exists()))
            except OSError:
                presence.append((str(path), False))
        return tuple(presence)

    def _view_fingerprint(self):
        project_id = None
        if self.project is not None and self.project.project_file is not None:
            project_id = str(self.project.project_file)
        return (
            project_id,
            self.project.name if self.project else "",
            tuple(self.settings.recent),
            tuple(str(path) for path in self.files),
            self._source_presence(),
            str(self.selected),
            self.show_original,
            self.output_mode,
            str(self.viewing_version),
            self.yaml_text,
            self.language,
            self.detection_profile,
            self._draft_mark,
            tuple(sorted(self._sync_marks.items())),
            self._edit_epoch,
            self.verification_acknowledged,
            id(self.verification),
            self.keys_origin,
            self._preview_epoch,
            tuple(sorted((self.project.metadata if self.project else {}).items())),
            self._handoff_mark(),
        )

    def _compose_state(self) -> dict:
        rows, entity_error = entity_rows(self.yaml_text)
        verification = _finding_payload(self.verification)
        verification["acknowledged"] = self.verification_acknowledged
        return {
            "projects": self._recent_nodes(),
            "active": (
                str(self.project.project_file)
                if self.project and self.project.project_file
                else None
            ),
            "project_name": self.project.name if self.project else "",
            "workdir": (
                str(self.project.workdir.resolve())
                if self.project is not None and self.project.workdir is not None
                else None
            ),
            "preview": self._preview(),
            "entities": rows,
            "entity_error": entity_error,
            "language": self.language,
            "profile": self.detection_profile,
            "format": self.output_mode,
            "languages": list(LANGUAGES),
            "profiles": list(PROFILES),
            "formats": list(FORMATS),
            "review_types": list(REVIEW_TYPES),
            "busy": self.busy,
            "status": self.status,
            "error": self.error,
            "verification": verification,
            "keys": self._keys_bar(),
            "viewing_version": (
                str(self.viewing_version) if self.viewing_version else None
            ),
            "read_only": self.viewing_version is not None,
            "metadata": self.project.metadata if self.project else {},
            "handoff": self._handoff_state(),
            "version": __version__,
            "directories": {
                "projects": self.settings.directory("projects"),
                "sources": self.settings.directory("sources"),
            },
        }

    # -------------------------------------------------------------- handoff ---
    def _handoff_docs(self) -> list[tuple[str, str, str]]:
        """(source key, title token, token-only body) for every pseudonymized document."""
        return [
            (str(path), self._title_token(index), self.anonymized[path])
            for index, path in enumerate(self._llm_paths(), 1)
        ]

    def _handoff_mark(self):
        if self.project is None or self.project.workdir is None:
            return None
        try:
            return (self.project.workdir / "draft" / handoff_mod.STATE).stat().st_mtime
        except OSError:
            return 0.0

    def _handoff_state(self) -> dict:
        """What the window (and the workbench badge) shows about the evid handoff."""
        if self.project is None or self.project.workdir is None:
            return {"set": "", "pending": 0, "at": 0, "handed": 0}
        state = handoff_mod.read_state(self.project.workdir)
        docs = self._handoff_docs()
        return {
            "set": state["set"] or (self.project.metadata or {}).get("evid_set", ""),
            "at": state["at"],
            "handed": len(state["docs"]),
            "pending": handoff_mod.pending(self.project.workdir, docs),
        }

    def handoff(self, set_slug: str = "") -> dict:
        """Render the pseudonymized documents as placeholder PDFs and put them in evid."""
        self._wait_until_preview()
        with self.lock:
            if self.project is None or self.project.workdir is None:
                raise ApiError("Open or save a project before handing off.")
            if self.viewing_version is not None:
                raise ApiError("Return to the draft before handing off.")
            slug = (set_slug or "").strip() or handoff_mod.read_state(
                self.project.workdir
            )["set"]
            slug = slug or (self.project.metadata or {}).get("evid_set", "")
            if not slug:
                raise ApiError(
                    "Which evid set? Give the case's anon set, e.g. 2026-014-anon."
                )
            docs = self._handoff_docs()
            if not docs:
                raise ApiError("Nothing pseudonymized to hand off.")
            if not self._publishable():
                raise ApiError(self.status)
            self._require_ack("Hand off to evid")
            workdir, yaml_text, language = (
                self.project.workdir,
                self.yaml_text,
                self.language,
            )

        def work():
            return handoff_mod.handoff(workdir, docs, yaml_text, slug, language)

        def apply(result):
            stale = (
                f", {result['stale']} label(s) to re-anchor"
                if result.get("stale")
                else ""
            )
            self.status = (
                f"Handed off to evid {result['set']}: {result['added']} added, "
                f"{result['replaced']} replaced, {result['unchanged']} unchanged{stale}."
            )
            self.error = None

        self._start_job(f"Handing off {len(docs)} document(s) to evid…", work, apply)
        return {"ok": True}

    def ensure_project(self, path: str, name: str = "", evid_set: str = "") -> None:
        """Open the project at PATH, or create one right there when PATH is empty.

        The workbench gives every case its own did folder and opens it with this, so the
        window always shows that case's project and nothing else."""
        target = Path(path).expanduser().resolve()
        if not str(path).strip():
            raise ApiError("A project path is required.")
        active = (
            self.project.workdir.resolve()
            if self.project is not None and self.project.workdir is not None
            else None
        )
        if active != target:
            if _project_file(target).is_file():
                self.open_project(str(target))
            else:
                if target.exists() and any(target.iterdir()):
                    raise ApiError(f"{target} is not empty and holds no did project.")
                target.mkdir(parents=True, exist_ok=True)
                with self.lock:
                    self.generation += 1
                    self.busy = None
                    self._reset_documents()
                    project = Project(
                        name=(name or target.name).strip() or target.name,
                        language=self.language or "da",
                        detection_profile=self.detection_profile,
                    )
                    try:
                        save_project(project, target)
                    except ProjectError as exc:
                        raise ApiError(str(exc)) from exc
                    self.project = project
                    self.output_mode = project.preview_format or "typst"
                    self.settings.add_recent_project(project.project_file)
                    self.status = f"{project.name} — add documents to begin."
                    self.error = None
        if evid_set and self.project is not None:
            meta = dict(self.project.metadata or {})
            if meta.get("evid_set") != evid_set:
                meta["evid_set"] = evid_set
                self.set_meta(meta)

    # ------------------------------------------------------------- projects ---
    def _reset_documents(self) -> None:
        self.files = []
        self.readings = {}
        self.anonymized = {}
        self.anonymizer = None
        self.yaml_text = ""
        self.selected = None
        self.viewing_version = None
        self.draft_view = None
        self.verification = None
        self.verification_acknowledged = False
        self.keys_origin = None
        self.show_original = False
        self.auto_version = False
        self.force_merge = False
        self._sync_marks = {}
        self._draft_mark = None
        self._edit_epoch += 1
        self._preview_busy = None
        self._preview_ready.set()
        self._touch_preview()

    def new_project(self, name: str, parent: str, language: str | None = None) -> None:
        name = (name or "Untitled project").strip() or "Untitled project"
        if not parent:
            raise ApiError("Choose a parent directory for the project.")
        language = language or self.language or "da"
        if language not in LANGUAGES:
            raise ApiError(f"Unsupported language: {language}")
        with self.lock:
            self.generation += 1
            self.busy = None
            self._reset_documents()
            self.language = language
            project = Project(
                name=name,
                language=language,
                detection_profile=self.detection_profile,
            )
            try:
                create_project_workdir(project, parent, name)
            except ProjectError as exc:
                self.project = None
                raise ApiError(str(exc)) from exc
            self.project = project
            self.output_mode = project.preview_format or "typst"
            self.settings.add_recent_project(project.project_file)
            self.settings.set_directory("projects", parent)
            self.status = f"{name} — add documents to begin."
            self.error = None

    def open_project(self, path: str) -> None:
        try:
            project = load_project(_project_file(path))
        except ProjectError as exc:
            raise ApiError(str(exc)) from exc
        with self.lock:
            self.generation += 1
            self.busy = None
            self._reset_documents()
            self.project = project
            self.language = project.language or "da"
            self.detection_profile = project.detection_profile or "thorough"
            self.output_mode = project.preview_format or "typst"
            self.yaml_text = project.entity_config_yaml or ""
            if project.project_file is not None:
                self.settings.add_recent_project(project.project_file)
            paths = [Path(item) for item in project.source_paths]
            review = self.yaml_text
            self.status = f"Opened {project.name}."
            self.error = None
        if paths:
            self.auto_version = False
            self._load_paths(paths, review)

    def rename_project(self, name: str) -> None:
        name = (name or "").strip()
        with self.lock:
            if self.project is None:
                raise ApiError("No project is open.")
            if not name or name == self.project.name:
                return
            self.project.name = name
            if self.project.project_file is not None:
                save_project(self.project)
            self.status = f"Project renamed to {name}."

    def remove_project(self, path: str) -> None:
        """Drop a project from the list. The workdir stays on disk."""
        target = _project_file(path)
        with self.lock:
            self.settings.remove_recent_project(target)
            if (
                self.project
                and self.project.project_file
                and self.project.project_file.resolve() == target
            ):
                self.generation += 1
                self.project = None
                self._reset_documents()
                self.busy = None
            self.status = "Removed the project from the list. The workdir is unchanged."

    def delete_project(self, path: str, *, confirm: bool) -> None:
        if not confirm:
            raise ApiError("Deleting a project workdir needs confirm=true.", 409)
        target = _project_file(path)
        try:
            project = load_project(target)
        except ProjectError as exc:
            raise ApiError(str(exc)) from exc
        with self.lock:
            delete_project_workdir(project)
            self.settings.remove_recent_project(target)
            if (
                self.project
                and self.project.project_file
                and self.project.project_file.resolve() == target
            ):
                self.generation += 1
                self.project = None
                self._reset_documents()
                self.busy = None
            self.status = "Deleted the project workdir."

    def set_meta(self, metadata: dict) -> None:
        if not isinstance(metadata, dict):
            raise ApiError("metadata must be an object.")
        with self.lock:
            if self.project is None:
                raise ApiError("No project is open.")
            self.project.metadata = {
                str(key): value
                for key, value in metadata.items()
                if value not in (None, "", [])
            }
            if self.project.project_file is not None:
                self.project.entity_config_yaml = self.yaml_text
                save_project(self.project)
            self.status = "Project meta saved and included in LLM handoffs."

    # ------------------------------------------------------------ documents ---
    def add_documents(self, paths) -> None:
        if not paths:
            raise ApiError("No documents were given.")
        with self.lock:
            if self.project is None:
                raise ApiError("Create or open a project before adding documents.")
            if self.viewing_version is not None:
                raise ApiError("Return to the draft before adding documents.")
            existing = list(self.files)
            review = self.yaml_text
        additions, temp_dirs = _collect_additions(paths, existing)
        if not additions:
            with self.lock:
                self.status = "No new supported documents found."
            return
        with self.lock:
            self.temp_dirs.extend(temp_dirs)
            for path in additions:
                if path not in self.project.source_paths:
                    self.project.source_paths.append(path)
            if self.project.project_file is not None:
                self.project.entity_config_yaml = self.yaml_text
                save_project(self.project)
            self.auto_version = True
            files = [*existing, *additions]
            parent = additions[0].parent
        self.settings.set_directory("sources", parent)
        self._load_paths(files, review)

    def remove_document(self, path: str) -> None:
        target = Path(path).expanduser().resolve()
        with self.lock:
            if self.project is None:
                raise ApiError("No project is open.")
            self.project.source_paths = [
                item
                for item in self.project.source_paths
                if Path(item).resolve() != target
            ]
            self.files = [item for item in self.files if Path(item).resolve() != target]
            self.readings.pop(target, None)
            self.anonymized.pop(target, None)
            if self.selected is not None and Path(self.selected).resolve() == target:
                self.selected = self.files[0] if self.files else None
            if self.project.project_file is not None:
                save_project(self.project)
            self.status = f"Removed {target.name}."
            review = self.yaml_text
            files = list(self.files)
        if files:
            self.auto_version = True
            self.force_merge = True
            self._load_paths(files, review)

    def select_document(self, path: str, *, version: str | None = None) -> None:
        pending = False
        with self.lock:
            if version:
                self._view_version(Path(version))
            elif self.viewing_version is not None:
                self._return_to_draft()
                pending = self._pending_redetect
                self._pending_redetect = False
            target = Path(path).expanduser()
            match = next(
                (item for item in self.files if item == target or str(item) == path),
                None,
            )
            if match is None:
                raise ApiError("That document is not in the open project.", 404)
            self.selected = match
            self.show_original = False
        if pending:
            self._start_detect()

    def set_preview(self, *, format=None, original=None) -> None:
        with self.lock:
            if format:
                if format not in FORMATS:
                    raise ApiError(f"Unsupported preview format: {format}")
                self.output_mode = format
                if self.project is not None:
                    self.project.preview_format = format
                    if self.project.project_file is not None:
                        self.project.entity_config_yaml = self.yaml_text
                        save_project(self.project)
            if original is not None:
                self.show_original = bool(original)

    def set_settings(self, *, language=None, profile=None) -> None:
        redetect = False
        with self.lock:
            if language and language != self.language:
                if language not in LANGUAGES:
                    raise ApiError(f"Unsupported language: {language}")
                self.language = language
                if self.project is not None:
                    self.project.language = language
                    if self.project.project_file is not None:
                        self.project.entity_config_yaml = self.yaml_text
                        save_project(self.project)
                redetect = True
            if profile and profile != self.detection_profile:
                if profile not in PROFILES:
                    raise ApiError(f"Unsupported detection profile: {profile}")
                self.detection_profile = profile
                if self.project is not None:
                    self.project.detection_profile = profile
                    if self.project.project_file is not None:
                        self.project.entity_config_yaml = self.yaml_text
                        save_project(self.project)
                redetect = True
            if self.viewing_version is not None and redetect:
                self.status = "Saved — the draft is re-detected when you return to it."
                self.force_merge = True
                return
            if redetect and self.readings:
                self.force_merge = True
                self.status = "Setting changed — re-detecting (review is kept)…"
            elif redetect:
                self.status = "Setting saved with the project."
                redetect = False
        if redetect:
            self._start_detect()

    # --------------------------------------------------------------- review ---
    def _wait_until_preview(self) -> None:
        """Block until a review edit has published, without holding the lock."""
        while True:
            with self.lock:
                ready = self._preview_ready
                if self._preview_busy is None:
                    return
            if not ready.wait(120):
                raise ApiError("The preview is still updating.")

    def _rewrite(self, anonymizer, yaml_text, texts, names, language, profile):
        """Pseudonymize and verify away from the request thread.

        The real anonymizer does this in the child so the rewrite cannot hold
        this process's GIL. The scripted stand-in stays here for tests.
        """
        if self.factory is Anonymizer:
            detect_process.ensure_started()
            return detect_process.run_job(
                detect_process.rewrite_with_keys,
                (language, profile, yaml_text, texts, list(names)),
            )
        anonymized = pipeline.pseudonymize_all(anonymizer, yaml_text, texts)
        report = pipeline.verify_outputs(yaml_text, anonymized, names)
        return anonymized, report

    def _apply_yaml(self, changed: str, status: str) -> None:
        """Store a review edit. The caller holds the lock.

        The YAML and the dropped verification ack are visible before this
        returns. Pseudonymize and verify run after the request, off this thread.
        """
        self._edit_epoch += 1
        epoch = self._edit_epoch
        generation = self.generation
        self.yaml_text = changed
        if self.project is not None:
            self.project.entity_config_yaml = changed
            if self.project.project_file is not None:
                save_draft_entities(self.project, changed)
        self.keys_origin = "edited"
        self.verification_acknowledged = False
        self.status = status
        self._view = None
        self._view_key = None
        if self.anonymizer is None or not self.readings:
            self._preview_busy = None
            self._preview_ready.set()
            return
        try:
            if str(changed).strip():
                pipeline.read_keys(changed)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        texts = dict(self._texts())
        anonymizer = self.anonymizer
        names = list(self._not_names())
        language = self.language
        profile = self.detection_profile
        self._preview_ready.clear()
        self._preview_busy = {"message": "Updating the preview…", "epoch": epoch}

        def work():
            try:
                anonymized, report = self._rewrite(
                    anonymizer, changed, texts, names, language, profile
                )
                error = None
            except Exception as exc:
                anonymized = None
                report = None
                error = str(exc)
            with self.lock:
                if self._edit_epoch != epoch or self.generation != generation:
                    # A newer detection owns the session. Drop the stale rewrite
                    # and its busy flag; a newer edit has already replaced both.
                    if (
                        self.generation != generation
                        and self._preview_busy
                        and self._preview_busy.get("epoch") == epoch
                    ):
                        self._preview_busy = None
                        self._preview_ready.set()
                    return
                if error:
                    self.status = "Error."
                    self.error = error
                else:
                    self.anonymized = anonymized
                    self._set_verification(report)
                self._view = None
                self._view_key = None
                if self._preview_busy and self._preview_busy.get("epoch") == epoch:
                    self._preview_busy = None
                self._preview_ready.set()

        threading.Thread(target=work, name="did-gui-preview", daemon=True).start()

    def _require_draft(self) -> str:
        if self.project is None:
            raise ApiError("No project is open.")
        if self.viewing_version is not None:
            raise ApiError("The saved output is read-only.")
        if not self.yaml_text.strip():
            raise ApiError("There are no identities to edit yet.")
        return self.yaml_text

    def change_types(self, entity_ids, target_type: str) -> None:
        if target_type not in REVIEW_TYPES:
            raise ApiError(f"Unsupported entity type: {target_type}")
        ids = [str(item) for item in entity_ids or [] if item]
        if not ids:
            raise ApiError("Select at least one identity.")
        with self.lock:
            try:
                changed = pipeline.change_entity_types(
                    self._require_draft(), ids, target_type
                )
            except ValueError as exc:
                raise ApiError(str(exc)) from exc
            count = len(set(ids))
            noun = "identity" if count == 1 else "identities"
            self._apply_yaml(changed, f"Changed {count} {noun} to {target_type}.")

    def add_variant(
        self, entity_type: str, entity_id: str, variant: str, position=None
    ) -> None:
        variant = str(variant or "").strip()
        if not variant:
            raise ApiError("A variant needs text.")
        with self.lock:
            text = self._require_draft()
            try:
                if entity_id:
                    changed = pipeline.add_entity_variant(
                        text, entity_type, str(entity_id), variant
                    )
                else:
                    changed = pipeline.add_entity_variant_at(
                        text, entity_type, int(position), variant
                    )
            except (ValueError, TypeError) as exc:
                raise ApiError(str(exc)) from exc
            if changed == text:
                self.status = f"{variant} is already a variant."
                return
            self._apply_yaml(changed, f"Added variant {variant}.")

    def merge(self, source_id: str, target_id: str, variant: str | None = None) -> None:
        with self.lock:
            try:
                changed = pipeline.merge_person_entities(
                    self._require_draft(), str(source_id), str(target_id), variant
                )
            except ValueError as exc:
                raise ApiError(str(exc)) from exc
            self._apply_yaml(changed, f"Merged {source_id} into {target_id}.")

    def mark_not_names(self, entity_ids) -> None:
        ids = {str(item) for item in entity_ids or [] if item}
        with self.lock:
            text = self._require_draft()
            if self.project.project_file is None:
                raise ApiError("Save the project before excluding a name.")
            rows, _error = entity_rows(text)
            variants = []
            seen = set()
            for row in rows:
                if row["child"] or row["id"] not in ids or row["type"] != "PERSON":
                    continue
                for variant in row["variants"]:
                    key = variant.casefold()
                    if variant.strip() and key not in seen:
                        seen.add(key)
                        variants.append(variant.strip())
            if not variants:
                raise ApiError("Select a person identity to exclude.")
            try:
                exclusions = load_not_names(self.project)
                save_not_names(self.project, [*exclusions, *variants])
                changed = pipeline.exclude_not_names(text, variants)
            except (ProjectError, ValueError) as exc:
                raise ApiError(str(exc)) from exc
            count = len(ids)
            noun = "identity" if count == 1 else "identities"
            self._apply_yaml(
                changed,
                f"Marked {count} {noun} as do-not-pseudonymize.",
            )

    def entity_from_selection(self, text: str, entity_type: str = "PERSON") -> None:
        selected = pipeline.resolve_selection_placeholders(
            str(text or ""), self.yaml_text
        ).strip()
        if not selected:
            raise ApiError("Select some text first.")
        if entity_type not in REVIEW_TYPES:
            raise ApiError(f"Unsupported entity type: {entity_type}")
        with self.lock:
            try:
                changed = pipeline.add_entity(
                    self._require_draft(), selected, entity_type
                )
            except ValueError as exc:
                raise ApiError(str(exc)) from exc
            self._apply_yaml(
                changed, f"Created a {entity_type} identity from the selection."
            )

    def add_selection(self, text: str, entity_type: str, entity_id: str) -> None:
        selected = pipeline.resolve_selection_placeholders(
            str(text or ""), self.yaml_text
        ).strip()
        if not selected:
            raise ApiError("Select some text first.")
        self.add_variant(entity_type, entity_id, selected)

    # --------------------------------------------------------------- export ---
    def _publishable(self) -> bool:
        bad = [
            reading.path.name
            for reading in self.readings.values()
            if not reading.readable
        ]
        if bad:
            self.status = f"Not publishing. No readable text for {', '.join(bad)}."
            return False
        return True

    def _export(self, mode: str, trigger: str) -> None:
        """Start an output write. Caller holds the lock, or calls with it free
        only through save(), which takes it. This method takes the lock itself
        when scheduling and is invoked from apply() which already holds it.
        """
        if self.project is None or self.project.project_file is None:
            raise ApiError("Save the project before writing output.")
        if not self.anonymized:
            raise ApiError("Nothing has been pseudonymized yet.")
        if mode not in {"multi", "single", "pdf"}:
            raise ApiError(f"Unsupported save mode: {mode}")
        project = self.project
        project.export_mode = mode
        project.entity_config_yaml = self.yaml_text
        save_project(project)
        stage = begin_version(project, "")
        files = [path for path in self.files if path in self.anonymized]
        yaml_text = self.yaml_text
        texts = self._texts()
        anonymizer = self.anonymizer
        processing = {
            "language": self.language,
            "detection_profile": self.detection_profile,
            "trigger": trigger,
            "verification": self._verification_record(),
        }
        export = {"format": project.export_format, "mode": mode}
        keys = {
            "origin": self.keys_origin or "edited",
            "identities": pipeline.count_identities(yaml_text),
            "excluded": len(self._not_names()),
        }
        snapshot = project

        sources = [Path(path) for path in project.source_paths]

        def work():
            pipeline.save_version_outputs(
                files, anonymizer, yaml_text, mode, stage.path, source_texts=texts
            )
            finalize_version(
                snapshot,
                stage,
                processing=processing,
                export=export,
                app_version=__version__,
                keys=keys,
            )
            marks, draft, cache = self._measure_sync(snapshot, sources)
            return {"marks": marks, "draft": draft, "cache": cache}

        def apply(result):
            self._digest_cache.update(result.get("cache") or {})
            self._sync_marks = result["marks"]
            self._draft_mark = result["draft"]
            self._refresh_cached_marks()
            self.status = f"Wrote the output · {len(files)} document(s)."
            self.error = None

        def failed_wrapper():
            try:
                return work()
            except Exception:
                abort_version(stage)
                raise

        self._start_job(
            f"Writing the output ({len(files)} document(s))…", failed_wrapper, apply
        )

    def save(self, mode: str) -> dict:
        self._wait_until_preview()
        with self.lock:
            if self.viewing_version is not None:
                raise ApiError("Return to the draft before writing output.")
            if not self._publishable():
                raise ApiError(self.status)
            self._require_ack("Write output")
            self._export(mode, "manual")
        return {"ok": True}

    # ------------------------------------------------------------------ llm ---
    def _llm_paths(self) -> list[Path]:
        return [path for path in self.files if path in self.anonymized]

    @staticmethod
    def _title_token(index: int) -> str:
        return f"#(DOC{index}V1)"

    def _llm_document(self, path, index: int) -> dict:
        return {
            "id": f"document-{index:03d}",
            "title_token": self._title_token(index),
            "content": self._display_text(path),
        }

    def _llm_documents(self, only: Path | None = None) -> list[dict]:
        ordered = self._llm_paths()
        if only is not None:
            if only not in ordered:
                return []
            index = ordered.index(only) + 1
            return [self._llm_document(only, index)]
        return [
            self._llm_document(path, index) for index, path in enumerate(ordered, 1)
        ]

    def _metadata_markdown(self) -> str:
        metadata = self.project.metadata if self.project is not None else {}
        lines = ["# Project meta"]
        for key, value in metadata.items():
            if value in (None, "", []):
                continue
            rendered = (
                ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            )
            lines.append(f"- **{key.replace('_', ' ').title()}:** {rendered}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _llm_markdown(self, documents) -> str:
        parts = []
        metadata = self._metadata_markdown()
        if metadata:
            parts.append(metadata)
        parts.extend(
            f"# {document['title_token']}\n\n{document['content']}"
            for document in documents
        )
        return "\n\n".join(parts) + "\n"

    def _llm_json(self, documents) -> str:
        payload = {
            "format": "did-pseudonymized-session",
            "version": 1,
            "rendering": self.output_mode,
            "metadata": self.project.metadata if self.project is not None else {},
            "documents": documents,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def llm(self, scope: str, form: str) -> str:
        self._wait_until_preview()
        with self.lock:
            if scope not in {"current", "all"} or form not in {"md", "json"}:
                raise ApiError("scope is current|all and form is md|json.")
            only = self.selected if scope == "current" else None
            documents = self._llm_documents(only)
            if not documents:
                raise ApiError("Nothing pseudonymized to hand off.")
            self._require_ack("Copy")
            if form == "md":
                text = self._llm_markdown(documents)
            else:
                text = self._llm_json(documents)
            self.status = f"Prepared {len(documents)} document(s) as {form}."
            return text

    def llm_zip(self, destination: str) -> str:
        destination = str(destination or "").strip()
        if not destination:
            raise ApiError("A destination path is required.")
        self._wait_until_preview()
        with self.lock:
            documents = self._llm_documents()
            if not documents:
                raise ApiError("Nothing pseudonymized to hand off.")
            self._require_ack("Export")
            dest = Path(destination).expanduser()
            if dest.suffix.lower() != ".zip":
                dest = dest.with_suffix(".zip")
            dest.parent.mkdir(parents=True, exist_ok=True)
            markdown = self._llm_markdown(documents)
            payload = self._llm_json(documents)
            try:
                with zipfile.ZipFile(
                    dest, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    archive.writestr("combined.md", markdown)
                    archive.writestr("session.json", payload)
                    archive.writestr(
                        "README.txt",
                        "Privacy-safe documents prepared by DID.\n"
                        f"Rendering mode: {self.output_mode}.\n"
                        "Original filenames, entity configuration, and identity "
                        "mappings are intentionally excluded.\n"
                        "Each document is titled with an assigned #(DOC<n>V1) "
                        "token; the real filenames stay in the project.\n",
                    )
                    for document in documents:
                        archive.writestr(
                            f"documents/{document['id']}.md",
                            (document["content"] or "") + "\n",
                        )
            except OSError as exc:
                raise ApiError(f"Could not save the package: {exc}") from exc
            self.settings.set_directory("exports", dest.parent)
            self.status = (
                f"Saved a safe session package with {len(documents)} document(s)."
            )
            return str(dest)

    # -------------------------------------------------------------- versions ---
    def _view_version(self, version_path: Path) -> None:
        if self.project is None:
            raise ApiError("No project is open.")
        version_path = Path(version_path)
        if self.viewing_version is not None:
            self._return_to_draft()
        self.draft_view = {
            "files": list(self.files),
            "readings": dict(self.readings),
            "anonymized": dict(self.anonymized),
            "yaml": self.yaml_text,
            "selected": self.selected,
        }
        files = version_document_files(version_path)
        self.files = files
        self.readings = {}
        self.anonymized = {}
        for path in files:
            try:
                self.anonymized[path] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        keys = version_path / ENTITIES_FILENAME
        if not keys.exists():
            found = sorted((version_path / "output").rglob("config.yaml"))
            keys = found[0] if found else keys
        try:
            self.yaml_text = keys.read_text(encoding="utf-8") if keys.exists() else ""
        except OSError:
            self.yaml_text = ""
        self.viewing_version = version_path
        self.selected = files[0] if files else None
        self.show_original = False
        self.status = "Viewing the saved output · read-only."

    def _return_to_draft(self) -> None:
        if self.viewing_version is None or self.draft_view is None:
            return
        state = self.draft_view
        self.viewing_version = None
        self.draft_view = None
        self.files = state["files"]
        self.readings = state["readings"]
        self.anonymized = state["anonymized"]
        self.yaml_text = state["yaml"]
        self.selected = state["selected"]
        self.show_original = False
        self.status = "Viewing the current draft."
        if self.force_merge and self.readings:
            self.force_merge = True
            # Caller holds the lock. select_document reads this after the
            # restore and starts detection once the lock is released.
            self._pending_redetect = True

    def view_version(self, path: str) -> None:
        with self.lock:
            self._pending_redetect = False
            self._view_version(Path(path))

    def reveal(self, path: str) -> bool:
        return open_in_file_manager(path)

    def redetect(self) -> None:
        """Detect again on purpose. Reviewed identities stay; new finds are added."""
        with self.lock:
            if self.viewing_version is not None:
                raise ApiError("Return to the draft before re-running detection.")
            if not self.readings:
                raise ApiError("Add documents before re-running detection.")
            self.force_merge = True
            self.status = "Re-running detection. The review is kept."
        self._start_detect()

    def save_open_project(self) -> str:
        with self.lock:
            if self.project is None or self.project.project_file is None:
                raise ApiError("No project is open.")
            self.project.entity_config_yaml = self.yaml_text
            saved = save_project(self.project)
            self.settings.add_recent_project(saved)
            self.status = f"Project saved to {saved}."
            return str(saved)

    def save_project_as(self, parent: str) -> str:
        """Write the open project into a new workdir. The previous workdir stays."""
        if not parent:
            raise ApiError("Choose a parent directory for the project.")
        with self.lock:
            if self.project is None:
                raise ApiError("No project is open.")
            self.project.entity_config_yaml = self.yaml_text
            name = self.project.name
            try:
                saved = create_project_workdir(self.project, parent, name)
            except ProjectError as exc:
                raise ApiError(str(exc)) from exc
            self.settings.add_recent_project(saved)
            self.settings.set_directory("projects", parent)
            self.status = f"Project saved to {saved}."
            return str(saved)

    def clear_recent(self) -> None:
        """Drop the recent list. The project that is open stays on screen."""
        with self.lock:
            self.settings.recent = []
            self.settings._save()
            self.status = "Cleared recent projects."

    def copy_output(self, version: str, parent: str) -> str:
        if not parent:
            raise ApiError("Choose a destination folder.")
        source = Path(version).expanduser().resolve()
        with self.lock:
            if self.project is None or self.project.workdir is None:
                raise ApiError("No project is open.")
            versions = (self.project.workdir / "versions").resolve()
            if versions not in source.parents:
                raise ApiError("That folder is not a saved output of this project.")
            if not source.is_dir():
                raise ApiError("That output folder is not on disk.", 404)
        destination = Path(parent).expanduser().resolve() / source.name
        if destination.exists():
            raise ApiError(f"Destination already exists: {destination}")
        try:
            shutil.copytree(source, destination)
        except OSError as exc:
            raise ApiError(f"Copy failed: {exc}") from exc
        with self.lock:
            self.status = f"Copied {source.name} to {destination}."
        return str(destination)

    def keys_document(self) -> dict:
        with self.lock:
            info = self._keys_bar()
            return {
                "yaml": self.yaml_text,
                "path": info.get("path") or "",
                "folder": info.get("folder") or "",
                "read_only": self.viewing_version is not None,
            }

    def replace_keys(self, yaml_text: str) -> None:
        with self.lock:
            if self.project is None:
                raise ApiError("No project is open.")
            if self.viewing_version is not None:
                raise ApiError("Return to the draft before editing keys.")
            self._apply_yaml(yaml_text, "Applied the keys.")
