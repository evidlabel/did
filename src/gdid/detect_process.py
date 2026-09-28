"""Run spaCy detection in a child process so it cannot stall the Qt event loop.

spaCy's Cython inference holds the GIL for long stretches. A ``QThread`` that
calls it therefore still freezes the GUI: the worker thread owns the GIL and the
Qt event loop never gets a turn. A child process has its own interpreter and its
own GIL, so the window stays responsive while detection runs.

The process is started lazily and reused, so the language model is loaded once
per (language, profile). All results cross the boundary as plain picklable data
(``yaml_str``, ``{Path: text}``, verification report).
"""

from __future__ import annotations

import atexit
import multiprocessing as mp
import threading

_DETECTION_TIMEOUT_S = 1800

_process = None
_requests = None
_responses = None
_lock = threading.Lock()

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


def run_detection(
    texts, language, detection_profile, not_names, *, work=None, timeout=None
):
    """Detect, pseudonymize, and verify in the child; block until it is done.

    Called from a worker thread, so blocking here does not touch the GUI. The
    child must already be running (see :func:`ensure_started`); the child never
    runs two requests at once.
    """
    work = work or _detect_work
    timeout = _DETECTION_TIMEOUT_S if timeout is None else timeout
    with _lock:
        if _process is None or not _process.is_alive():
            raise RuntimeError("Detection process is not running.")
        _requests.put((work, (texts, language, detection_profile, list(not_names))))
        try:
            kind, payload = _responses.get(timeout=timeout)
        except Exception as exc:
            # A dead or wedged child must not hang the worker forever.
            _discard_process()
            raise RuntimeError(f"Detection process failed: {exc}") from exc
    if kind == "error":
        raise RuntimeError(payload)
    return payload


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
