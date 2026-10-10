"""Hand pseudonymized documents to evid: placeholder PDFs, added once, replaced in place after."""

import json
import shutil
import subprocess

import pytest

from gdid import handoff

pytestmark = pytest.mark.skipif(
    not (shutil.which("evid") and shutil.which("typst")),
    reason="needs the evid and typst commands",
)

YAML_V1 = """PERSON:
  - id: "P1"
    variants: ["Jane Roe", "J. Roe"]
  - id: "P2"
    variants: ["John Doe"]
ORGANIZATION:
  - id: "O1"
    variants: ["Acme Property ApS"]
"""
# a new review: John Doe is now P1 and Jane Roe is P2 (renumbered)
YAML_V2 = """PERSON:
  - id: "P1"
    variants: ["John Doe"]
  - id: "P2"
    variants: ["Jane Roe", "J. Roe"]
ORGANIZATION:
  - id: "O1"
    variants: ["Acme Property ApS"]
"""


def test_label_map():
    old, new = handoff.identities(YAML_V1), handoff.identities(YAML_V2)
    assert old["P1"] == ["J. Roe", "Jane Roe"]
    assert handoff.label_map(old, new) == {
        "[PERSON 1]": "[PERSON 2]",
        "[PERSON 2]": "[PERSON 1]",
    }
    assert handoff.label_map(old, old) == {}


def _evid(db, *args):
    r = subprocess.run(
        ["evid", "--db", str(db), *args], capture_output=True, text=True, check=True
    )
    return r.stdout


def test_handoff_adds_then_replaces(tmp_path, monkeypatch):
    db = tmp_path / "evid"
    monkeypatch.setenv("EVID_DB", str(db))
    work = tmp_path / "did" / "2026-014"
    (work / "draft").mkdir(parents=True)
    docs = [
        ("/raw/letter.pdf", "#(DOC1V1)", "#(P1V1) moved out. #(O1V1) kept the deposit.")
    ]

    r = handoff.handoff(work, docs, YAML_V1, "2026-014-anon", "en")
    assert r["added"] == 1 and r["replaced"] == 0
    rows = json.loads(_evid(db, "doc", "list", "-s", "2026-014-anon", "-f", "json"))
    assert len(rows) == 1 and rows[0]["title"] == "[DOCUMENT 1]"
    uuid = rows[0]["uuid"]
    sets = _evid(db, "set", "list")
    assert "anon" in sets
    state = json.loads((work / "draft" / "handoff.json").read_text())
    assert (
        state["docs"]["/raw/letter.pdf"]["uuid"] == uuid
        and state["set"] == "2026-014-anon"
    )
    assert handoff.pending(work, docs) == 0

    # a label on the handed-off text, in evid
    _evid(
        db,
        "label",
        "add",
        "-s",
        "2026-014-anon",
        "-u",
        uuid,
        "--key",
        "moved",
        "--text",
        "[PERSON 1] moved out.",
    )

    # same text again: nothing to do
    assert handoff.handoff(work, docs, YAML_V1, "2026-014-anon", "en")["unchanged"] == 1

    # the review renumbered the people: the document is replaced in place, the label follows
    docs2 = [
        ("/raw/letter.pdf", "#(DOC1V1)", "#(P2V1) moved out. #(O1V1) kept the deposit.")
    ]
    assert handoff.pending(work, docs2) == 1
    r = handoff.handoff(work, docs2, YAML_V2, "2026-014-anon", "en")
    assert r["replaced"] == 1 and r["added"] == 0 and r["stale"] == 0
    rows = json.loads(_evid(db, "doc", "list", "-s", "2026-014-anon", "-f", "json"))
    assert [x["uuid"] for x in rows] == [uuid]
    labels = json.loads(
        _evid(db, "label", "ls", "-s", "2026-014-anon", "-u", uuid, "-f", "json")
    )
    moved = next(
        x
        for x in (labels if isinstance(labels, list) else labels["labels"])
        if x["key"] == "moved"
    )
    assert moved["text"] == "[PERSON 2] moved out."


def test_handoff_needs_a_set(tmp_path):
    with pytest.raises(handoff.HandoffError, match="Choose the evid set"):
        handoff.handoff(tmp_path, [("a", "#(DOC1V1)", "x")], YAML_V1, "", "en")
    with pytest.raises(handoff.HandoffError, match="Nothing pseudonymized"):
        handoff.handoff(tmp_path, [], YAML_V1, "s-anon", "en")
