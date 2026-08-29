"""Tests for output verification (no spaCy for tiers A and B).

The point of these tests is that the verifier is *looser* than the replacer.
Every tier A case below is text that ``did.core.replacement.anonymize`` leaves
behind, because its patterns are ``\\b``-anchored and case-sensitive. If the
verifier ever stops finding them it has become tautological and worthless.
"""

from did.core import replacement, verification
from did.core.anonymizer import Anonymizer
from did.core.verification import (
    KIND_RETAINED_EXCLUSION,
    KIND_SURVIVING_VARIANT,
    Finding,
    find_retained_exclusions,
    find_surviving_variants,
    find_suspected_entities,
    mask_tokens,
    variant_probe,
    verify,
)

CONFIG = {
    "PERSON": [
        {"id": "PERSON_1", "variants": ["John Doe", "Doe"]},
        {"id": "PERSON_2", "variants": ["Jane Roe"]},
    ],
    "LOCATION": [{"id": "LOCATION_1", "variants": ["Storetorv 10"]}],
}


def _replace(text, config=CONFIG):
    """Run the real replacement pass, as the pipeline would."""
    anonymizer = Anonymizer.__new__(Anonymizer)
    anonymizer.counts = dict.fromkeys(
        [
            f"{c}_{s}"
            for c in replacement.CATEGORY_MAPPING
            for s in ("found", "replaced")
        ]
        + list(replacement.CATEGORY_MAPPING.values()),
        0,
    )
    anonymizer.load_replacements(config)
    return replacement.anonymize(anonymizer, text)[0]


# ------------------------------------------------------------- mask_tokens ---
def test_mask_tokens_removes_every_prefix():
    text = "#(P1V1) #(PH2V1) #(DT3V4) #(URL1V1) #(GN9V9) #(A1V1)"
    assert not any(ch == "#" for ch in mask_tokens(text))


def test_tokens_alone_are_never_flagged():
    """A correctly pseudonymized document must verify clean."""
    report = verify({"d": "#(P1V1) drove #(P1V2) home to #(A1V1) on #(DT1V1)."}, CONFIG)
    assert report.is_clean
    assert report.leaks == []
    assert report.suspected == []


# --------------------------------------------------- tier A: surviving variants ---
def test_danish_compound_leak_survives_replacement_and_is_caught():
    """``Doesagen`` — a surname inside a Danish compound noun.

    The replacer anchors single-token names with ``\\b``, so a name glued to a
    following noun is left in the clear. For a tool that targets Danish case
    files this is the leak class that matters most.
    """
    output = _replace("John Doe drove. Doesagen blev afgjort i dag.")
    assert "Doesagen" in output, "replacement was expected to leave this behind"

    findings = find_surviving_variants(output, CONFIG, document="d.typ")
    assert [f.text for f in findings] == ["Doe"]
    assert findings[0].kind == KIND_SURVIVING_VARIANT
    assert findings[0].entity_id == "PERSON_1"
    assert findings[0].entity_type == "PERSON"
    assert findings[0].document == "d.typ"


