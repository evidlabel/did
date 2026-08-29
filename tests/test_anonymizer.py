"""Tests for core Anonymizer functionality."""

from collections import defaultdict
from types import SimpleNamespace

import pytest
from ruamel import yaml

from did.core.anonymizer import Anonymizer
from did.core.detection import detect_entities
from did.core.models import Config


def test_detection_preserves_reported_confidence():
    result = SimpleNamespace(
        start=0,
        end=8,
        score=0.873,
        entity_type="PERSON",
    )
    detector = SimpleNamespace(
        model_map={"en": "test-model"},
        language="en",
        analyzer=SimpleNamespace(analyze=lambda **_kwargs: [result]),
        preprocess_text=lambda text: (text, lambda start, end: (start, end)),
        counts=defaultdict(int),
        entities=Config(),
    )

    detect_entities(detector, ["John Doe"])

    assert detector.entities.person[0].confidence == 0.873


def test_missing_spacy_models_do_not_invoke_pip(monkeypatch):
    """Presidio must not `python -m pip` when models are absent.

    uv venvs have no pip, so spaCy's download path SystemExits inside the GUI
    worker thread (``No module named pip`` / QThread destroyed).
    """
    import spacy.cli

    monkeypatch.setattr("spacy.util.is_package", lambda _name: False)
    download_calls = []

    def fake_download(*args, **kwargs):
        download_calls.append((args, kwargs))
        raise AssertionError("spacy.cli.download must not be called")

    monkeypatch.setattr(spacy.cli, "download", fake_download)

    with pytest.raises(ValueError, match="uv sync --extra models"):
        Anonymizer(language="en")
    assert download_calls == []


@pytest.fixture
def anonymizer():
    return Anonymizer(language="en")


def test_extract_empty_text(anonymizer):
    anonymizer.detect_entities([""])
    yaml_obj = yaml.YAML()
    config_str = anonymizer.generate_yaml()
    config = yaml_obj.load(config_str)
    assert config["PERSON"] == []
    assert config["EMAIL_ADDRESS"] == []
    assert config["LOCATION"] == []
    assert config["PHONE_NUMBER"] == []
    assert config["DATE_NUMBER"] == []
    assert config["ID_NUMBER"] == []
    assert config["CODE_NUMBER"] == []
    assert config["GENERAL_NUMBER"] == []
    assert all(count == 0 for count in anonymizer.counts.values())


def test_anonymize_name_exact(anonymizer):
    text = "Hello John Doe, how are you?"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "#(P1V" in result
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
    assert "#(P1V" in result
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
    detected_entities = [
        entry
        for entries in config.values()
        if isinstance(entries, list)
        for entry in entries
    ]
    assert any(
        "1234567890" in variant
        for entry in detected_entities
        for variant in entry["variants"]
    )
    assert any(
        "12 34 56 78" in variant
        for entry in detected_entities
        for variant in entry["variants"]
    )
    result, counts = anonymizer.anonymize(text)
    assert "1234567890" not in result
    assert "1234567" not in result
    assert "12 34 56 78" not in result
    assert sum(value for key, value in counts.items() if key.endswith("_replaced")) >= 3


def test_anonymize_address(anonymizer):
    text = "Lives at 123 Oneway St, Springfield, US"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "#(A1V" in result
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
    assert "#(A1V" in result
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
    assert "#(ID1V" in result
    assert "123456-1234" not in result
    assert counts["id_number_replaced"] >= 1


def test_anonymize_danish_compound_name():
    anonymizer = Anonymizer(language="da")
    text = "Stine Louise Eising von Christierson bor i København."
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "#(P1V" in result
    assert counts["person_found"] >= 1
    assert counts["person_replaced"] >= 1
    assert "Stine" not in result


def test_possessive_handled_silently(anonymizer):
    """Possessive/suffix forms are matched without being listed as explicit variants."""
    config = {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    anonymizer.load_replacements(config)
    text = "Met John Doe. John Doe's car. John Does house."
    result, counts = anonymizer.anonymize(text)
    assert "John" not in result
    assert "Doe" not in result
    assert result.count("#(P1V1)") == 3
    assert counts["person_replaced"] == 3


def test_single_word_possessive_no_overmatch(anonymizer):
    """Suffix consumption must not bleed into longer words."""
    config = {"PERSON": [{"id": "PERSON_1", "variants": ["Anvise"]}]}
    anonymizer.load_replacements(config)
    text = "Anvise and Anvises but not Anvisende."
    result, _ = anonymizer.anonymize(text)
    assert result.count("#(P1V1)") == 2
    assert "Anvisende" in result


def test_detection_omits_possessive_variants(anonymizer):
    """Detected person variants should not include generated possessive forms."""
    text = "John Doe and Jon Doe were mentioned."
    anonymizer.detect_entities([text])
    for entity in anonymizer.entities.person:
        for variant in entity.variants:
            assert not variant.endswith("'s")
            assert not variant.endswith("'")


def test_anonymize_mixed_content(anonymizer):
    text = "Contact John Doe at 1234567890 or Jane Smith via 12 34 56 78. Jon Doe and Jane Smyth share details at 123 Oneway St, Springfield, US. CPR: 123456-1234. Additional phone: 1234567"
    anonymizer.detect_entities([text])
    anonymizer.load_replacements(
        anonymizer.entities.model_dump(by_alias=True, exclude_none=True)
    )
    result, counts = anonymizer.anonymize(text)
    assert "#(PH" in result or "#(GN" in result
    assert "#(A" in result
    for name in ("John Doe", "Jane Smith", "Jon Doe", "Jane Smyth"):
        assert name not in result
    assert counts["person_replaced"] >= 3
    assert counts["phone_number_found"] + counts["general_number_found"] >= 2
    assert counts["location_found"] >= 1
    assert counts["location_replaced"] >= 1
