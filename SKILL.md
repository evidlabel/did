---
name: did
description: >
    Use when removing PII from unpublished data.    
Use when stripping PII from legal, medical, HR, or other personal-data
  documents; when a local agent holds source files a non-local or cloud
  model must not see; when a non-local or cloud model is asked to de-id or
  read that source; when preparing a de-identified or token-only handoff;
  or when installing or running `did` on a real case. Triggers: anonymise,
  de-id, pseudonymise, PII removal, "keep names out", `#(P1V1)`, LLM
  handoff, `did extract`, `did anon`.
---

# did

Aimed at **local** models. PII removal: the local agent holds the source; a remote model gets only the stripped handoff.

If this session is not local (inference, tools, logs, or storage leave the device): **track back**. A local model runs did and delivers the pseudonymized set. Resume on that artifact only.

`#(P1V1)` / `#(P1V2)` = same person, two forms. `#(E1V1)` email · `#(A1V1)` address · `#(O1V1)` organisation · `#(DOC1V1)` title. Reason from tokens.

Discover the flags from the installed CLI: `did -h`, `did <path> -h`, `did -j`. After install or upgrade, rediscover — invoke from that output. Danish text needs `-l da`; Swedish text needs `-l sv`.

## Route

The loop is a map you review, then apply: **extract → fix the map → anon → check the output**. One command per step; one `<case>.yaml` threads through all of them.

1. **Extract.** `did extract <in> -c <case>.yaml` (add `-l da` / `-l sv`). Detection writes one YAML map.
2. **Fix the map.** Over-removal wins: merge variants of one identity, drop trivial entries (single letters, stray numbers) that would mangle the text, add what detection missed.
   - Local model available → it fixes the map here.
   - No model → a human edits the YAML, or reviews in `did gui` (extra `did[gui]`).
   - Neither → still continue, but record the output as **unreviewed**: run `evid --db evid tag assign <uuid> unreviewed` for each source document already in the case set, and say so in the report. evid stores that tag as `<slug>.unreviewed`. The handoff PDFs are not evid documents, so they have no uuid to tag.
3. **Anon.** `did anon <in> -c <case>.yaml -o <out> --pdf`. Applies the reviewed map with no re-detection. Writes `<out>/anon/` (token-only Typst), `<out>/keys/` (secret), and `<out>/pdf/` (handoff). Without `-c` it detects and pseudonymizes in one pass. Inputs may be files or directories (`.pdf`, `.docx`, and common text formats).
4. **Check the output, then hand off `<out>/pdf/`.** `anon` prints an automated sweep and records counts in `anon/manifest.json` (full report in `keys/sweep.json`). The sweep is mechanical — it cannot judge re-identifying context — so it is an **aid to your review, never a substitute: read the tokens yourself.** `--pdf` compiles one placeholder PDF per document (`D1.pdf`, or `combined.pdf` with `--combine`); every identifier is written out as a label (`[PERSON 1]`, `[DOCUMENT 1]`, and the same for the other types). `pdf/` holds PDFs and nothing else — no key set, no config — so it is safe to share and `evid --db evid doc add <out>/pdf` ingests it as ordinary documents. `--render redacted|synthetic` changes the labels; `placeholder` is the default. `<out>/anon/` stays the token-only Typst for a text-only agent; it is not PDFs, so `doc add` refuses it — pass `pdf/` instead.
5. **Keep `<out>/keys/` out of Git.** Add it to `.gitignore` before the first commit.

Done when `<case>.yaml` has been reviewed, `<out>/pdf/` is the only thing handed on, and `keys/` is ignored.

## Output

```
<out>/
  pdf/    HANDOFF (with --pdf) — placeholder PDFs, no keys
  anon/   AGENT-SAFE — token-only Typst + manifest.json
  keys/   SECRET — config.yaml, vars, sweep.json
```

The handoff is `pdf/` (or the GUI Markdown/JSON/ZIP export). `keys/` and the GUI workdir stay on the device. `sweep.json` is advisory and contains PII. Keep tokens verbatim.

## Install

```bash
uv tool install 'did[models] @ git+https://github.com/evidlabel/did.git'
```

`did[gui]` for human review, or `did[all]`.

## Limits

- Organizations are tokenised (`#(O1V1)`). Do not assume organisation names stay in the text.
- Tokens inside BibTeX/Hayagriva do not substitute on compile, so they stay in `anon/`.
- `anon` always writes a key set; there is no output mode that emits only a pseudonymized document.
