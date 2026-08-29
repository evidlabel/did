"""Tests for the single entity-type registry.

Six modules used to enumerate token prefixes by hand. They disagreed: the
Typst exporter's list omitted ``organization`` while the replacer's included
it, so ``#(O1V1)`` reached document bodies with no ``#let O1V1`` ever written.
These tests exist to keep the registry the only place a type is declared.
"""

import re

from did.core import entity_types
from did.core.entity_types import (
    CATEGORY_MAPPING,
    ENTITY_TYPES,
    NUMBER_CATEGORIES,
    PLACEHOLDER_WORDS,
    PREFIX_MAP,
    prefix_pattern,
    token_re,
)


def test_every_declared_type_is_unique():
    """A duplicated prefix or category would silently merge two entity types."""
    assert len({e.category for e in ENTITY_TYPES}) == len(ENTITY_TYPES)
    assert len({e.prefix for e in ENTITY_TYPES}) == len(ENTITY_TYPES)
    assert len({e.config_key for e in ENTITY_TYPES}) == len(ENTITY_TYPES)


def test_organization_is_registered():
    """The type whose omission caused undefined #let O1V1 in exported Typst."""
    org = next(e for e in ENTITY_TYPES if e.category == "organization")
    assert org.prefix == "O"
    assert org.config_key == "ORGANIZATION"
    assert org.detected


def test_document_title_is_registered_but_never_detected():
    """Titles are assigned per document, not found by the model."""
    title = next(e for e in ENTITY_TYPES if e.category == "document_title")
    assert title.prefix == "DOC"
    assert title.config_key == "DOCUMENT_TITLE"
    assert not title.detected
    assert not title.number_like


def test_derived_maps_cover_every_type():
    assert set(PREFIX_MAP) == {e.category for e in ENTITY_TYPES}
    assert set(CATEGORY_MAPPING) == {e.category for e in ENTITY_TYPES}
    assert set(PLACEHOLDER_WORDS) == {e.prefix for e in ENTITY_TYPES}
    assert NUMBER_CATEGORIES == {e.category for e in ENTITY_TYPES if e.number_like}


def test_category_mapping_points_at_replacement_counters():
    for category, key in CATEGORY_MAPPING.items():
        assert key == f"{category}_replaced"


def test_token_re_matches_every_registered_prefix():
    pattern = token_re()
    for entity in ENTITY_TYPES:
        match = pattern.fullmatch(f"#({entity.prefix}1V2)")
        assert match, f"{entity.prefix} is not matched by token_re()"
        assert match.group(1) == entity.prefix
        assert match.group(2) == "1"
        assert match.group(3) == "2"


def test_longer_prefixes_win_over_their_own_shorter_forms():
    """``PH`` starts with ``P``; alternation order decides which one matches."""
    assert token_re().fullmatch("#(PH1V1)").group(1) == "PH"
    assert token_re().fullmatch("#(DOC1V1)").group(1) == "DOC"
    assert token_re().fullmatch("#(DT1V1)").group(1) == "DT"


def test_prefix_pattern_is_a_usable_alternation():
    """The highlighter builds a combined regex from this fragment."""
    combined = re.compile(rf"#\({prefix_pattern()}\d+V\d+\)")
    assert combined.fullmatch("#(DOC12V3)")
    assert combined.fullmatch("#(P1V1)")
    assert not combined.fullmatch("#(ZZ1V1)")


def test_registry_is_the_only_declaration_site():
    """Nothing may hand-maintain a parallel prefix list.

    Guards the failure mode this module exists to prevent: a second list that
    drifts out of step with the registry.
    """
    for module_name in ("replacement", "verification"):
        module = __import__(f"did.core.{module_name}", fromlist=["x"])
        source = open(module.__file__, encoding="utf-8").read()
        assert "URL|PH|DT" not in source, (
            f"{module_name} still hardcodes a prefix alternation"
        )

    # Every consumer's map must be the registry's, not a copy of it. A copy is
    # what let export_to_typst drift and drop `organization`.
    from did.core import config, replacement
    from gdid import pipeline

    assert config.FIELD_MAPPING == entity_types.CATEGORY_TO_CONFIG_KEY
    assert replacement.PREFIX_MAP == PREFIX_MAP
    assert replacement.CATEGORY_MAPPING == CATEGORY_MAPPING
    assert pipeline.PLACEHOLDER_WORDS == PLACEHOLDER_WORDS


def test_detected_types_are_what_detection_asks_the_model_for():
    detected = {e.config_key for e in ENTITY_TYPES if e.detected}
    assert "DOCUMENT_TITLE" not in detected
    assert "PERSON" in detected
    assert entity_types.DOCUMENT_TITLE.config_key == "DOCUMENT_TITLE"
