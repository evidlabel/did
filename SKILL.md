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

Aimed at **local** models. PII removal: local agent holds the source; remote AI gets only the stripped handoff.

If this session is not local (inference, tools, logs, or storage leave the device): **track back**. A local model runs DID and delivers the pseudonymized / token-only set. Resume on that artifact only.

`#(P1V1)` / `#(P1V2)` = same person, two forms. `#(E1V1)` email · `#(A1V1)` address · `#(DOC1V1)` title. Reason from tokens.

Discover flags from the installed CLI: `did -h`, `did <path> -h`, `did -j`. After install or upgrade, rediscover — invoke from that output.

## Route

1. **Local** — full docs. Run DID. Review entities. Over-removal wins.
2. **Handoff** — token-only artifact (`anon/` or GUI Markdown/JSON/ZIP). Leftover names, re-identifying context.
3. **Remote** — if needed, that artifact only.

Done when that artifact is inspected and any remote recipient has only it.

## Run

```bash
uv tool install 'did[models] @ git+https://github.com/evidlabel/did.git'
did batch ./case_files -o ./work
```

https://github.com/evidlabel/did · `did gui` for human review.

```
<out>/
  anon/   AGENT-SAFE — token-only Typst + manifest.json
  keys/   SECRET — maps, vars, verification.json
```

Handoff is `anon/` (or the GUI export). `keys/` and the GUI workdir stay on-device.

Keep tokens verbatim. Real-source wording → citing skill for that type.

## Limits

- org names stay
- tokens in BibTeX/Hayagriva do not substitute on compile — stay in `anon/`
