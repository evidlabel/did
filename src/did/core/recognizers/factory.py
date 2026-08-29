"""Config-driven factory for custom Presidio PatternRecognizers.

Each entry in RECOGNIZER_SPECS declares entity type, optional context, and a
pattern builder. ``build_recognizers(language)`` is the deep seam; individual
``get_*_recognizer`` helpers remain for direct tests and thin module re-exports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from presidio_analyzer import Pattern, PatternRecognizer

PatternBuilder = Callable[[str], list[Pattern]]
ContextBuilder = Callable[[str], list[str]]


@dataclass(frozen=True)
class RecognizerSpec:
    """Declarative description of one custom recognizer."""

    entity: str
    patterns: PatternBuilder
    context: ContextBuilder | None = None
    name: str = ""  # optional stable id for logging/tests


def _patterns_general_number(_language: str) -> list[Pattern]:
    return [
        Pattern(
            name="aggressive_number",
            regex=r"\b[\d\s\-.,+/()]*\d[\d\s\-.,+/()]*\b",
            score=0.7,
        ),
        Pattern(name="single_digit", regex=r"\b\d\b", score=0.6),
        Pattern(name="letter_digit", regex=r"\b[A-Z]{1,4}\d{2,10}", score=0.8),
        Pattern(name="digit_sequence", regex=r"\d{2,20}", score=0.85),
    ]


def _patterns_date_number(_language: str) -> list[Pattern]:
    return [
        Pattern(name="full_date", regex=r"\b\d{4}-\d{2}-\d{2}\b", score=0.95),
        Pattern(name="dotted_date", regex=r"\b\d{2}\.\d{2}\.\d{4}\b", score=0.95),
        Pattern(
            name="short_date",
            regex=r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
            score=0.9,
        ),
        Pattern(name="year_only", regex=r"\b\d{4}\b", score=0.8),
    ]


def _patterns_id_number(language: str) -> list[Pattern]:
    patterns = [
        Pattern(name="iban", regex=r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b", score=0.99),
    ]
    if language == "da":
        patterns.extend(
            [
                Pattern(name="cpr", regex=r"\b\d{6}-\d{4}\b", score=1.0),
                Pattern(name="dk_iban", regex=r"\bDK\d{18}\b", score=1.0),
                Pattern(name="long_number_da", regex=r"\b\d{15,}\b", score=0.95),
            ]
        )
    else:
        patterns.extend(
            [
                Pattern(name="ssn", regex=r"\b\d{3}-\d{2}-\d{4}\b", score=1.0),
                Pattern(name="gb_iban", regex=r"\bGB[A-Z0-9]{20,24}\b", score=0.99),
                Pattern(
                    name="us_account",
                    regex=r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,20}\b",
                    score=0.95,
                ),
                Pattern(name="long_number_en", regex=r"\b\d{10,}\b", score=0.90),
            ]
        )
    patterns.extend(
        [
            Pattern(name="long_digits", regex=r"\d{10,}", score=0.90),
            Pattern(
                name="id_code",
                regex=r"\b\d{3,}[\-\d]{3,}\s*\(\d{3,}\)\b",
                score=0.85,
            ),
            Pattern(name="year_based_id", regex=r"\b\d{4}-\d{5}\b", score=0.85),
        ]
    )
    return patterns


def _context_id_number(_language: str) -> list[str]:
    return ["account", "iban", "ssn", "cpr", "konto"]


def _patterns_code_number(_language: str) -> list[Pattern]:
    return [
        Pattern(name="parenthesized_code", regex=r"\(\d{6}\)\b", score=0.9),
        Pattern(
            name="channel_identifier",
            regex=r"\b\d{1,2},\d{1,2}\.[a-zA-Z]{2,3}\b",
            score=0.85,
        ),
    ]


def _patterns_location(language: str) -> list[Pattern]:
    if language == "da":
        return [
            Pattern(
                name="multiline_danish_address",
                regex=(
                    r"\b[A-Z\u00c6\u00d8\u00c5][a-z\u00e6\u00f8\u00e5\u00e9\u00fc]+"
                    r"\s*(?:gade|vej|str\u00e6de|plads|torv|all\u00e9|boulevard)\s+\d+"
                    r"\n\d{4}\s+[A-Z\u00c6\u00d8\u00c5][a-z\u00e6\u00f8\u00e5\u00e9\u00fc]+"
                    r"(?:\s+[A-Z\u00c6\u00d8\u00c5][a-z\u00e6\u00f8\u00e5\u00e9\u00fc]+)*"
                    r"[.\s]*\b"
                ),
                score=0.98,
            ),
            Pattern(
                name="single_address_line",
                regex=(
                    r"\b[A-Z\u00c6\u00d8\u00c5][a-z\u00e6\u00f8\u00e5\u00e9\u00fc]+"
                    r"\s*(?:gade|vej|str\u00e6de|plads|torv|all\u00e9|boulevard)\s+\d+"
                    r"[.\s]*\b"
                ),
                score=0.9,
            ),
        ]
    return [
        Pattern(
            name="multiline_english_address",
            regex=(
                r"\b\d+\s+[A-Z][a-z]+(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|"
                r"Boulevard|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place))?(?:\s+[A-Za-z0-9]+)*"
                r"\n[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:,\s*[A-Z]{2})?\s*\d{5}(?:-\d{4})?\b"
            ),
            score=0.98,
        ),
        Pattern(
            name="single_english_address",
            regex=(
                r"\b\d+\s+[A-Z][a-z]+(?:\s+(?:St|Ave|Rd|Blvd|Dr|Ln|Ct|Pl))?"
                r"(?:\s+[A-Za-z0-9]+)*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*"
                r"[A-Z]{2}\s*\d{5}(?:-\d{4})?\b"
            ),
            score=0.95,
        ),
        Pattern(
            name="city_state_zip",
            regex=r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?\b",
            score=0.90,
        ),
        Pattern(
            name="street_address",
            regex=(
                r"\b\d+\s+[A-Z][a-z]+(?:\s+(?:St|Ave|Rd|Blvd|Dr|Ln|Ct|Pl))?"
                r"(?:\s+[A-Za-z0-9]+)*\b"
            ),
            score=0.85,
        ),
    ]


def _context_location(language: str) -> list[str]:
    if language == "da":
        return ["adresse", "vej", "gade"]
    return ["address", "street", "city", "state", "zip"]


def _patterns_phone_number(_language: str) -> list[Pattern]:
    return [
        Pattern(
            name="danish_phone_with_45_spaced",
            regex=r"\+45\s\d{4}\s\d{4}",
            score=0.95,
        ),
        Pattern(
            name="danish_phone_with_45_spaced_2",
            regex=r"\+45\s\d{2}\s\d{2}\s\d{2}\s\d{2}",
            score=0.95,
        ),
        Pattern(
            name="danish_phone_with_45_8_digits",
            regex=r"\+45\s?\d{8}",
            score=0.95,
        ),
        Pattern(
            name="danish_phone_spaced",
            regex=r"\d{4}\s\d{4}",
            score=0.9,
        ),
        Pattern(
            name="danish_phone_2_2_2_2",
            regex=r"\d{2}\s\d{2}\s\d{2}\s\d{2}",
            score=0.92,
        ),
        Pattern(
            name="danish_phone_8_digits",
            regex=r"\b\d{8}\b",
            score=0.85,
        ),
    ]


def _context_phone_number(_language: str) -> list[str]:
    return ["telefon", "mobil", "phone", "tel"]


def _patterns_url(_language: str) -> list[Pattern]:
    return [
        Pattern(
            name="full_url",
            regex=r"https?://(?:[a-zA-Z0-9-._~:/?#\[\]@!$&'()*+,;%=]+)(?:\.{3})?",
            score=0.99,
        ),
        Pattern(
            name="www_url",
            regex=r"www\.(?:[a-zA-Z0-9-._~:/?#\[\]@!$&'()*+,;%=]+)(?:\.{3})?",
            score=0.98,
        ),
        Pattern(
            name="domain_url",
            regex=(
                r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}"
                r"(?:/[a-zA-Z0-9./?=&%#~-]*)?(?:\.{3})?"
            ),
            score=0.95,
        ),
        Pattern(
            name="query_url",
            regex=r"[a-zA-Z0-9-]+\.[a-zA-Z]{2,}\?[^\\s]+(?:\.{3})?",
            score=0.97,
        ),
    ]


def _context_url(_language: str) -> list[str]:
    return ["http", "https", "www", "url", "link", "site"]


RECOGNIZER_SPECS: tuple[RecognizerSpec, ...] = (
    RecognizerSpec(
        name="general_number",
        entity="GENERAL_NUMBER",
        patterns=_patterns_general_number,
    ),
    RecognizerSpec(
        name="date_number",
        entity="DATE_NUMBER",
        patterns=_patterns_date_number,
    ),
    RecognizerSpec(
        name="id_number",
        entity="ID_NUMBER",
        patterns=_patterns_id_number,
        context=_context_id_number,
    ),
    RecognizerSpec(
        name="code_number",
        entity="CODE_NUMBER",
        patterns=_patterns_code_number,
    ),
    RecognizerSpec(
        name="location",
        entity="LOCATION",
        patterns=_patterns_location,
        context=_context_location,
    ),
    RecognizerSpec(
        name="phone_number",
        entity="PHONE_NUMBER",
        patterns=_patterns_phone_number,
        context=_context_phone_number,
    ),
    RecognizerSpec(
        name="url",
        entity="URL",
        patterns=_patterns_url,
        context=_context_url,
    ),
)


def build_recognizer(spec: RecognizerSpec, language: str) -> PatternRecognizer:
    """Build one PatternRecognizer from a declarative *spec*."""
    kwargs: dict = {
        "supported_entity": spec.entity,
        "patterns": spec.patterns(language),
        "supported_language": language,
    }
    if spec.context is not None:
        kwargs["context"] = spec.context(language)
    return PatternRecognizer(**kwargs)


def build_recognizers(language: str) -> list[PatternRecognizer]:
    """Return all custom recognizers for *language* (primary factory seam)."""
    return [build_recognizer(spec, language) for spec in RECOGNIZER_SPECS]


def get_custom_recognizers(language: str) -> list[PatternRecognizer]:
    """Backward-compatible alias for :func:`build_recognizers`."""
    return build_recognizers(language)


def get_general_number_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[0], language)


def get_date_number_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[1], language)


def get_id_number_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[2], language)


def get_code_number_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[3], language)


def get_location_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[4], language)


def get_phone_number_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[5], language)


def get_url_recognizer(language: str) -> PatternRecognizer:
    return build_recognizer(RECOGNIZER_SPECS[6], language)
