"""Run document analysis in a child process so it cannot stall the page.

spaCy's Cython inference holds the GIL for long stretches. A thread in this
process that calls it freezes every request the page is waiting on, even when
that thread is not the one that accepted the request. A child process has its
own interpreter and its own GIL, so the page stays responsive while detection,
settling, and source hashing run.

The process is started lazily and reused, so the language model is loaded once
per (language, profile). All results cross the boundary as plain picklable data
(``yaml_str``, ``{Path: text}``, verification report).
"""

from __future__ import annotations

import atexit
import multiprocessing as mp
import queue
import threading
import time

_DETECTION_TIMEOUT_S = 1800

_process = None
_requests = None
_responses = None
_lock = threading.Lock()
# Serializes jobs. cancel() does not take this, so a running wait can be stopped.
_job_lock = threading.Lock()


class DetectionCancelled(Exception):
    """The child was stopped. The caller asked to see something else."""


# Child-process cache: one Anonymizer (and its spaCy model) per language/profile.
_anonymizers: dict = {}


def _detect_work(texts, language, detection_profile, not_names):
    """The real pipeline, run inside the child. Returns picklable results."""
    from did.core.anonymizer import Anonymizer

    from . import pipeline

    key = (language, detection_profile)
    anonymizer = _anonymizers.get(key)
    if anonymizer is None:
        anonymizer = Anonymizer(language=language, detection_profile=detection_profile)
        _anonymizers[key] = anonymizer
    anonymizer.detect_entities(list(texts.values()))
    yaml_str = anonymizer.generate_yaml()
    anonymized = pipeline.pseudonymize_all(anonymizer, yaml_str, texts)
    report = pipeline.verify_outputs(
        yaml_str, anonymized, not_names, anonymizer=anonymizer
    )
    return yaml_str, anonymized, report


def _echo_work(texts, language, detection_profile, not_names):
    """Trivial child work used by tests to exercise the process plumbing."""
    import os

    if language == "boom":
        raise ValueError("nope")
    return f"{language}:{len(texts)}:{os.getpid()}", dict(texts), None


def _sleep_work(texts, language, detection_profile, not_names):
    """Child work that stays busy until the process is stopped. Tests only."""
    import os

    mark = os.environ.get("DID_SLEEP_MARK")
    if mark:
        with open(mark, "w", encoding="utf-8") as handle:
            handle.write("in-job\n")
    time.sleep(30)
    return "slept", dict(texts), None


def _child_main(request_queue, response_queue):
    while True:
        request = request_queue.get()
        if request is None:
            return
        work, args = request
        try:
            result = work(*args)
        except BaseException as exc:  # noqa: BLE001 — report, keep the child alive
            response_queue.put(("error", f"{type(exc).__name__}: {exc}"))
        else:
            response_queue.put(("ok", result))


def _start_process():
    global _process, _requests, _responses
    context = mp.get_context("spawn")
    _requests = context.Queue()
    _responses = context.Queue()
    _process = context.Process(
        target=_child_main,
        args=(_requests, _responses),
        daemon=True,
        name="did-detect",
    )
    _process.start()


def ensure_started():
    """Start the child process if it is not running.

    Must be called from the main thread: forking a Qt process from a worker
    thread can deadlock, so the worker only ever talks to an already-running
    child.
    """
    with _lock:
        if _process is None or not _process.is_alive():
            _start_process()
        return _process is not None


def cancel():
    """Stop the child so a scan does not keep the CPU after the user moves on.

    The waiter notices the process is gone and raises :class:`DetectionCancelled`.
    The next job starts a fresh child.
    """
    with _lock:
        _discard_process()


def _stopped(process) -> bool:
    return process is not _process or not process.is_alive()


