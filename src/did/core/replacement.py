"""Replacement logic for Anonymizer."""

import re

from . import entity_types

# Optional trailing possessive/plural suffix consumed silently with a person
# name so forms like "Doe's", "Does", "Doe'" are replaced without leaking and
# without being listed as explicit variants. Handles straight and curly
# (U+2019) apostrophes.
_APOS = "['\u2019]"  # straight + curly apostrophe
POSSESSIVE_SUFFIX = f"(?:{_APOS}s|{_APOS}|s)?"

# Re-exported from the registry so existing importers keep working; the types
# themselves are declared in exactly one place.
CATEGORY_MAPPING = entity_types.CATEGORY_MAPPING
PREFIX_MAP = entity_types.PREFIX_MAP


def anonymize(anonymizer, text: str) -> tuple:
    """Anonymize text by replacing known variants with Typst-style parameters in a single pass."""
    anonymizer.counts = dict.fromkeys(anonymizer.counts, 0)

    # Collect all potential replacements as list of (start, end, repl, cat, variant)
    all_matches = []
    for cat in CATEGORY_MAPPING:
        entities = getattr(anonymizer.entities, cat)
        for ent_idx, entity in enumerate(entities, 1):
            for v_idx, variant in enumerate(entity.variants, 1):
                escaped = re.escape(variant)
                if cat == "person":
                    parts = variant.split()
                    if len(parts) > 1:
                        # Possessive/plural suffix consumed silently after the name.
                        pattern = (
                            r"\s+".join(re.escape(p) for p in parts) + POSSESSIVE_SUFFIX
                        )
                    else:
                        # \b after the optional suffix keeps single-token names
                        # from bleeding into longer words (e.g. "Anvisende").
                        pattern = r"\b" + escaped + POSSESSIVE_SUFFIX + r"\b"
                elif cat in ["organization", "location", "url"]:
                    parts = variant.split()
                    if len(parts) > 1:
                        pattern = r"\s+".join(re.escape(p) for p in parts)
                    else:
                        pattern = escaped
                else:
                    pattern = escaped

                for match in re.finditer(pattern, text):
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
