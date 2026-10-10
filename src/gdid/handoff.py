"""Hand a project's pseudonymized documents to evid, as placeholder PDFs in an anon set.

Each document is rendered with its tokens written out as labels (``[PERSON 1]``) and its
title as its document token (``[DOCUMENT 1]``) — the same PDFs ``did anon --pdf`` writes —
and ingested into an evid set of type ``anon`` (created when missing).

The project remembers what it handed off in ``draft/handoff.json`` (in the project
workdir, which may hold identifiers, so it never leaves did's workspace): per source
document the evid uuid, the digest of what was sent, and the identities as they were.
A later handoff replaces each document in place (``evid doc replace``) instead of adding
a duplicate, and passes the label renames between the two versions (an identity that was
``[PERSON 1]`` and is now ``[PERSON 4]``) so evid can move its labels and quotes along.

evid is the ``evid`` command (``$DID_EVID_BIN`` overrides it), with its own data dir
(``$EVID_DB`` when set).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from did.core.entity_types import PLACEHOLDER_WORDS

from . import pipeline

STATE = "handoff.json"
TOKEN_RE = re.compile(r"#\(([A-Z]+\d+V\d+)\)")
ID_RE = re.compile(r"^([A-Z]+)(\d+)$")


class HandoffError(RuntimeError):
    pass


# ---------------------------------------------------------------- evid, as a command
def evid_bin() -> str:
    exe = os.environ.get("DID_EVID_BIN") or shutil.which("evid")
    if not exe:
        raise HandoffError("evid not found: install it, or set DID_EVID_BIN")
    return exe


def _evid(
    *args: str, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess:
    cmd = [evid_bin()]
    if os.environ.get("EVID_DB"):
        cmd += ["--db", os.environ["EVID_DB"]]
    r = subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise HandoffError(
            f"evid {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[-600:]}"
        )
    return r


def ensure_set(slug: str) -> None:
    r = _evid("set", "create", "-s", slug, "--type", "anon", check=False)
    if r.returncode != 0 and "already exists" not in (r.stdout + r.stderr):
        raise HandoffError(
            f"evid set create {slug}: {(r.stderr or r.stdout).strip()[-600:]}"
        )


def list_docs(slug: str) -> dict:
    r = _evid("doc", "list", "-s", slug, "-f", "json")
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError as e:
        raise HandoffError(f"evid doc list: not JSON: {r.stdout[:200]}") from e
    return {str(row.get("uuid")): row for row in rows if row.get("uuid")}


def add_doc(slug: str, pdf: Path) -> str:
    before = set(list_docs(slug))
    _evid("doc", "add", str(pdf), "-s", slug, "--no-index")
    new = sorted(set(list_docs(slug)) - before)
    if len(new) != 1:
        raise HandoffError(
            f"evid doc add {pdf.name}: expected one new document, saw {len(new)}"
        )
    return new[0]


def replace_doc(slug: str, uuid: str, pdf: Path, label_map: dict) -> dict:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(label_map, f, ensure_ascii=False)
        map_path = f.name
    try:
        r = _evid(
            "doc",
            "replace",
            "-s",
            slug,
            "-u",
            uuid,
            "--source",
            str(pdf),
            "--map",
            map_path,
            "-f",
            "json",
        )
    finally:
        os.unlink(map_path)
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {"output": r.stdout.strip()}


# ---------------------------------------------------------------- identities and labels
def identities(yaml_text: str) -> dict:
    """{entity id ("P1"): sorted variants} from the keys YAML."""
    out: dict = {}
    try:
        data = pipeline.read_keys(yaml_text) or {}
    except ValueError:
        return out
    for entries in data.values() if isinstance(data, dict) else []:
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("id"):
                out[str(entry["id"])] = sorted(
                    str(v) for v in entry.get("variants") or []
                )
    return out


def label_of(entity_id: str) -> str | None:
    m = ID_RE.match(entity_id)
    if not m:
        return None
    return f"[{PLACEHOLDER_WORDS.get(m.group(1), m.group(1))} {m.group(2)}]"


def label_map(old: dict, new: dict) -> dict:
    """{old label: new label} for identities whose number changed between two handoffs.

    An old identity is matched to the new one (same type) that shares most of its written
    forms. Unchanged labels are left out; so are identities that vanished."""
    out = {}
    for oid, ovars in old.items():
        m = ID_RE.match(oid)
        if not m or not ovars:
            continue
        best, score = None, 0
        for nid, nvars in new.items():
            n = ID_RE.match(nid)
            if not n or n.group(1) != m.group(1):
                continue
            s = len(set(ovars) & set(nvars))
            if s > score or (s == score and s and nid == oid):
                best, score = nid, s
        if best and best != oid:
            a, b = label_of(oid), label_of(best)
            if a and b:
                out[a] = b
    return out


# ---------------------------------------------------------------- state
def read_state(workdir: Path) -> dict:
    try:
        data = json.loads((workdir / "draft" / STATE).read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "set": data.get("set") or "",
        "at": data.get("at") or 0,
        "docs": data.get("docs") or {},
        "identities": data.get("identities") or {},
    }


def write_state(workdir: Path, state: dict) -> None:
    path = workdir / "draft" / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def digest(title_token: str, body: str) -> str:
    return hashlib.sha256(f"{title_token}\n{body}".encode()).hexdigest()[:16]


def pending(workdir: Path, docs: list[tuple[str, str, str]]) -> int:
    """How many documents changed (or were never handed off) since the last handoff."""
    state = read_state(workdir)["docs"]
    return sum(
        1
        for key, title, body in docs
        if state.get(key, {}).get("digest") != digest(title, body)
    )


# ---------------------------------------------------------------- the handoff
def handoff(
    workdir: Path,
    docs: list[tuple[str, str, str]],
    yaml_text: str,
    set_slug: str,
    language: str = "en",
) -> dict:
    """Render DOCS — (source key, "#(DOC1V1)", token-only body) — and put them in evid.

    Returns {"set", "added", "replaced", "unchanged", "stale": n, "pdf_dir"}.
    """
    from did.utils.pdf_render import render_pdfs

    if not docs:
        raise HandoffError("Nothing pseudonymized to hand off.")
    if not set_slug or "/" in set_slug or set_slug.startswith("."):
        raise HandoffError("Choose the evid set to hand off to (e.g. 2026-014-anon).")
    workdir = Path(workdir)
    state = read_state(workdir)
    if state["set"] and state["set"] != set_slug:
        state = {
            "set": set_slug,
            "at": 0,
            "docs": {},
            "identities": {},
        }  # another set: start over
    ids_now = identities(yaml_text)
    renames = label_map(state["identities"], ids_now) if state["identities"] else {}

    out_dir = workdir / "handoff" / time.strftime("%Y%m%d-%H%M%S")
    bodies = [(title, body) for _, title, body in docs]
    tokens = set()
    for title, body in bodies:
        tokens.update(TOKEN_RE.findall(title + "\n" + body))
    pdfs = render_pdfs(
        bodies, tokens, out_dir, mode="placeholder", language=language, titles=True
    )

    ensure_set(set_slug)
    present = list_docs(set_slug)
    report = {
        "set": set_slug,
        "added": 0,
        "replaced": 0,
        "unchanged": 0,
        "stale": 0,
        "pdf_dir": str(out_dir),
    }
    for (key, title, body), pdf in zip(docs, pdfs, strict=True):
        d = digest(title, body)
        was = state["docs"].get(key) or {}
        uuid = was.get("uuid")
        if uuid and uuid in present:
            if was.get("digest") == d and not renames:
                report["unchanged"] += 1
                continue
            r = replace_doc(set_slug, uuid, pdf, renames)
            report["replaced"] += 1
            report["stale"] += int(r.get("stale") or 0)
        else:
            uuid = add_doc(set_slug, pdf)
            report["added"] += 1
        state["docs"][key] = {
            "uuid": uuid,
            "title": title,
            "digest": d,
            "pdf": pdf.name,
        }
    state.update({"set": set_slug, "at": time.time(), "identities": ids_now})
    write_state(workdir, state)
    return report
