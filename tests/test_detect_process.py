"""The detection child process that keeps spaCy off the Qt thread."""

import os
import threading
import time

import pytest

from did.core.anonymizer import Anonymizer
from gdid import detect_process


def test_detection_runs_in_a_separate_process():
    detect_process.shutdown()
    detect_process.ensure_started()
    try:
        yaml_str, anonymized, report = detect_process.run_detection(
            {"a": "hello", "b": "world"},
            "en",
            "thorough",
            [],
            work=detect_process._echo_work,
        )
    finally:
        detect_process.shutdown()

    assert yaml_str.startswith("en:2:")
    child_pid = int(yaml_str.rsplit(":", 1)[1])
    assert child_pid != os.getpid()
    assert anonymized == {"a": "hello", "b": "world"}
    assert report is None


def test_child_errors_are_raised_in_the_parent():
    detect_process.shutdown()
    detect_process.ensure_started()
    try:
        with pytest.raises(RuntimeError, match="ValueError: nope"):
            detect_process.run_detection(
                {"a": "x"}, "boom", "thorough", [], work=detect_process._echo_work
            )
    finally:
        detect_process.shutdown()


def test_run_detection_requires_a_started_child():
    detect_process.shutdown()
    with pytest.raises(RuntimeError, match="not running"):
        detect_process.run_detection(
            {"a": "x"}, "en", "thorough", [], work=detect_process._echo_work
        )


def test_cancel_stops_a_running_job(tmp_path):
    mark = tmp_path / "in-job"
    os.environ["DID_SLEEP_MARK"] = str(mark)
    detect_process.shutdown()
    detect_process.ensure_started()
    holder = {}

    def run():
        try:
            holder["result"] = detect_process.run_detection(
                {"a": "x"},
                "en",
                "thorough",
                [],
                work=detect_process._sleep_work,
                timeout=5,
            )
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not mark.exists():
        time.sleep(0.05)
    detect_process.cancel()
    thread.join(5)
    try:
        assert mark.exists()
        assert not thread.is_alive()
        assert isinstance(holder.get("error"), detect_process.DetectionCancelled)
    finally:
        os.environ.pop("DID_SLEEP_MARK", None)
        detect_process.shutdown()


def test_extract_documents_reads_without_detecting(tmp_path):
    path = tmp_path / "letter.txt"
    path.write_text("John Doe met Jane Roe.", encoding="utf-8")
    readings = detect_process.extract_documents([str(path)])
    assert readings[str(path)].text == "John Doe met Jane Roe."


def test_regex_only_anonymizer_needs_no_model():
    anonymizer = Anonymizer.for_regex_only("da", "thorough")
    anonymizer.load_replacements(
        {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    )

    text, counts = anonymizer.anonymize("John Doe signed the deal.")

    assert "#(P1V1)" in text
    assert "John Doe" not in text
    assert counts["person_replaced"] == 1
