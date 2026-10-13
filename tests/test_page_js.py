"""Node checks for the page's pure token and find helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_token_hit_and_find_wrap():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
const assert = require("assert");
const { tokenAt, tokenSpans, findWrap, agentPaste } = require("./src/gdid/web/page.js");
const text = "Say #(PH1V2) then #(P1V1) and [ORGANIZATION 3].";
const phone = tokenAt(text, text.indexOf("#(PH"));
assert.strictEqual(phone.type, "PHONE_NUMBER");
assert.strictEqual(phone.number, 1);
assert.strictEqual(phone.variant, 2);
const person = tokenAt(text, text.indexOf("#(P1"));
assert.strictEqual(person.type, "PERSON");
assert.strictEqual(person.variant, 1);
const org = tokenAt(text, text.indexOf("[ORGANIZATION"));
assert.strictEqual(org.type, "ORGANIZATION");
assert.strictEqual(org.number, 3);
assert.strictEqual(tokenAt(text, 0), null);
assert.strictEqual(tokenSpans(text).length, 3);
const hay = "abc abc";
assert.deepStrictEqual(findWrap(hay, "abc", 1, false), { start: 4, end: 7 });
assert.deepStrictEqual(findWrap(hay, "abc", 5, false), { start: 0, end: 3 });
assert.deepStrictEqual(findWrap(hay, "abc", 4, true), { start: 0, end: 3 });
assert.strictEqual(findWrap(hay, "", 0, false), null);
const pseudo = { text: "Hello #(P1V1).", state: "PSEUDONYMIZED", original: false };
assert.strictEqual(agentPaste(pseudo, ""), "Hello #(P1V1).");
assert.strictEqual(agentPaste(pseudo, " #(P1V1) "), " #(P1V1) ");
assert.strictEqual(agentPaste({ text: "John Doe", state: "RAW", original: true }, "John Doe"), null);
assert.strictEqual(agentPaste({ text: "John Doe", state: "RAW", original: false }, ""), null);
assert.strictEqual(agentPaste({ text: "John Doe", state: "UNANON", original: false }, "John Doe"), null);
assert.strictEqual(agentPaste({ text: "", state: "EMPTY", original: false }, "John Doe"), null);
assert.strictEqual(agentPaste(null, "John Doe"), null);
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)


def test_keyword_review_ships_the_yaml():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
const assert = require("assert");
const { keywordReview, fullCheck, agentSubmit } = require("./src/gdid/web/page.js");
assert.strictEqual(keywordReview(""), null);
assert.strictEqual(keywordReview("  \n\t"), null);
assert.strictEqual(keywordReview(null), null);
const yaml = "PERSON:\n- id: p1\n  variants:\n  - Jane Roe\n";
const prompt = keywordReview(yaml);
assert.ok(prompt.includes(yaml.trim()));
assert.ok(/not PII/.test(prompt));
assert.ok(/combined/.test(prompt));
assert.ok(/Do not edit draft\/entities\.yaml/.test(prompt));
assert.ok(prompt.includes("```yaml\n"));
const fenced = keywordReview("note: ```\n");
assert.ok(fenced.includes("````yaml\n"));
assert.ok(fenced.endsWith("\n````"));
const wrapped = agentSubmit(prompt);
assert.ok(wrapped.paste.startsWith("\u001b[200~"));
assert.ok(wrapped.paste.endsWith("\u001b[201~"));
assert.ok(wrapped.paste.includes("Jane Roe"));
assert.ok(wrapped.delay >= 200 && wrapped.delay <= 1500);
assert.strictEqual(agentSubmit("end\u001b[201~then").paste, "\u001b[200~endthen\u001b[201~");
assert.strictEqual(agentSubmit("x".repeat(200000)).delay, 1500);
assert.strictEqual(fullCheck("", [], 0), null);
assert.strictEqual(fullCheck("  \n", [{ text: "  \n" }], 0), null);
assert.strictEqual(fullCheck(null, null, 0), null);
const checked = fullCheck(yaml, [{ name: "note.pdf", text: "Hello #(P1V1)." }], 0);
assert.ok(checked.includes(yaml.trim()));
assert.ok(checked.includes("note.pdf"));
assert.ok(checked.includes("Hello #(P1V1)."));
assert.ok(/not PII/.test(checked));
assert.ok(/variants list/.test(checked));
assert.ok(/Leftovers:/.test(checked));
assert.ok(/Do not edit draft\/entities\.yaml/.test(checked));
assert.ok(checked.includes("```yaml\n"));
assert.ok(checked.includes("```\nHello #(P1V1).\n```"));
const yamlOnly = fullCheck("PERSON: []\n", [], 2);
assert.ok(yamlOnly.includes("2 documents are still source text"));
assert.ok(/Skip leftovers/.test(yamlOnly));
const textOnly = fullCheck("", [{ name: "  ", text: "has ``` inside" }], 1);
assert.ok(textOnly.includes("Keyword YAML: none yet."));
assert.ok(textOnly.includes("1 document is still source text"));
assert.ok(textOnly.startsWith("Full check"));
assert.ok(textOnly.includes("````\nhas ``` inside\n````"));
assert.ok(textOnly.includes("document\n"));
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)


def test_annotation_queue_names_each_span():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
const assert = require("assert");
const { annotationQueue } = require("./src/gdid/web/page.js");
assert.strictEqual(annotationQueue("PERSON: []\n", []), null);
assert.strictEqual(annotationQueue("", [{ text: "  " }]), null);
const prompt = annotationQueue("PERSON:\n- id: p1\n", [
  { text: "Jane Roe", document: "letter.txt", source: true },
  { text: "#(P1V1)", document: "note.pdf", source: false },
]);
assert.ok(prompt.includes("1. Jane Roe — letter.txt (source view)"));
assert.ok(prompt.includes("2. #(P1V1) — note.pdf (pseudonymized view)"));
assert.ok(/not PII/.test(prompt));
assert.ok(/variants list/.test(prompt));
assert.ok(/Name the type/.test(prompt));
assert.ok(/original wording/.test(prompt));
assert.ok(/Do not edit that file/.test(prompt));
assert.ok(prompt.includes("```yaml\nPERSON:"));
const bare = annotationQueue("", [{ text: "Acme" }]);
assert.ok(bare.includes("1. Acme — (pseudonymized view)"));
assert.ok(bare.includes("Keyword YAML: none yet."));
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)


def test_preview_mode_maps_placeholder_fake_and_key():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
const assert = require("assert");
const { PREVIEW_MODES, previewModeId, previewModeBody } = require("./src/gdid/web/page.js");
assert.deepStrictEqual(PREVIEW_MODES.map((item) => item.id), [
  "placeholder", "tokens", "fake", "redacted", "key",
]);
assert.strictEqual(previewModeId("plain", false), "placeholder");
assert.strictEqual(previewModeId("typst", false), "tokens");
assert.strictEqual(previewModeId("synthetic", false), "fake");
assert.strictEqual(previewModeId("redacted", false), "redacted");
assert.strictEqual(previewModeId("synthetic", true), "key");
assert.strictEqual(previewModeId("nope", false), "tokens");
assert.deepStrictEqual(previewModeBody("placeholder"), { original: false, format: "plain" });
assert.deepStrictEqual(previewModeBody("tokens"), { original: false, format: "typst" });
assert.deepStrictEqual(previewModeBody("fake"), { original: false, format: "synthetic" });
assert.deepStrictEqual(previewModeBody("redacted"), { original: false, format: "redacted" });
assert.deepStrictEqual(previewModeBody("key"), { original: true });
assert.deepStrictEqual(previewModeBody("nope"), { original: false, format: "typst" });
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)
