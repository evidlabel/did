"""Replacement logic for Anonymizer."""

import re

from . import entity_types

# Optional trailing possessive/plural suffix consumed silently with a person
# name so forms like "Doe's", "Does", "Doe'" are replaced without leaking and
# without being listed as explicit variants. Handles straight and curly
# (U+2019) apostrophes.
_APOS = "['\u2019]"  # straight + curly apostrophe
POSSESSIVE_SUFFIX = f"(?:{_APOS}[sS]|{_APOS}|[sS])?"

# Re-exported from the registry so existing importers keep working; the types
# themselves are declared in exactly one place.
CATEGORY_MAPPING = entity_types.CATEGORY_MAPPING
PREFIX_MAP = entity_types.PREFIX_MAP


def _case_forms(token: str) -> str:
    """Pattern for one word as written, Capitalized, or ALL-CAPS.

    Headings and signature blocks write names in capitals ("JOHN DOE"), which
    an exact match leaks. A lone word is not matched case-insensitively
    outright: short names double as common words ("Hans"/"hans", "Mark"/"mark").
    """
    forms = dict.fromkeys([token, token[:1].upper() + token[1:], token.upper()])
    return "(?:" + "|".join(re.escape(form) for form in forms) + ")"


def variant_pattern(cat: str, variant: str) -> re.Pattern:
    """Compiled pattern that replaces *variant* of category *cat*.

    Multi-word names, e-mail addresses, URLs, and codes match in any case —
    "JOHN DOE", "John DOE", "john doe" are all the same person. A single-word
    name matches as written, Capitalized, or ALL-CAPS.
    """
    parts = variant.split()
    if cat in ("person", "organization", "location"):
        if len(parts) > 1:
            pattern = r"\s+".join(re.escape(part) for part in parts)
            if cat == "person":
                # Possessive/plural suffix consumed silently after the name.
                pattern += POSSESSIVE_SUFFIX
            return re.compile(pattern, re.IGNORECASE)
        word = _case_forms(variant)
        if cat == "person":
            # \b after the optional suffix keeps single-token names from
            # bleeding into longer words (e.g. "Anvisende").
            return re.compile(r"\b" + word + POSSESSIVE_SUFFIX + r"\b")
        return re.compile(word)
    if cat == "url":
        pattern = r"\s+".join(re.escape(part) for part in parts) or re.escape(variant)
        return re.compile(pattern, re.IGNORECASE)
    return re.compile(re.escape(variant), re.IGNORECASE)


def anonymize(anonymizer, text: str) -> tuple:
    """Anonymize text by replacing known variants with Typst-style parameters in a single pass."""
    anonymizer.counts = dict.fromkeys(anonymizer.counts, 0)

    # Collect all potential replacements as list of (start, end, repl, cat, variant)
    all_matches = []
    for cat in CATEGORY_MAPPING:
        entities = getattr(anonymizer.entities, cat)
        for ent_idx, entity in enumerate(entities, 1):
            for v_idx, variant in enumerate(entity.variants, 1):
                pattern = variant_pattern(cat, variant)
                for match in pattern.finditer(text):
                    start, end = match.start(), match.end()
                    repl = f"#({PREFIX_MAP[cat]}{ent_idx}V{v_idx})"
                    all_matches.append((start, end, repl, cat, variant))

    # Sort matches by start position ascending, then by length descending (for overlap resolution)
    all_matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    # Resolve overlaps: keep only non-overlapping matches, preferring longer ones
    selected_matches = []
    last_end = 0
    for match in all_matches:
        start, end = match[0], match[1]
        if start >= last_end:
            selected_matches.append(match)
            last_end = end
            found_key = CATEGORY_MAPPING[match[3]].replace("_replaced", "_found")
            replaced_key = CATEGORY_MAPPING[match[3]]
            anonymizer.counts[found_key] += 1
            anonymizer.counts[replaced_key] += 1

    # Sort selected by start descending to replace from end
    selected_matches.sort(key=lambda x: x[0], reverse=True)

    anonymized = list(text)
    for start, end, repl, _, _ in selected_matches:
        anonymized[start:end] = list(repl)

    return "".join(anonymized), anonymizer.counts
