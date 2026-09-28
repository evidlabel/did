"""Qt-free pipeline logic for the gdid GUI.

Everything the GUI *does* (collect inputs, extract text, detect entities,
pseudonymize, save Typst) lives here as plain functions so it can be unit-tested
without PySide6 — and, by injecting ``anonymizer_factory``, without loading spaCy.
"""

import copy
import functools
import io
import re
import shutil
import tempfile
import zipfile
from hashlib import sha256
from pathlib import Path

from faker import Faker
from ruamel import yaml

from did.core import entity_types
from did.core.anonymizer import Anonymizer
from did.core.verification import TOKEN_RE as _TOKEN_RE
from did.core.verification import verify
from did.utils.file_utils import (
    SUPPORTED_SUFFIXES,
    Reading,
    compile_typst_pdf,
    export_to_typst,
    read_document,
)
from did.utils.typst_cleaning import escape_typst_specials

# Written-out labels per token prefix, for display outside Typst.
PLACEHOLDER_WORDS = entity_types.PLACEHOLDER_WORDS

_WRITTEN_TOKEN_RE = re.compile(
    r"\[("
    + "|".join(sorted(set(PLACEHOLDER_WORDS.values()), key=len, reverse=True))
    + r") (\d+)\]"
)
_PREFIX_ENTITY_TYPES = {
    entity.prefix: entity.config_key for entity in entity_types.ENTITY_TYPES
}
_WORD_PREFIXES = {word: prefix for prefix, word in PLACEHOLDER_WORDS.items()}


def normalize_entity_ids(data):
    """Renumber identity ids to their position within each type.

    Tokens are positional — ``#(P2V1)`` is the second PERSON in the list — so
    after any edit that removes, moves, or inserts an identity the stored ids
    are rewritten to match. The YAML then says exactly which token each
    identity produces.
    """
    if not isinstance(data, dict):
        return data
    for entity_type, entities in data.items():
        if not isinstance(entities, list):
            continue
        position = 0
        for entity in entities:
            if isinstance(entity, dict):
                position += 1
                entity["id"] = f"{entity_type}_{position}"
    return data


def token_entity(data, prefix, number):
    """Return the identity a ``#(<prefix><number>V…)`` token refers to, or None."""
    entity_type = _PREFIX_ENTITY_TYPES.get(prefix)
    entities = data.get(entity_type) if isinstance(data, dict) else None
    if not isinstance(entities, list):
        return None
    identities = [entity for entity in entities if isinstance(entity, dict)]
    index = int(number) - 1
    return identities[index] if 0 <= index < len(identities) else None


def change_entity_types(yaml_text, entity_ids, target_type):
    """Move identities to another supported YAML category and assign fresh IDs."""
    data = parse_yaml(yaml_text)
    selected = set(entity_ids)
    if not selected:
        return yaml_text
    target_type = str(target_type).upper()
    moved = []
    for entity_type, entities in data.items():
        if not isinstance(entities, list):
            continue
        retained = []
        for entity in entities:
            if isinstance(entity, dict) and str(entity.get("id", "")) in selected:
                moved.append(entity)
            else:
                retained.append(entity)
        data[entity_type] = retained
    if not moved:
        return yaml_text
    destination = data.setdefault(target_type, [])
    if not isinstance(destination, list):
        raise ValueError(f"{target_type} must be a list.")
    destination.extend(moved)
    return dump_yaml(normalize_entity_ids(data))


def add_entity_variant(yaml_text, entity_type, entity_id, variant):
    """Add a unique textual variant to one identity."""
    variant = str(variant).strip()
    if not variant:
        raise ValueError("Variant cannot be empty.")
    data = parse_yaml(yaml_text)
    entities = data.get(entity_type, [])
    if not isinstance(entities, list):
        raise ValueError(f"{entity_type} must be a list.")
    for entity in entities:
        if not isinstance(entity, dict) or str(entity.get("id", "")) != entity_id:
            continue
        variants = entity.setdefault("variants", [])
        if not isinstance(variants, list):
            raise ValueError(f"{entity_id} variants must be a list.")
        if variant.casefold() in {str(item).casefold() for item in variants}:
            # Already covered: capitals and other case forms match anyway.
            return yaml_text
        variants.append(variant)
        return dump_yaml(data)
    raise ValueError(f"Could not find identity {entity_id}.")


