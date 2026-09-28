"""The detection child process that keeps spaCy off the Qt thread."""

import os

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


def test_regex_only_anonymizer_needs_no_model():
    anonymizer = Anonymizer.for_regex_only("da", "thorough")
    anonymizer.load_replacements(
        {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    )

    text, counts = anonymizer.anonymize("John Doe signed the deal.")

    assert "#(P1V1)" in text
    assert "John Doe" not in text
    assert counts["person_replaced"] == 1