def run_job(work, args, *, timeout=None):
    """Run ``work(*args)`` in the child and block the caller until it returns.

    The caller is a worker thread. The child has its own interpreter, so spaCy
    and text extraction there cannot hold this process's GIL or the GUI thread.
    The child must already be running (see :func:`ensure_started`).

    The wait does not hold ``_lock``, so :func:`cancel` can stop the child
    without waiting for the job to finish.
    """
    timeout = _DETECTION_TIMEOUT_S if timeout is None else timeout
    with _job_lock:
        with _lock:
            if _process is None or not _process.is_alive():
                raise RuntimeError("Detection process is not running.")
            process = _process
            responses = _responses
            _requests.put((work, tuple(args)))
        deadline = time.monotonic() + timeout
        while True:
            with _lock:
                stopped = _stopped(process)
            if stopped:
                raise DetectionCancelled("Detection cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with _lock:
                    if process is _process:
                        _discard_process()
                raise RuntimeError("Detection process failed: timed out")
            try:
                kind, payload = responses.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            except Exception as exc:
                with _lock:
                    stopped = _stopped(process)
                    if not stopped and process is _process:
                        _discard_process()
                if stopped:
                    raise DetectionCancelled("Detection cancelled.") from exc
                raise RuntimeError(f"Detection process failed: {exc}") from exc
            break
    if kind == "error":
        raise RuntimeError(payload)
    return payload


def run_detection(
    texts, language, detection_profile, not_names, *, work=None, timeout=None
):
    """Detect, pseudonymize, and verify in the child; block until it is done."""
    work = work or _detect_work
    return run_job(
        work,
        (texts, language, detection_profile, list(not_names)),
        timeout=timeout,
    )


def extract_documents(paths):
    """Child work: read the documents and return readings. No detection.

    The parent caches these and can show the source text while detection runs
    in a later job. Keys are the path strings the parent sent.
    """
    from . import pipeline

    readings = {}
    for raw in paths:
        readings[str(raw)] = pipeline.extract_one(raw)
    return readings


def extract_and_detect(paths, language, detection_profile, not_names):
    """Child work: read the documents, then detect. No review merge."""
    from pathlib import Path

    readings = {Path(key): reading for key, reading in extract_documents(paths).items()}
    texts = {path: reading.text for path, reading in readings.items()}
    yaml_str, anonymized, report = _detect_work(
        texts, language, detection_profile, not_names
    )
    return readings, yaml_str, anonymized, report


def settle_detected(
    language,
    detection_profile,
    yaml_str,
    anonymized,
    report,
    review,
    readings,
    texts,
    not_names,
    force,
):
    """Child work: merge the review and re-pseudonymize. Regex only, no spaCy."""
    from did.core.anonymizer import Anonymizer

    from . import pipeline

    anonymizer = Anonymizer.for_regex_only(language, detection_profile)
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


def rewrite_with_keys(language, detection_profile, yaml_text, texts, not_names):
    """Child work: pseudonymize and verify a reviewed config. Regex only, no spaCy."""
    from did.core.anonymizer import Anonymizer

    from . import pipeline

    anonymizer = Anonymizer.for_regex_only(language, detection_profile)
    anonymized = pipeline.pseudonymize_all(anonymizer, yaml_text, texts)
    report = pipeline.verify_outputs(yaml_text, anonymized, not_names)
    return anonymized, report


def hash_sources(paths):
    """Child work: digest sources so the parent process does not read them."""
    from pathlib import Path

    from .syncstate import source_digest

    cache = {}
    for raw in paths:
        source_digest(Path(raw), cache)
    return cache


def _discard_process():
    global _process, _requests, _responses
    process, _process = _process, None
    _requests = _responses = None
    if process is not None and process.is_alive():
        process.terminate()
        process.join(timeout=2)


def shutdown():
    """Stop the child process, if one is running."""
    global _process, _requests, _responses
    with _lock:
        if _process is None:
            return
        try:
            _requests.put(None)
            _process.join(timeout=2)
        except Exception:
            pass
        if _process.is_alive():
            _process.terminate()
            _process.join(timeout=2)
        _process = _requests = _responses = None


atexit.register(shutdown)
