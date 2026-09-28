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

Strip is this CLI (spaCy/Presidio), not the chat model. The local process holds the source. A remote session gets only the token handoff.

If this session is not local (inference, tools, logs, or storage leave the device): do not read the source. A local `did` run delivers `anon/`. Resume on that artifact only.

`#(P1V1)` / `#(P1V2)` = same person, two forms. `#(E1V1)` email · `#(A1V1)` address · `#(O1V1)` org · `#(DOC1V1)` title. Reason from tokens.

Discover flags from the installed CLI: `did -h`, `did <path> -h`, `did -j`. After install or upgrade, rediscover. Danish text needs `-l da`. Swedish text needs `-l sv`.

## Run

```bash
uv tool install 'did[models] @ git+https://github.com/evidlabel/did.git'
did batch ./case_files -o ./work -l da
```

https://github.com/evidlabel/did · `did gui` for a human pass (extra `did[gui]`, or `did[all]`).

```
<out>/
  anon/   AGENT-SAFE — token-only Typst + manifest.json
  keys/   SECRET — maps, vars, verification.json
```

Handoff is `anon/` (or the GUI export). `keys/` and the GUI workdir stay on-device. Over-removal wins.

## Limits

- Organizations are detected (`#(O1V1)`). Do not assume org names stay.
- Tokens in BibTeX/Hayagriva do not substitute on compile. Keep them in `anon/`. They are not human-readable headings.
