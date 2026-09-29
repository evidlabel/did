---
name: did
description: >
  Use when stripping PII from legal, medical, HR, or other personal-data
  documents; when a local agent holds source files a non-local or cloud
  model must not see; when a non-local or cloud model is asked to de-id or
  read that source; when preparing a de-identified or token-only handoff;
  or when installing or running `did` on a real case. Triggers: anonymise,
  de-id, pseudonymise, PII removal, "keep names out", `#(P1V1)`, LLM
  handoff, did-anon.
---

# did

Aimed at **local** models. PII removal: the local agent holds the source; a remote model gets only the stripped handoff.

If this session is not local (inference, tools, logs, or storage leave the device): **track back**. A local model runs did and delivers the pseudonymized, token-only set. Resume on that artifact only.

`#(P1V1)` / `#(P1V2)` = same person, two forms. `#(E1V1)` email · `#(A1V1)` address · `#(O1V1)` organisation · `#(DOC1V1)` title. Reason from tokens.

Discover flags from the installed CLI: `did -h`, `did <path> -h`, `did -j`. After install or upgrade, rediscover — invoke from that output. Danish text needs `-l da`. Swedish text needs `-l sv`.

## Route

1. **Run locally:** `did batch <in> -o <out>`.
2. **Review `<out>/anon/`** for leftover names and re-identifying context. Over-removal wins.
   - Local model available → it reviews, and gates what goes on to ingestion.
   - No model → a human reviews in `did gui` (extra `did[gui]`).
   - Neither → still continue, but record the output as **unreviewed** (for example, an `UNREVIEWED` note in `<out>/anon/`, and say so in the report).
3. **Hand off only `<out>/anon/`.** It is token-only Typst (`D1.typ`, or `combined.typ` when documents are combined) plus `manifest.json`. The tokens are raw `#(P1V1)` markup, and the directory is not PDFs. `evid --db evid doc add` accepts PDFs, a directory of PDFs, or a URL, so it does not ingest `anon/`. A remote model gets `<out>/anon/` and nothing else. Nothing in did or evid turns that Typst into a case PDF. Name that gap and stop rather than passing `anon/` to `doc add`.
4. **Keep `<out>/keys/` out of Git.** Add it to `.gitignore` before the first commit.

Done when `<out>/anon/` is reviewed or flagged unreviewed, it is the only thing handed to a remote model, and `keys/` is ignored.

## Output

```
<out>/
  anon/   AGENT-SAFE — token-only Typst + manifest.json
  keys/   SECRET — maps, vars, verification.json
```

The handoff is `anon/` (or the GUI Markdown/JSON/ZIP export). `keys/` and the GUI workdir stay on the device. Keep tokens verbatim.

## Install

```bash
uv tool install 'did[models] @ git+https://github.com/evidlabel/did.git'
```

`did[gui]` for human review, or `did[all]`.

## Limits

- Organizations are tokenised (`#(O1V1)`). Do not assume organisation names stay in the text.
- Tokens inside BibTeX/Hayagriva do not substitute on compile, so they stay in `anon/`.