def add_entity_variant_at(yaml_text, entity_type, position, variant):
    """Add a variant to the *position*-th (1-based) identity of a type.

    Tokens are positional, so a review row knows its identity even when the
    saved keys file carries no ``id`` for it. Ids are rewritten afterwards so
    the stored keys keep matching the tokens they produce.
    """
    variant = str(variant).strip()
    if not variant:
        raise ValueError("Variant cannot be empty.")
    data = parse_yaml(yaml_text)
    entities = data.get(entity_type, [])
    if not isinstance(entities, list):
        raise ValueError(f"{entity_type} must be a list.")
    identities = [entity for entity in entities if isinstance(entity, dict)]
    index = int(position)
    if not 1 <= index <= len(identities):
        raise ValueError(f"Could not find identity {index} in {entity_type}.")
    entity = identities[index - 1]
    variants = entity.setdefault("variants", [])
    if not isinstance(variants, list):
        raise ValueError(f"{entity_type} variants must be a list.")
    if variant.casefold() in {str(item).casefold() for item in variants}:
        return yaml_text
    variants.append(variant)
    return dump_yaml(normalize_entity_ids(data))


def add_entity(yaml_text, variant, entity_type="PERSON"):
    """Create a new identity containing ``variant`` and return updated YAML.

    The identity is appended, so existing identities keep their tokens.
    """
    variant = str(variant).strip()
    if not variant:
        raise ValueError("Variant cannot be empty.")
    entity_type = str(entity_type).upper()
    data = parse_yaml(yaml_text) if yaml_text.strip() else {}
    entities = data.setdefault(entity_type, [])
    if not isinstance(entities, list):
        raise ValueError(f"{entity_type} must be a list.")
    entities.append({"id": "", "variants": [variant]})
    return dump_yaml(normalize_entity_ids(data))


def resolve_selection_placeholders(selected_text, yaml_text):
    """Restore known preview placeholders inside a manually selected phrase.

    A selection such as ``#(P1V1) Doe`` becomes ``John Doe`` before it is
    stored as an entity variant. Written-out placeholders use the identity's
    first variant because that display format intentionally omits variant IDs.
    Unknown placeholders are retained unchanged.
    """
    data = read_keys(yaml_text) if yaml_text.strip() else {}

    def resolve(prefix, number, variant_number, original):
        entity = token_entity(data, prefix, number)
        if entity is None:
            return original
        variants = entity.get("variants", [])
        if not isinstance(variants, list):
            return original
        index = max(0, int(variant_number or 1) - 1)
        return str(variants[index]) if index < len(variants) else original

    text = str(selected_text).replace("\u2029", "\n")
    text = _TOKEN_RE.sub(
        lambda match: resolve(
            match.group(1), match.group(2), match.group(3), match.group(0)
        ),
        text,
    )
    return _WRITTEN_TOKEN_RE.sub(
        lambda match: resolve(
            _WORD_PREFIXES[match.group(1)], match.group(2), 1, match.group(0)
        ),
        text,
    )


def to_written_out(text):
    """Rewrite ``#(P1V2)``-style tokens as ``[PERSON 1]``.

    The variant index is dropped: all variants of an identity render as the
    same label, which is what matters when reading the document.
    """
    return _TOKEN_RE.sub(
        lambda m: f"[{PLACEHOLDER_WORDS[m.group(1)]} {m.group(2)}]", text
    )


def to_redacted(text):
    """Replace every DID placeholder with one non-identifying marker."""
    return _TOKEN_RE.sub("[REDACTED]", text)


