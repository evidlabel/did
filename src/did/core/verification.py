"""Output verification — re-scan pseudonymized text for surviving identifiers.

Detection runs on *source* text. This module runs on the *output*, answering the
separate question "did anything identifying survive replacement?" before the
document is copied, zipped, or frozen into a version.

Three tiers, cheapest first:

``A`` surviving variants
    A variant from the confirmed config still present in the output. A definite
    leak. Tiers A's patterns are deliberately **looser** than the ones
    :func:`did.core.replacement.anonymize` replaces with — case-insensitive, no
    word boundaries, whitespace-flexible — because a verifier reusing the
    replacer's own pattern would find nothing by construction. The asymmetry is
    what surfaces boundary, possessive, and line-break leaks.

``B`` suspected entities
    Deterministic re-sweep via :func:`did.core.helpers.fallback_scan` for
    identifiers that were never in the config at all.

``C`` re-detection
    Full Presidio/spaCy sweep, available only where an ``Anonymizer`` is already
    loaded.

Tiers A and B need no language model, so they can run on every config edit.

Findings warn; they never block. DID detects aggressively by design, so false
positives are expected and the reviewer stays the decision-maker.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import entity_types
from .config import FIELD_MAPPING, to_plain
from .detection import preprocess_text
from .helpers import fallback_scan

# Canonical placeholder pattern. Longest prefixes first so "PH" matches before "P".
TOKEN_RE = entity_types.token_re()

# Neutral filler for masked tokens. Alphabetic and lowercase so it is not itself
# picked up as a name, number, or code by the tier B/C scanners.
TOKEN_MASK = "xx"

#: Findings of this kind are definite leaks.
KIND_SURVIVING_VARIANT = "surviving_variant"
#: Findings of this kind are candidates the detector never captured.
KIND_SUSPECTED_ENTITY = "suspected_entity"
#: Findings of this kind were deliberately retained by the reviewer.
KIND_RETAINED_EXCLUSION = "retained_exclusion"

_CONTEXT_RADIUS = 40


@dataclass(frozen=True)
class Finding:
    """One identifier that survived into the pseudonymized output."""

    kind: str
    text: str
    document: str
    line: int
    context: str
    entity_id: str | None = None
    entity_type: str | None = None

    def to_dict(self) -> dict:
        """Serialize for JSON reports. Includes the offending text."""
        return {
            "kind": self.kind,
            "text": self.text,
            "document": self.document,
            "line": self.line,
            "context": self.context,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
        }

    def to_safe_dict(self) -> dict:
        """Serialize without the offending text, for agent-safe outputs."""
        return {
            "kind": self.kind,
            "document": self.document,
            "line": self.line,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
        }


@dataclass
class VerificationReport:
    """Findings from one verification pass over a document set."""

    leaks: list[Finding] = field(default_factory=list)
    suspected: list[Finding] = field(default_factory=list)
    retained: list[Finding] = field(default_factory=list)
    deep: bool = False
    #: Why the re-detection sweep was skipped, when one was requested.
    deep_error: str | None = None

    @property
    def is_clean(self) -> bool:
        """True when nothing needs the reviewer's attention.

        Retained exclusions are the reviewer's own recorded decision, so they do
        not make a report unclean.
        """
        return not self.leaks and not self.suspected

    def summary(self) -> str:
        """One-line human summary."""
        if self.is_clean:
            return "Output verification: clean."
        parts = []
        if self.leaks:
            parts.append(f"{len(self.leaks)} surviving identifier(s)")
        if self.suspected:
            parts.append(f"{len(self.suspected)} suspected identifier(s)")
        return "Output verification: " + ", ".join(parts) + "."

    def counts(self) -> dict:
        """Finding counts only — safe to place beside token-only output."""
        counts = {
            "leaks": len(self.leaks),
            "suspected": len(self.suspected),
            "retained": len(self.retained),
            "deep": self.deep,
            "clean": self.is_clean,
        }
        if self.deep_error:
            counts["deep_error"] = self.deep_error
        return counts

    def to_dict(self) -> dict:
        """Full report, including the offending text. Treat as sensitive."""
        return {
            "schema_version": 1,
            **self.counts(),
            "findings": [
                finding.to_dict()
                for finding in (*self.leaks, *self.suspected, *self.retained)
            ],
        }

    def to_safe_dict(self) -> dict:
        """Report with locations but no offending text. Agent-safe."""
        return {
            "schema_version": 1,
            **self.counts(),
            "findings": [
                finding.to_safe_dict()
                for finding in (*self.leaks, *self.suspected, *self.retained)
            ],
        }


def mask_tokens(text: str) -> str:
    """Replace every ``#(P1V1)`` placeholder with neutral filler.

    Required before tiers B and C: left in place, a token is itself detected as a
    code or general number and every document reports leaks it does not have.
    """
    return TOKEN_RE.sub(TOKEN_MASK, text)


def _fold_char(ch: str) -> str:
    """Lowercase one character without ever changing its length.

    ``str.lower`` is 1:1 for realistic text but not universally — U+0130 lowers
    to two code points. Offsets from :func:`preprocess_text` are only valid
    against an equal-length string, so anything that would grow is left as-is.
    """
    lowered = ch.lower()
    return lowered if len(lowered) == 1 else ch


def _fold(text: str) -> str:
    """Case-fold for matching, preserving length so offsets stay valid.

    Accents are deliberately *not* stripped. Tier A hunts variants taken from
    the source text, which carry the same diacritics as the text they came from,
    so accent folding would buy nothing and cost precision.
    """
    return "".join(_fold_char(ch) for ch in text)


def _searchable(text: str):
    """Return ``(folded_text, map_to_original)`` for loose matching.

    Rejoins words hyphenated across a line break, reusing the same helper
    detection uses, so a name broken as ``John Do-\\ne`` is still found, then
    folds case. Folding is per-character, so ``map_to_original`` stays valid.
    """
    searchable, map_to_original = preprocess_text(text)
    return _fold(searchable), map_to_original


def variant_probe(variant: str) -> re.Pattern | None:
    """Build the loose search pattern used to hunt a surviving variant.

    Deliberately weaker than the replacement pattern in
    :mod:`did.core.replacement`, which anchors *both* ends with ``\\b`` and
    matches case-sensitively. This anchors the **left** edge only, and folds
    case.

    The asymmetry is deliberate and is the whole design. Every way a name leaks
    — a Danish compound (``Doesagen``), a hyphenated compound (``Doe-sagen``), a
    possessive, a line-break rejoin — *appends* to the name, so dropping the
    right anchor catches all of them. Dropping the left anchor as well would
    additionally match names buried inside unrelated words (``Roe`` inside
    ``vedrører``), which is pure noise: keeping it costs no recall.

    Returns ``None`` for needles too short to search without flooding the
    report.
    """
    tokens = variant.split()
    if not tokens:
        return None
    folded = _fold(variant)
    if len(folded.replace(" ", "")) < 3:
        return None
    pattern = r"\s+".join(re.escape(_fold(token)) for token in tokens)
    return re.compile(r"\b" + pattern)


#: Entity types whose variants are worth breaking into individual name tokens.
#: Numbers and codes are not: their parts carry no identity on their own.
_TOKENISED_TYPES = ("PERSON", "LOCATION", "ORGANIZATION")

#: Name particles that identify nobody by themselves.
_PARTICLES = frozenset(
    "von van de del der den det af og the of da di du la le el bin ibn".split()
)


def _name_tokens(variant: str):
    """Yield the individually identifying tokens of a multi-token name.

    Detection typically produces whole names — ``John Doe``, ``JOHN DOE`` — and
    never the bare surname. But the surname alone is what leaks: Danish glues it
    into compounds like ``Doesagen``, which the replacer's ``\\b``-anchored
    pattern walks straight past. Probing tokens as well as whole variants is
    what turns that from a silent miss into a reported leak.
    """
    tokens = variant.split()
    if len(tokens) < 2:
        return
    for token in tokens:
        cleaned = token.strip(".,;:!?()[]\"'")
        if len(cleaned) < 3 or _fold(cleaned) in _PARTICLES:
            continue
        if not any(ch.isalpha() for ch in cleaned):
            continue
        yield cleaned


def _iter_config_entities(config_data, *, tokenise=False):
    """Yield ``(entity_type, entity_id, needle)`` for every configured variant.

    With ``tokenise``, also yields the individual name tokens of multi-token
    variants for the types in :data:`_TOKENISED_TYPES`.
    """
    data = to_plain(config_data) or {}
    for alias in FIELD_MAPPING.values():
        entities = data.get(alias)
        if not isinstance(entities, list):
            continue
        for index, entity in enumerate(entities, 1):
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or f"{alias}_{index}")
            variants = entity.get("variants")
            if not isinstance(variants, list):
                continue
            if tokenise and alias in _TOKENISED_TYPES:
                seen = set()
                for variant in variants:
                    for token in _name_tokens(str(variant).strip()):
                        if _fold(token) not in seen:
                            seen.add(_fold(token))
                            yield alias, entity_id, token
            for variant in variants:
                variant = str(variant).strip()
                if variant:
                    yield alias, entity_id, variant


def _finding_at(text, start, end, *, kind, document, entity_id=None, entity_type=None):
    """Build a :class:`Finding` for a span of the original text."""
    left = max(0, start - _CONTEXT_RADIUS)
    right = min(len(text), end + _CONTEXT_RADIUS)
    context = " ".join(text[left:right].split())
    return Finding(
        kind=kind,
        text=text[start:end],
        document=str(document),
        line=text.count("\n", 0, start) + 1,
        context=context,
        entity_id=entity_id,
        entity_type=entity_type,
    )


def _resolve_overlaps(spans):
    """Keep non-overlapping spans, preferring longer ones.

    ``John Doe`` and its short variant ``Doe`` both match the same text; without
    this the reviewer sees the same leak twice. Longest-wins matches the overlap
    policy in :mod:`did.core.replacement` and :mod:`did.core.detection`.
    """
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept = []
    last_end = 0
    for span in ordered:
        if span[0] >= last_end:
            kept.append(span)
            last_end = span[1]
    return kept


def _scan_needles(text, needles, *, kind, document):
    """Find every ``(entity_type, entity_id, needle)`` still present in ``text``."""
    folded, map_to_original = _searchable(text)
    spans = []
    for entity_type, entity_id, needle in needles:
        probe = variant_probe(needle)
        if probe is None:
            continue
        for match in probe.finditer(folded):
            start, end = map_to_original(match.start(), match.end())
            spans.append((start, end, entity_id, entity_type))
    return sorted(
        (
            _finding_at(
                text,
                start,
                end,
                kind=kind,
                document=document,
                entity_id=entity_id,
                entity_type=entity_type,
            )
            for start, end, entity_id, entity_type in _resolve_overlaps(spans)
        ),
        key=lambda f: (f.line, f.text),
    )


def find_surviving_variants(text: str, config_data, *, document="") -> list[Finding]:
    """Tier A — confirmed variants that are still present verbatim in ``text``.

    Every hit is a definite leak: the value was known to be identifying and the
    replacement pass failed to remove it.
    """
    return _scan_needles(
        text,
        _iter_config_entities(config_data, tokenise=True),
        kind=KIND_SURVIVING_VARIANT,
        document=document,
    )


def find_retained_exclusions(text: str, not_names, *, document="") -> list[Finding]:
    """Names the reviewer marked "do not pseudonymize" that appear in the output.

    Reported apart from leaks: their presence is a recorded decision, not a
    failure. Surfacing them keeps that decision visible at export time.
    """
    return _scan_needles(
        text,
        (("PERSON", None, str(name).strip()) for name in not_names),
        kind=KIND_RETAINED_EXCLUSION,
        document=document,
    )


def _locate(text, haystack, needle, *, kind, document, entity_type, known_folded):
    """Yield findings for each occurrence of ``needle`` not already accounted for.

    ``haystack`` is the ``(folded_text, map_to_original)`` pair from
    :func:`_searchable`, passed in so a sweep over many needles preprocesses the
    document once rather than once per needle.
    """
    if _fold(needle) in known_folded:
        return
    probe = variant_probe(needle)
    if probe is None:
        return
    folded, map_to_original = haystack
    for match in probe.finditer(folded):
        start, end = map_to_original(match.start(), match.end())
        yield _finding_at(
            text,
            start,
            end,
            kind=kind,
            document=document,
            entity_type=entity_type,
        )


def find_suspected_entities(text: str, *, known=(), document="") -> list[Finding]:
    """Tier B — deterministic re-sweep for identifiers absent from the config.

    Reuses :func:`did.core.helpers.fallback_scan`, so it needs no language model
    and finds the same classes that scan covers: multi-token capitalized names,
    IBANs, dates, spaced phone numbers, and URLs.
    """
    masked = mask_tokens(text)
    buckets = defaultdict(list)
    fallback_scan(masked, buckets, {})

    haystack = _searchable(text)
    known_folded = {_fold(str(item)) for item in known}
    findings = []
    seen = set()
    for category, values in buckets.items():
        entity_type = category.upper()
        for value in values:
            for finding in _locate(
                text,
                haystack,
                str(value),
                kind=KIND_SUSPECTED_ENTITY,
                document=document,
                entity_type=entity_type,
                known_folded=known_folded,
            ):
                key = (finding.line, finding.text)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
    return sorted(findings, key=lambda f: (f.line, f.text))


def supports_deep_rescan(anonymizer) -> bool:
    """Whether ``anonymizer`` exposes enough surface to re-run detection.

    Lets :func:`verify` report ``deep`` honestly. Claiming a full sweep ran when
    it did not would put a false assurance into the audit manifest.
    """
    return anonymizer is not None and all(
        hasattr(anonymizer, name) for name in ("entities", "counts", "detect_entities")
    )


def rescan_with_anonymizer(
    text: str, anonymizer, *, known=(), document=""
) -> list[Finding]:
    """Tier C — re-run full detection over the masked output.

    Only worth calling where an ``Anonymizer`` is already loaded; it costs a
    complete Presidio/spaCy pass. Detected values already present in ``known``
    are skipped, since tier A reports those with far better precision.

    Returns an empty list for objects that do not expose a full ``Anonymizer``
    surface, so a caller holding a lighter stand-in gets tiers A and B rather
    than an error.
    """
    if not supports_deep_rescan(anonymizer):
        return []

    # Shallow-copy the loaded anonymizer so the expensive analyzer and language
    # model are shared, but detection writes into throwaway state instead of
    # clobbering the entity config the caller is still using to pseudonymize.
    cls = type(anonymizer)
    probe = cls.__new__(cls)
    probe.__dict__.update(anonymizer.__dict__)
    probe.entities = type(anonymizer.entities)()
    probe.counts = dict.fromkeys(anonymizer.counts, 0)
    probe.detect_entities([mask_tokens(text)])

    haystack = _searchable(text)
    known_folded = {_fold(str(item)) for item in known}
    findings = []
    seen = set()
    for alias in FIELD_MAPPING:
        for entity in getattr(probe.entities, alias, []):
            for variant in entity.variants:
                for finding in _locate(
                    text,
                    haystack,
                    str(variant),
                    kind=KIND_SUSPECTED_ENTITY,
                    document=document,
                    entity_type=alias.upper(),
                    known_folded=known_folded,
                ):
                    key = (finding.line, finding.text)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(finding)
    return sorted(findings, key=lambda f: (f.line, f.text))


def verify(
    outputs, config_data, *, not_names=(), anonymizer=None
) -> VerificationReport:
    """Verify pseudonymized ``outputs`` against the config that produced them.

    ``outputs`` maps a document key (a ``Path``, a name) to its pseudonymized
    text, matching the shape returned by ``gdid.pipeline.pseudonymize_all``.
    Passing ``anonymizer`` enables the tier C re-detection sweep.
    """
    report = VerificationReport(deep=supports_deep_rescan(anonymizer))
    if anonymizer is not None and not report.deep:
        report.deep_error = (
            f"{type(anonymizer).__name__} does not support re-detection; "
            "ran the model-free tiers only"
        )
    known = [variant for _, _, variant in _iter_config_entities(config_data)]
    known += [str(name) for name in not_names]

    for key, text in outputs.items():
        document = getattr(key, "name", None) or str(key)
        report.leaks.extend(
            find_surviving_variants(text, config_data, document=document)
        )
        report.retained.extend(
            find_retained_exclusions(text, not_names, document=document)
        )
        suspected = find_suspected_entities(text, known=known, document=document)
        if anonymizer is not None:
            # Verification is a check on the output, not part of producing it.
            # A failure here must degrade to the model-free tiers, never take
            # down the pseudonymization run it was called to inspect.
            try:
                suspected += rescan_with_anonymizer(
                    text, anonymizer, known=known, document=document
                )
            except Exception as exc:
                report.deep = False
                report.deep_error = str(exc)
        # Tier A already reports these spans with an entity ID; do not double-count.
        leaked = {(f.line, _fold(f.text)) for f in report.leaks}
        retained = {(f.line, _fold(f.text)) for f in report.retained}
        seen = set()
        for finding in suspected:
            key_ = (finding.line, _fold(finding.text))
            if key_ in leaked or key_ in retained or key_ in seen:
                continue
            seen.add(key_)
            report.suspected.append(finding)
    return report
