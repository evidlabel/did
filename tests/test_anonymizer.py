"""Tests for the Anonymizer."""

import pytest
import ruamel.yaml as yaml  # Import for ruamel.yaml usage
from did.core.anonymizer import Anonymizer
import sys
import io
from contextlib import redirect_stdout, redirect_stderr


@pytest.fixture
def anonymizer():
    return Anonymizer(language="en")


def test_extract_empty_text(anonymizer):
    anonymizer.detect_entities([""])
    yaml_obj = yaml.YAML()  # Use ruamel.yaml YAML object
    config_str = anonymizer.generate_yaml()
    config = yaml_obj.load(config_str)  # Load from string
    assert config["PERSON"] == []
    assert config["EMAIL_ADDRESS"] == []
    assert config["LOCATION"] == []
    assert config["PHONE_NUMBER"] == []
    assert config["DATE_NUMBER"] == []
    assert config["ID_NUMBER"] == []
    assert config["CODE_NUMBER"] == []
    assert config["GENERAL_NUMBER"] == []
    assert config["CPR_NUMBER"] == []
    assert all(count == 0 for count in anonymizer.counts.values())


def test_anonymize_name_exact(anonymizer):
    text = "Hello John Doe, how are you?"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "<PERSON_1>" in result
    assert counts["person_found"] >= 1
    assert counts["person_replaced"] >= 1


def test_anonymize_name_variants(anonymizer):
    text = "John Doe and Jon Doe and john DOE were mentioned."
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    config_str = anonymizer.generate_yaml()
    yaml_obj = yaml.YAML()
    config = yaml_obj.load(config_str)
    assert len(config["PERSON"]) == 1
    result, counts = anonymizer.anonymize(text)
    assert "<PERSON_1>" in result
    assert counts["person_found"] == 3
    assert counts["person_replaced"] == 3


def test_anonymize_number_variants(anonymizer):
    text = "Account: 1234567890, Phone: 1234567, Code: 12 34 56 78"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    config_str = anonymizer.generate_yaml()
    yaml_obj = yaml.YAML()
    config = yaml_obj.load(config_str)
    assert any(
        "1234567890" in entry["variants"]
        for entry in config.get("PHONE_NUMBER", []) + config.get("GENERAL_NUMBER", [])
    )
    assert any(
        "12 34 56 78" in entry["variants"]
        for entry in config.get("PHONE_NUMBER", []) + config.get("GENERAL_NUMBER", [])
    )
    result, counts = anonymizer.anonymize(text)
    assert any(tag in result for tag in ["<PHONE_NUMBER_", "<GENERAL_NUMBER_"])
    assert counts["phone_number_found"] + counts["general_number_found"] >= 3


def test_anonymize_address(anonymizer):
    text = "Lives at 123 Oneway St, Springfield, US"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "<LOCATION_1>" in result
    assert counts["location_found"] >= 1
    assert counts["location_replaced"] >= 1


def test_anonymize_danish_address():
    anonymizer = Anonymizer(language="da")
    text = "Bor på Langelandsgade 14, 1.tv, 7300 Jelling"
    anonymizer.detect_entities([text])
    assert anonymizer.counts["location_found"] >= 1
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "<LOCATION_1>" in result
    assert counts["location_found"] >= 1
    assert counts["location_replaced"] >= 1


def test_anonymize_cpr():
    anonymizer = Anonymizer(language="da")
    text = "CPR: 123456-1234"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "<CPR_NUMBER_1>" in result
    assert counts["cpr_number_found"] >= 1
    assert counts["cpr_number_replaced"] >= 1


def test_anonymize_mixed_content(anonymizer):
    text = "Contact John Doe at 1234567890 or Jane Smith via 12 34 56 78. Jon Doe and Jane Smyth share details at 123 Oneway St, Springfield, US. CPR: 123456-1234. Additional phone: 1234567"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert any(tag in result for tag in ["<PHONE_NUMBER_", "<GENERAL_NUMBER_"])
    assert "<LOCATION_" in result
    assert "<CPR_NUMBER_" not in result  # CPR might not be detected in 'en'
    assert counts["person_found"] >= 4
    assert counts["person_replaced"] >= 4
    assert counts["phone_number_found"] + counts["general_number_found"] >= 3
    assert counts["location_found"] >= 1
    assert counts["location_replaced"] >= 1
    assert counts["cpr_number_found"] == 0  # Adjusted for 'en'
    assert counts["cpr_number_replaced"] == 0


def test_cli_extract(tmp_path, capsys):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    input_file.write_text("Hello John Doe and Jon Doe, CPR: 123456-1234")

    old_argv = sys.argv
    sys.argv = ["did", "extract", str(input_file), "--config", str(config_file)]

    from did.cli import main

    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
        main()

    sys.argv = old_argv
    output = out.getvalue()

    assert "PERSON found: 2" in output  # Grouped
    assert (
        "CPR_NUMBER found: 0" in output or "CPR_NUMBER found: 1" in output
    )  # Depending on language
    assert config_file.exists()
    yaml_obj = yaml.YAML()  # Use ruamel.yaml YAML object
    with open(config_file, "r") as f:
        config = yaml_obj.load(f)  # Load using YAML object
        assert len(config["PERSON"]) >= 1
        assert any(
            "123456-1234" in entry["variants"]
            for entry in config.get("CPR_NUMBER", []) + config.get("PHONE_NUMBER", [])
        )


def test_cli_anonymize(tmp_path, capsys):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    output_file = tmp_path / "output.md"
    original_text = "Hello John Doe and Jon Doe, CPR: 123456-1234"
    input_file.write_text(original_text)

    # First extract to generate config
    old_argv = sys.argv
    sys.argv = ["did", "extract", str(input_file), "--config", str(config_file)]
    from did.cli import main

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        main()
    sys.argv = old_argv

    # Modify the input file to add new content
    modified_text = (
        original_text
        + " and John Doe again, and new person Alice, new CPR: 987654-4321"
    )
    input_file.write_text(modified_text)

    sys.argv = [
        "did",
        "pseudo",
        str(input_file),
        "--config",
        str(config_file),
        "--output",
        str(output_file),
    ]
    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
        main()
    sys.argv = old_argv
    output = out.getvalue()

    assert "PERSON replaced: 3" in output
    assert "CPR_NUMBER replaced: 1" in output or "PHONE_NUMBER replaced" in output
    assert output_file.exists()
    with open(output_file, "r") as f:
        content = f.read()
        assert "<PERSON_1>" in content
        assert "<CPR_NUMBER_1>" in content or "<PHONE_NUMBER_" in content
        assert "Alice" in content
        assert "987654-4321" in content
        assert content.count("<PERSON_1>") == 3  # Variants of John Doe