def to_synthetic(text, yaml_text, language="en", seed="did"):
    """Render known placeholders as stable, locale-aware synthetic values."""
    data = read_keys(yaml_text) if yaml_text.strip() else {}
    generated = {}

    def replace(match):
        prefix, number = match.group(1), match.group(2)
        key = (prefix, number)
        if token_entity(data, prefix, number) is None:
            return match.group(0)
        if key not in generated:
            fake = Faker({"da": "da_DK", "sv": "sv_SE"}.get(language, "en_US"))
            digest = sha256(f"{seed}:{prefix}:{number}".encode()).digest()
            fake.seed_instance(int.from_bytes(digest[:8], "big"))
            entity_type = _PREFIX_ENTITY_TYPES[prefix]
            factories = {
                "PERSON": fake.name,
                "ORGANIZATION": fake.company,
                "LOCATION": lambda: fake.address().replace("\n", ", "),
                "EMAIL_ADDRESS": fake.safe_email,
                "PHONE_NUMBER": fake.phone_number,
                "DATE_NUMBER": lambda: fake.date_object().isoformat(),
                "ID_NUMBER": fake.ssn,
                "CODE_NUMBER": lambda: fake.bothify("??-####").upper(),
                "GENERAL_NUMBER": lambda: str(fake.random_number(digits=6)),
                "URL": fake.url,
            }
            generated[key] = factories[entity_type]()
        return generated[key]

    return _TOKEN_RE.sub(replace, text)


def collect_inputs(paths, *, allow_unknown=False):
    """Expand file/dir/zip paths into ``(files, temp_dirs)``.

    ``.zip`` archives are extracted to a temp dir (returned so the caller can clean
    up), directories are scanned one level deep, and plain files are kept when their
    suffix is supported. With ``allow_unknown`` an explicitly chosen file is kept
    whatever its extension: the reader decides whether it holds text.
    """
    files = []
    temp_dirs = []
    for raw in paths:
        p = Path(raw)
        if p.suffix.lower() == ".zip":
            temp_dir = Path(tempfile.mkdtemp())
            temp_dirs.append(temp_dir)
            with zipfile.ZipFile(p, "r") as zf:
                zf.extractall(temp_dir)
            files.extend(
                sorted(
                    q
                    for q in temp_dir.rglob("*")
                    if q.suffix.lower() in SUPPORTED_SUFFIXES
                )
            )
        elif p.is_dir():
            files.extend(
                sorted(q for q in p.iterdir() if q.suffix.lower() in SUPPORTED_SUFFIXES)
            )
        elif p.suffix.lower() in SUPPORTED_SUFFIXES or (allow_unknown and p.is_file()):
            files.append(p)
    return files, temp_dirs


def extract_one(path) -> Reading:
    """Read one document. Downstream steps use this reading and do not open the file again."""
    return read_document(Path(path))


def detect_to_yaml(
    texts, language, *, detection_profile=None, anonymizer_factory=Anonymizer
):
    """Detect entities across ``texts`` and return ``(anonymizer, yaml_str)``.

    ``anonymizer_factory`` is injectable so tests can supply a fast fake instead of
    the real spaCy-backed :class:`Anonymizer`.
    """
    kwargs = {"language": language}
    if detection_profile is not None:
        kwargs["detection_profile"] = detection_profile
    anonymizer = anonymizer_factory(**kwargs)
    anonymizer.detect_entities(list(texts))
    return anonymizer, anonymizer.generate_yaml()


def _load_keys(yaml_text):
    # The C-backed safe loader is ~15x faster than round-trip; the keys file
    # carries no comments or anchors worth preserving.
    try:
        data = yaml.YAML(typ="safe").load(yaml_text)
    except Exception as e:
        raise ValueError(f"Invalid YAML: {e}") from e
    if data is None:
        raise ValueError("YAML is empty or invalid.")
    return data


@functools.lru_cache(maxsize=16)
def _cached_keys(yaml_text):
    return _load_keys(yaml_text)


def read_keys(yaml_text):
    """Parse keys YAML for reading; cached, so the result must not be mutated.

    The GUI reads the same text from several places per change (table, keys
    bar, synthetic preview, context menus); each parses it once.
    """
    return _cached_keys(str(yaml_text))


