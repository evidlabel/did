"""Tests for the Anonymizer."""

import pytest
import yaml
from did.core.anonymizer import Anonymizer
from click.testing import CliRunner
from did.cli import main


@pytest.fixture
def anonymizer():
    return Anonymizer()


@pytest.fixture
def runner():
    return CliRunner()


def test_extract_empty_text(anonymizer):
    anonymizer.detect_entities([""])
    config = yaml.safe_load(anonymizer.generate_yaml())
    assert config["names"] == []
    assert config["emails"] == []
    assert config["addresses"] == []
    assert config["numbers"] == []
    assert config["cpr"] == []
    assert all(count == 0 for count in anonymizer.counts.values())


def test_anonymize_name_exact(anonymizer):
    text = "Hello John Doe, how are you?"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    result, counts = anonymizer.anonymize(text)
    assert "<PERSON_1>" in result
    assert counts["names_found"] >= 1
    assert counts["names_replaced"] >= 1


def test_anonymize_name_variants(anonymizer):
    text = "John Doe and Jon Doe and john DOE were mentioned."
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    config = yaml.safe_load(anonymizer.generate_yaml())
    assert len(config["names"]) == 1
    result, counts = anonymizer.anonymize(text)
    assert "<PERSON_1>" in result
    assert counts["names_found"] == 3
    assert counts["names_replaced"] == 3


def test_anonymize_number_variants(anonymizer):
    text = "Account: 1234567890, Phone: 1234567, Code: 12 34 56 78"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    config = yaml.safe_load(anonymizer.generate_yaml())
    assert any("1234567890" in entry["variants"] for entry in config["numbers"])
    assert any(
        "12 34 56 78" in entry["variants"]
        and entry.get("pattern") == r"\b\d{2}\s+\d{2}\s+\d{2}\s+\d{2}\b"
        for entry in config["numbers"]
    )
    result, counts = anonymizer.anonymize(text)
    assert "<PHONE_NUMBER_" in result
    assert counts["numbers_found"] >= 3
    assert counts["patterns_found"] >= 1


def test_anonymize_address(anonymizer):
    text = "Lives at 123 Oneway St, Springfield, US"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    result, counts = anonymizer.anonymize(text)
    assert "<ADDRESS_1>" in result
    assert counts["addresses_found"] >= 1
    assert counts["addresses_replaced"] >= 1


def test_anonymize_danish_address():
    anonymizer = Anonymizer(language="da")
    text = "Bor på Søndergade 14,1.tv, 8600 Silkeborg"
    anonymizer.detect_entities([text])
    assert anonymizer.counts["addresses_found"] >= 1
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    result, counts = anonymizer.anonymize(text)
    assert "<ADDRESS_1>" in result
    assert counts["addresses_found"] >= 1
    assert counts["addresses_replaced"] >= 1


def test_anonymize_cpr(anonymizer):
    text = "CPR: 123456-1234"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    result, counts = anonymizer.anonymize(text)
    assert "<CPR_NUMBER_1>" in result
    assert counts["cpr_found"] >= 1
    assert counts["cpr_replaced"] >= 1


def test_anonymize_mixed_content(anonymizer):
    text = "Contact John Doe at 1234567890 or Jane Smith via 12 34 56 78. Jon Doe and Jane Smyth share details at 123 Oneway St, Springfield, US. CPR: 123456-1234. Additional phone: 1234567"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(anonymizer.entities.model_dump(exclude_none=True))
    result, counts = anonymizer.anonymize(text)
    assert "<PHONE_NUMBER_" in result
    assert "<ADDRESS_" in result
    assert "<CPR_NUMBER_" in result
    assert counts["names_found"] >= 4
    assert counts["names_replaced"] >= 4
    assert counts["numbers_found"] >= 3
    assert counts["patterns_found"] >= 1
    assert counts["addresses_found"] >= 1
    assert counts["addresses_replaced"] >= 1
    assert counts["cpr_found"] >= 1
    assert counts["cpr_replaced"] >= 1


def test_cli_extract(runner, tmp_path):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    input_file.write_text(
        "Hello John Doe and Jon Doe, CPR: 123456-1234"
    )
    result = runner.invoke(main, ["ex", "--file", str(input_file), "--config", str(config_file)])
    assert result.exit_code == 0
    assert "Names found: 2" in result.output  # Grouped
    assert "CPR found: 1" in result.output
    assert config_file.exists()
    with open(config_file, "r") as f:
        config = yaml.safe_load(f)
        assert len(config["names"]) >= 1
        assert any(
            "123456-1234" in entry["variants"] for entry in config["cpr"]
        )


def test_cli_anonymize(runner, tmp_path):
    input_file = tmp_path / "input.md"
    config_file = tmp_path / "config.yaml"
    output_file = tmp_path / "output.md"
    original_text = "Hello John Doe and Jon Doe, CPR: 123456-1234"
    input_file.write_text(original_text)

    # First extract to generate config
    runner.invoke(main, ["ex", "--file", str(input_file), "--config", str(config_file)])

    # Modify the input file to add new content
    modified_text = original_text + " and John Doe again, and new person Alice, new CPR: 987654-4321"
    input_file.write_text(modified_text)

    result = runner.invoke(
        main, ["an", "--file", str(input_file), "--config", str(config_file), "--output", str(output_file)]
    )
    assert result.exit_code == 0
    assert "Names replaced: 3" in result.output
    assert "CPR replaced: 1" in result.output
    assert output_file.exists()
    with open(output_file, "r") as f:
        content = f.read()
        assert "<PERSON_1>" in content
        assert "<CPR_NUMBER_1>" in content
        assert "Alice" in content
        assert "987654-4321" in content
        assert content.count("<PERSON_1>") == 3  # Variants of John Doe
