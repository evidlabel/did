"""Off-thread workers for gdid (evid pattern: QThread + progress/finished/error).

Each worker delegates to :mod:`gdid.pipeline`, keeping all real logic Qt-free and
testable. The window keeps references in a list so threads aren't GC'd mid-run.
"""

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from did.core.anonymizer import MODELS_INSTALL_HINT, Anonymizer

from .. import pipeline


class ExtractWorker(QThread):
    """Extract text from each file (sequentially, off the UI thread)."""

    progress = Signal(int, int, str)  # done, total, name
    # object (not dict): the payload is keyed by Path; Signal(dict) would force a
    # C++ QVariantMap conversion that mangles non-str keys.
    finished = Signal(object)  # {Path: text}
    error = Signal(str)

    def __init__(self, files):
        super().__init__()
        self._files = list(files)

    def run(self):
        results = {}
        total = len(self._files)
        try:
            for i, raw in enumerate(self._files, 1):
                f = Path(raw)
                self.progress.emit(i, total, f.name)
                results[f] = pipeline.extract_one(f)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.finished.emit(results)


class AnonymizeWorker(QThread):
    """Detect entities (spaCy — slow) then pseudonymize with the detected config.

    Verification runs deep here: the language model is already loaded, so the
    full re-detection sweep over the output costs one extra pass rather than a
    cold start.
    """

    # anonymizer, yaml_str, {Path: anonymized}, VerificationReport
    finished = Signal(object, str, object, object)
    error = Signal(str)

    def __init__(
        self,
        texts,
        language,
        *,
        detection_profile=None,
        anonymizer_factory=Anonymizer,
        not_names=(),
    ):
        super().__init__()
        self._texts = dict(texts)
        self._language = language
        self._detection_profile = detection_profile
        self._factory = anonymizer_factory
        self._not_names = list(not_names)

    def run(self):
        try:
            anonymizer, yaml_str = pipeline.detect_to_yaml(
                self._texts.values(),
                self._language,
                detection_profile=self._detection_profile,
                anonymizer_factory=self._factory,
            )
            anonymized = pipeline.pseudonymize_all(anonymizer, yaml_str, self._texts)
            report = pipeline.verify_outputs(
                yaml_str, anonymized, self._not_names, anonymizer=anonymizer
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        except SystemExit:
            self.error.emit(
                "Detection aborted: a spaCy model is missing and cannot be "
                f"downloaded (uv venvs have no pip). Install with: {MODELS_INSTALL_HINT}"
            )
            return
        self.finished.emit(anonymizer, yaml_str, anonymized, report)


class PseudoWorker(QThread):
    """Re-apply an edited YAML config (regex-only — fast).

    Verification stays shallow here. This runs on every config edit, so it uses
    only the model-free tiers; the deep sweep belongs to :class:`AnonymizeWorker`.
    """

    finished = Signal(object, object)  # {Path: anonymized}, VerificationReport
    error = Signal(str)

    def __init__(self, anonymizer, yaml_text, texts, *, not_names=()):
        super().__init__()
        self._anonymizer = anonymizer
        self._yaml_text = yaml_text
        self._texts = dict(texts)
        self._not_names = list(not_names)

    def run(self):
        try:
            anonymized = pipeline.pseudonymize_all(
                self._anonymizer, self._yaml_text, self._texts
            )
            report = pipeline.verify_outputs(
                self._yaml_text, anonymized, self._not_names
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.finished.emit(anonymized, report)