def test_compound_leak_found_when_only_the_full_name_is_configured():
    """The realistic case: detection yields full names, the surname leaks.

    Real configs from ``did extract`` contain ``John Doe`` and ``JOHN DOE`` but
    never a bare ``Doe``. Without probing name tokens the compound below is
    reported as clean, which is the failure mode this whole module exists to
    prevent.
    """
    config = {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe", "JOHN DOE"]}]}
    findings = find_surviving_variants("Doesagen blev afgjort af retten.", config)
    assert [f.text for f in findings] == ["Doe"]
    assert findings[0].entity_id == "PERSON_1"


def test_name_buried_inside_an_unrelated_word_is_not_reported():
    """``Roe`` inside ``vedrører`` is noise, not a leak.

    Every real leak form appends to the name, so the probe anchors its left edge
    only. Losing that anchor floods the report with matches like this one.
    """
    assert find_surviving_variants("Sagen vedrorer parterne.", CONFIG) == []
    assert find_surviving_variants("Sagen vedrører parterne.", CONFIG) == []


def test_particles_are_not_probed_as_standalone_tokens():
    """``van`` identifies nobody, and would match half the document."""
    config = {"PERSON": [{"id": "PERSON_1", "variants": ["Kees van der Berg"]}]}
    findings = find_surviving_variants("De van der vejen er lang.", config)
    assert [f.text for f in findings] == []


def test_hyphenated_compound_leak_is_caught():
    """``Doe-sagen`` when only the full name is configured."""
    config = {"PERSON": [{"id": "PERSON_1", "variants": ["John Doe"]}]}
    output = _replace("Se Doe-sagen her.", config)
    assert "Doe-sagen" in output, "replacement was expected to leave this behind"
    findings = find_surviving_variants(output, config)
    assert [f.text for f in findings] == ["Doe"]
    assert findings[0].entity_id == "PERSON_1"


def test_case_variant_leak_survives_replacement_and_is_caught():
    """An ALL-CAPS heading form is case-sensitively missed by the replacer."""
    output = _replace("John Doe said. JOHN DOE signed it.")
    assert "JOHN DOE" in output, "replacement was expected to leave this behind"
    assert [f.text for f in find_surviving_variants(output, CONFIG)] == ["JOHN DOE"]


def test_hyphenated_linebreak_leak_survives_replacement_and_is_caught():
    """A name broken across a line break by PDF extraction still leaks."""
    output = _replace("Then John Do-\ne was seen.")
    assert "John Do-\ne" in output, "replacement was expected to leave this behind"
    findings = find_surviving_variants(output, CONFIG)
    assert [f.text for f in findings] == ["John Do-\ne"]
    assert findings[0].entity_id == "PERSON_1"


def test_flexible_whitespace_is_matched():
    """Runs of whitespace inside a variant do not defeat the probe.

    The replacer already handles this one; the verifier must not regress below
    it, or re-extracted text would verify clean when it is not.
    """
    findings = find_surviving_variants("Lives at Storetorv  10 today.", CONFIG)
    assert [f.text for f in findings] == ["Storetorv  10"]
    assert findings[0].entity_type == "LOCATION"


def test_overlapping_variants_report_once_preferring_the_longer():
    """``John Doe`` and its short variant ``Doe`` must not double-report."""
    findings = find_surviving_variants("Here is John Doe again.", CONFIG)
    assert [f.text for f in findings] == ["John Doe"]


def test_findings_carry_line_and_context():
    findings = find_surviving_variants("line one\nline two\nJohn Doe here", CONFIG)
    assert findings[0].line == 3
    assert "John Doe here" in findings[0].context


def test_short_variants_are_not_probed():
    """Two-character variants would flood the report with noise."""
    assert variant_probe("Jo") is None
    assert variant_probe("") is None
    assert variant_probe("Doe") is not None


# -------------------------------------------------- tier B: suspected entities ---
def test_suspected_entity_absent_from_config_is_found():
    findings = find_suspected_entities(
        "#(P1V1) met Ulrik Bagger yesterday.", known=["John Doe"]
    )
    assert any("Ulrik Bagger" in f.text for f in findings)


def test_known_values_are_not_reported_as_suspected():
    findings = find_suspected_entities("John Doe here.", known=["John Doe"])
    assert [f.text for f in findings] == []


# ------------------------------------------------- retained exclusions ---
def test_retained_exclusion_is_not_a_leak():
    """A reviewer's "do not pseudonymize" decision is recorded, not flagged."""
    report = verify(
        {"d": "Signed by Mette Sagsbehandler for #(P1V1)."},
        {"PERSON": []},
        not_names=["Mette Sagsbehandler"],
    )
    assert report.leaks == []
    assert [f.text for f in report.retained] == ["Mette Sagsbehandler"]
    assert report.retained[0].kind == KIND_RETAINED_EXCLUSION


def test_find_retained_exclusions_directly():
    findings = find_retained_exclusions("Hi Jane Roe.", ["Jane Roe"], document="d")
    assert [f.text for f in findings] == ["Jane Roe"]
    assert findings[0].document == "d"


# ---------------------------------------------------------------- report ---
def test_report_counts_and_cleanliness():
    report = verify({"d.typ": "John Doe was here."}, CONFIG)
    assert not report.is_clean
    counts = report.counts()
    assert counts["leaks"] == 1
    assert counts["clean"] is False
    assert counts["deep"] is False
    assert "1 surviving identifier" in report.summary()


def test_clean_report_summary():
    report = verify({"d": "#(P1V1) only."}, CONFIG)
    assert report.summary() == "Output verification: clean."


def test_retained_alone_does_not_make_a_report_unclean():
    report = verification.VerificationReport(
        retained=[Finding(KIND_RETAINED_EXCLUSION, "X", "d", 1, "X")]
    )
    assert report.is_clean


def test_safe_dict_omits_the_offending_text():
    """The agent-safe report must never carry the PII it is reporting."""
    report = verify({"d.typ": "John Doe was here."}, CONFIG)
    safe = report.to_safe_dict()
    assert "John Doe" not in str(safe)
    assert safe["leaks"] == 1
    assert safe["findings"][0]["line"] == 1
    assert safe["findings"][0]["entity_id"] == "PERSON_1"

    full = report.to_dict()
    assert full["findings"][0]["text"] == "John Doe"


def test_verify_labels_documents_by_name(tmp_path):
    from pathlib import Path

    report = verify({Path(tmp_path / "case.typ"): "John Doe here."}, CONFIG)
    assert report.leaks[0].document == "case.typ"


def test_verify_spans_multiple_documents():
    report = verify({"a": "John Doe.", "b": "Jane Roe."}, CONFIG)
    assert sorted(f.document for f in report.leaks) == ["a", "b"]
