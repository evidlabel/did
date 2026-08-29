"""Qt-free pipeline logic for the gdid GUI.

Everything the GUI *does* (collect inputs, extract text, detect entities,
pseudonymize, save Typst) lives here as plain functions so it can be unit-tested
without PySide6 — and, by injecting ``anonymizer_factory``, without loading spaCy.
"""

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
from did.utils.file_utils import export_to_typst, extract_text

SUPPORTED_SUFFIXES = (".pdf", ".docx", ".md", ".txt")

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
    used = {
        str(entity.get("id", "")) for entity in destination if isinstance(entity, dict)
    }
    next_number = 1
    for entity in moved:
        while f"{target_type}_{next_number}" in used:
            next_number += 1
        entity["id"] = f"{target_type}_{next_number}"
        used.add(entity["id"])
        destination.append(entity)
        next_number += 1
    return dump_yaml(data)


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
        if variant.casefold() not in {str(item).casefold() for item in variants}:
            variants.append(variant)
        return dump_yaml(data)
    raise ValueError(f"Could not find identity {entity_id}.")


def add_entity(yaml_text, variant, entity_type="PERSON"):
    """Create a new identity containing ``variant`` and return updated YAML.

    IDs are allocated within the selected type without renumbering existing
    identities.  This makes manual review additive and predictable.
    """
    variant = str(variant).strip()
    if not variant:
        raise ValueError("Variant cannot be empty.")
    entity_type = str(entity_type).upper()
    data = parse_yaml(yaml_text) if yaml_text.strip() else {}
    entities = data.setdefault(entity_type, [])
    if not isinstance(entities, list):
        raise ValueError(f"{entity_type} must be a list.")
    used = {
        str(entity.get("id", "")) for entity in entities if isinstance(entity, dict)
    }
    next_number = 1
    while f"{entity_type}_{next_number}" in used:
        next_number += 1
    entities.append({"id": f"{entity_type}_{next_number}", "variants": [variant]})
    return dump_yaml(data)


def resolve_selection_placeholders(selected_text, yaml_text):
    """Restore known preview placeholders inside a manually selected phrase.

    A selection such as ``#(P1V1) Doe`` becomes ``John Doe`` before it is
    stored as an entity variant. Written-out placeholders use the identity's
    first variant because that display format intentionally omits variant IDs.
    Unknown placeholders are retained unchanged.
    """
    data = parse_yaml(yaml_text) if yaml_text.strip() else {}

    def resolve(prefix, number, variant_number, original):
        entity_type = _PREFIX_ENTITY_TYPES[prefix]
        entities = data.get(entity_type, [])
        if not isinstance(entities, list):
            return original
        possible_ids = {f"{prefix}{number}", f"{entity_type}_{number}"}
        for entity in entities:
            if (
                not isinstance(entity, dict)
                or str(entity.get("id")) not in possible_ids
            ):
                continue
            variants = entity.get("variants", [])
            if not isinstance(variants, list):
                return original
            index = max(0, int(variant_number or 1) - 1)
            return str(variants[index]) if index < len(variants) else original
        return original

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
    data = parse_yaml(yaml_text) if yaml_text.strip() else {}
    known = set()
    for entity_type, entities in data.items():
        if not isinstance(entities, list):
            continue
        prefix = next(
            (
                key
                for key, value in _PREFIX_ENTITY_TYPES.items()
                if value == entity_type
            ),
            None,
        )
        if prefix is None:
            continue
        for index, entity in enumerate(entities, 1):
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id", ""))
            number = entity_id.removeprefix(f"{entity_type}_")
            if not number.isdigit():
                number = str(index)
            known.add((prefix, number))

    generated = {}

    def replace(match):
        prefix, number = match.group(1), match.group(2)
        key = (prefix, number)
        if key not in known:
            return match.group(0)
        if key not in generated:
            fake = Faker("da_DK" if language == "da" else "en_US")
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


def collect_inputs(paths):
    """Expand file/dir/zip paths into ``(files, temp_dirs)``.

    ``.zip`` archives are extracted to a temp dir (returned so the caller can clean
    up), directories are scanned one level deep, and plain files are kept when their
    suffix is supported.
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
        elif p.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(p)
    return files, temp_dirs


def extract_one(path):
    """Extract normalized plain text from one supported document."""
    return extract_text(Path(path))


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


def parse_yaml(yaml_text):
    """Parse YAML config text into a dict; raise ``ValueError`` if bad or empty."""
    try:
        data = yaml.YAML().load(io.StringIO(yaml_text))
    except Exception as e:
        raise ValueError(f"Invalid YAML: {e}") from e
    if data is None:
        raise ValueError("YAML is empty or invalid.")
    return data


def dump_yaml(data):
    """Serialize entity configuration data as YAML."""
    stream = io.StringIO()
    yaml.YAML().dump(data, stream)
    return stream.getvalue()


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
    stream = io.StringIO()
    yaml.YAML().dump(data, stream)
    return stream.getvalue()


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
    stream = io.StringIO()
    yaml.YAML().dump(data, stream)
    return stream.getvalue()


def pseudonymize_all(anonymizer, yaml_text, texts):
    """Load replacements from ``yaml_text`` and anonymize each text.

    ``texts`` maps an arbitrary key (e.g. a Path) to its raw text; the return value
    maps the same keys to anonymized text.
    """
    config_data = parse_yaml(yaml_text)
    anonymizer.load_replacements(config_data)
    return {key: anonymizer.anonymize(text)[0] for key, text in texts.items()}


def verify_outputs(yaml_text, anonymized, not_names=(), *, anonymizer=None):
    """Re-scan pseudonymized output for identifiers that survived replacement.

    ``anonymized`` is what :func:`pseudonymize_all` returned. Passing
    ``anonymizer`` adds the full re-detection sweep, which costs a spaCy pass —
    worth it once per detection run, too slow for every config edit.
    """
    config_data = parse_yaml(yaml_text) if yaml_text.strip() else {}
    return verify(
        anonymized, config_data, not_names=list(not_names), anonymizer=anonymizer
    )


def save_outputs(files, anonymizer, yaml_text, mode, out_dir):
    """Write pseudonymized Typst output. ``mode`` is ``"multi"`` or ``"single"``.

    Returns the subdirectory that was written to.
    """
    out_path = Path(out_dir)
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
                )
                body = tmp.read_text(encoding="utf-8").strip()
                out_f.write(f"= {f.name}\n\n{body}\n\n")
        (sub_dir / "config.yaml").write_text(yaml_text, encoding="utf-8")
        return sub_dir

    raise ValueError(f"Unknown save mode: {mode!r}")


def save_version_outputs(files, anonymizer, yaml_text, mode, version_stage):
    """Write canonical outputs into a version stage's ``output`` directory."""
    stage = Path(version_stage)
    generated = save_outputs(files, anonymizer, yaml_text, mode, stage)
    output = stage / "output"
    if output.exists():
        output.rmdir()
    shutil.move(str(generated), str(output))
    return output