def parse_yaml(yaml_text):
    """Parse YAML config text into a dict; raise ``ValueError`` if bad or empty.

    Returns a private copy that the caller may modify.
    """
    return copy.deepcopy(read_keys(yaml_text))


def dump_yaml(data):
    """Serialize entity configuration data as YAML (block style, keys in order)."""
    dumper = yaml.YAML(typ="safe")
    dumper.default_flow_style = False
    dumper.allow_unicode = True
    dumper.representer.sort_base_mapping_type_on_output = False
    stream = io.StringIO()
    dumper.dump(data, stream)
    return stream.getvalue()


_READING_KEY = "_reading"


def readings_digest(readings) -> str:
    """Hash of the readings a review belongs to, in path order."""
    digest = sha256()
    items = readings.items() if isinstance(readings, dict) else readings
    for path, reading in sorted(items, key=lambda item: str(item[0])):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(reading.content_hash.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def stamp_reading(yaml_text: str, digest: str) -> str:
    """Record which document reading an entity review was built from."""
    if str(yaml_text or "").strip():
        try:
            data = parse_yaml(yaml_text)
        except ValueError:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[_READING_KEY] = digest
    return dump_yaml(data)


def drop_fragment_organizations(yaml_text: str) -> str:
    """Remove organization variants that are letters or short words, not names."""
    from did.core.detection import keep_named_entity

    if not str(yaml_text or "").strip():
        return yaml_text
    try:
        data = parse_yaml(yaml_text)
    except ValueError:
        return yaml_text
    if not isinstance(data, dict):
        return yaml_text
    organizations = data.get("ORGANIZATION")
    if not isinstance(organizations, list):
        return yaml_text
    kept = []
    for entity in organizations:
        if not isinstance(entity, dict):
            continue
        variants = [
            variant
            for variant in entity.get("variants") or []
            if keep_named_entity("organization", str(variant))
        ]
        if variants:
            entity["variants"] = variants
            kept.append(entity)
    data["ORGANIZATION"] = kept
    return dump_yaml(data)


def review_matches_readings(yaml_text, readings) -> bool:
    """A saved review applies only to the reading whose hash it stores."""
    if not readings or not str(yaml_text or "").strip():
        return False
    try:
        data = read_keys(yaml_text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    stamped = data.get(_READING_KEY)
    return bool(stamped) and str(stamped) == readings_digest(readings)


def exclude_not_names(yaml_text, names):
    """Remove PERSON identities matching project-level name exclusions."""
    exclusions = {str(name).strip().casefold() for name in names if str(name).strip()}
    if not exclusions:
        return yaml_text
    data = parse_yaml(yaml_text)
    people = data.get("PERSON", [])
    if not isinstance(people, list):
        return yaml_text
    data["PERSON"] = [
        entity
        for entity in people
        if not (
            isinstance(entity, dict)
            and any(
                str(variant).strip().casefold() in exclusions
                for variant in entity.get("variants", [])
            )
        )
    ]
    return dump_yaml(normalize_entity_ids(data))


def merge_person_entities(yaml_text, source_id, target_id, variant=None):
    """Merge a PERSON identity or one of its variants into another identity."""
    if source_id == target_id:
        return yaml_text
    data = parse_yaml(yaml_text)
    people = data.get("PERSON", [])
    if not isinstance(people, list):
        raise ValueError("PERSON must be a list.")
    source = next(
        (entity for entity in people if str(entity.get("id")) == source_id), None
    )
    target = next(
        (entity for entity in people if str(entity.get("id")) == target_id), None
    )
    if source is None or target is None:
        raise ValueError("Could not find both PERSON identities to merge.")
    source_variants = list(source.get("variants", []))
    moving = (
        [item for item in source_variants if str(item) == variant]
        if variant is not None
        else source_variants
    )
    if not moving:
        return yaml_text
    target_variants = list(target.get("variants", []))
    seen = {str(item).casefold() for item in target_variants}
    for item in moving:
        if str(item).casefold() not in seen:
            target_variants.append(item)
            seen.add(str(item).casefold())
    target["variants"] = target_variants
    if variant is None:
        people.remove(source)
    else:
        source["variants"] = [item for item in source_variants if str(item) != variant]
        if not source["variants"]:
            people.remove(source)
    return dump_yaml(normalize_entity_ids(data))


def merge_review(reviewed_yaml, detected_yaml):
    """Combine a reviewed config with a fresh detection without losing review work.

    Every reviewed identity is kept as it is and in order, so review edits
    (merges, type changes, added variants, manual identities) survive
    re-detection. A detected identity is appended only when none of its
    variants is already covered by any reviewed identity of any type. The new
    detection's reading stamp is carried over.

    Returns ``(yaml_text, kept, added)``.
    """
    reviewed = parse_yaml(reviewed_yaml)
    detected = parse_yaml(detected_yaml) if str(detected_yaml).strip() else {}
    if not isinstance(reviewed, dict):
        raise ValueError("Reviewed configuration must be a mapping.")
    covered = set()
    kept = 0
    for entities in reviewed.values():
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if isinstance(entity, dict):
                kept += 1
                covered.update(
                    str(variant).strip().casefold()
                    for variant in entity.get("variants") or []
                )
    added = 0
    for entity_type, entities in (detected or {}).items():
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            variants = [str(variant) for variant in entity.get("variants") or []]
            if not variants or any(
                variant.strip().casefold() in covered for variant in variants
            ):
                continue
            destination = reviewed.setdefault(entity_type, [])
            if not isinstance(destination, list):
                raise ValueError(f"{entity_type} must be a list.")
            destination.append(entity)
            covered.update(variant.strip().casefold() for variant in variants)
            added += 1
    if isinstance(detected, dict) and detected.get(_READING_KEY):
        reviewed[_READING_KEY] = detected[_READING_KEY]
    return dump_yaml(normalize_entity_ids(reviewed)), kept, added


_IDENTITY_COUNTS: dict[str, int] = {}


def count_identities(yaml_text) -> int:
    """Number of identities in a config, or 0 when it is empty or invalid.

    Uses the fast safe loader and remembers results, because the project tree
    counts every project's and version's keys on each rebuild.
    """
    text = str(yaml_text or "")
    if not text.strip():
        return 0
    key = sha256(text.encode("utf-8")).hexdigest()
    if key not in _IDENTITY_COUNTS:
        _IDENTITY_COUNTS[key] = _count_identities(text)
    return _IDENTITY_COUNTS[key]


def _count_identities(yaml_text) -> int:
    try:
        data = read_keys(yaml_text)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    return sum(
        1
        for entities in data.values()
        if isinstance(entities, list)
        for entity in entities
        if isinstance(entity, dict)
    )


def pseudonymize_all(anonymizer, yaml_text, texts):
    """Load replacements from ``yaml_text`` and anonymize each text.

    ``texts`` maps an arbitrary key (e.g. a Path) to its raw text; the return value
    maps the same keys to anonymized text. The caller's ``anonymizer`` is not
    modified: replacements are loaded into a copy, so a worker thread never
    changes the instance the window later exports with.
    """
    anonymizer = loaded_anonymizer(anonymizer, yaml_text)
    return {key: anonymizer.anonymize(text)[0] for key, text in texts.items()}


def loaded_anonymizer(anonymizer, yaml_text):
    """Return a copy of ``anonymizer`` with replacements loaded from ``yaml_text``."""
    config_data = read_keys(yaml_text)
    loaded = copy.copy(anonymizer)
    loaded.load_replacements(config_data)
    return loaded


def verify_outputs(yaml_text, anonymized, not_names=(), *, anonymizer=None):
    """Re-scan pseudonymized output for identifiers that survived replacement.

    ``anonymized`` is what :func:`pseudonymize_all` returned. Passing
    ``anonymizer`` adds the full re-detection sweep, which costs a spaCy pass —
    worth it once per detection run, too slow for every config edit.
    """
    config_data = read_keys(yaml_text) if yaml_text.strip() else {}
    return verify(
        anonymized, config_data, not_names=list(not_names), anonymizer=anonymizer
    )


def save_outputs(files, anonymizer, yaml_text, mode, out_dir, source_texts=None):
    """Write pseudonymized output. ``mode`` is ``"multi"``, ``"single"``, or ``"pdf"``.

    Returns the subdirectory that was written to.
    """
    out_path = Path(out_dir)
    if mode == "pdf":
        # Compile each document to <stem>_pseudo.pdf. Typst's sources (which
        # import the real-value vars file) live in a temp dir, so only the PDFs
        # — rendered with fake values — reach the output folder.
        sub_dir = out_path / "pseudonymized_pdf"
        sub_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            build = Path(td)
            shared_vars = str(build / "shared_vars.typ")
            shared_fakevars = str(build / "shared_fakevars.typ")
            for f in files:
                main_typ = build / f"{f.stem}_pseudonymized.typ"
                export_to_typst(
                    f,
                    anonymizer,
                    main_typ,
                    vars_filename=shared_vars,
                    fakevars_filename=shared_fakevars,
                    source_text=None if source_texts is None else source_texts.get(f),
                )
                compile_typst_pdf(main_typ, sub_dir / f"{f.stem}_pseudo.pdf")
        (sub_dir / "config.yaml").write_text(yaml_text, encoding="utf-8")
        return sub_dir

    if mode == "multi":
        sub_dir = out_path / "pseudonymized"
        sub_dir.mkdir(parents=True, exist_ok=True)
        shared_vars = str(sub_dir / "shared_vars.typ")
        shared_fakevars = str(sub_dir / "shared_fakevars.typ")
        for f in files:
            export_to_typst(
                f,
                anonymizer,
                sub_dir / f"{f.stem}_pseudonymized.typ",
                vars_filename=shared_vars,
                fakevars_filename=shared_fakevars,
                source_text=None if source_texts is None else source_texts.get(f),
            )
        (sub_dir / "config.yaml").write_text(yaml_text, encoding="utf-8")
        return sub_dir

    if mode == "single":
        sub_dir = out_path / "single_pseudonymized"
        sub_dir.mkdir(parents=True, exist_ok=True)
        shared_vars = str(sub_dir / "shared_vars.typ")
        shared_fakevars = str(sub_dir / "shared_fakevars.typ")
        combined = sub_dir / "combined.typ"
        with (
            tempfile.TemporaryDirectory() as td,
            open(combined, "w", encoding="utf-8") as out_f,
        ):
            out_f.write('#import "shared_vars.typ": *\n#outline()\n\n')
            for f in files:
                tmp = Path(td) / f"{f.stem}.typ"
                # write_imports=False → token-only body, no #import lines to strip.
                export_to_typst(
                    f,
                    anonymizer,
                    tmp,
                    vars_filename=shared_vars,
                    fakevars_filename=shared_fakevars,
                    write_imports=False,
                    source_text=None if source_texts is None else source_texts.get(f),
                )
                body = tmp.read_text(encoding="utf-8").strip()
                out_f.write(f"= {escape_typst_specials(f.name)}\n\n{body}\n\n")
        (sub_dir / "config.yaml").write_text(yaml_text, encoding="utf-8")
        return sub_dir

    raise ValueError(f"Unknown save mode: {mode!r}")


def save_version_outputs(
    files, anonymizer, yaml_text, mode, version_stage, source_texts=None
):
    """Write canonical outputs into a version stage's ``output`` directory.

    Tokens come from ``yaml_text`` itself, never from whatever replacements
    ``anonymizer`` last held, so the output always matches the version's
    ``entities.yaml``. That file is the version's only keys file; the
    ``config.yaml`` copy that :func:`save_outputs` writes is dropped.
    """
    stage = Path(version_stage)
    anonymizer = loaded_anonymizer(anonymizer, yaml_text)
    generated = save_outputs(
        files, anonymizer, yaml_text, mode, stage, source_texts=source_texts
    )
    (generated / "config.yaml").unlink(missing_ok=True)
    output = stage / "output"
    if output.exists():
        output.rmdir()
    shutil.move(str(generated), str(output))
    return output
