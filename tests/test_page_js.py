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
const { keywordReview, agentSubmit } = require("./src/gdid/web/page.js");
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
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)
