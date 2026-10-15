![CI](https://github.com/evidlabel/did/actions/workflows/ci.yml/badge.svg)![Version](https://img.shields.io/github/v/release/evidlabel/did)![License](https://img.shields.io/badge/license-MIT-blue.svg)

# did

**Aim.** Local-first review and pseudonymization of identity-bearing documents so a human can correct detection, and a remote model never sees the source.

**Use.** `did extract` finds entities into a YAML map; you review and fix that map; `did anon` applies it and emits pseudonymized documents into one output folder, or a token-only Markdown/JSON/ZIP handoff. `did anon --pdf` compiles a placeholder PDF per document (labels such as `[PERSON 1]`, never the real values) for an ordinary `evid doc add`. Placeholders like `#(P1V1)` / `#(P1V2)` are the same person, two written forms.

**Features.**
- CLI + GUI over one on-disk case workdir (mutable draft, one rewritten output)
- Entity review: type correction, aliases, identity merge, persistent exclusions
- Searchable preview, clickable keys, manual labelling
- PDF / DOCX / Markdown / plain-text and other text formats extraction
- Advisory sweep that re-scans for identifiers that survived replacement (an aid to review, not a verdict)
- Placeholder-PDF handoff (`did anon --pdf`): PDFs only, no key set, ready for `evid doc add`
- **Hand off to evid** (GUI): the reviewed documents go into the case's evid set of type `anon` as placeholder PDFs (title `[DOCUMENT n]`). A later handoff replaces them in place, and evid's labels follow identities that were renumbered. The map of sources to evid documents stays in the project's `draft/handoff.json`.
- Workbench embedding: `did gui` opened with `?embed=1&project=<folder>&set=<evid set>` shows that one project, created when the folder is empty. It follows the workbench's light or dark theme and reports how many documents wait for a handoff.
- Faker identities, or irreversible `[REDACTED]`

## GUI

<img src="gui.png" alt="DID desktop review: project tree, pseudonymized preview, and entity table" width="800">

## Install

Python 3.12+, [uv](https://docs.astral.sh/uv/). The GUI is part of the base install: a local page on 127.0.0.1, shown in the `did-app` window when that binary is built, otherwise in the browser. Language models are an extra.

```bash
uv tool install 'did[models] @ git+https://github.com/evidlabel/did.git'   # CLI + spaCy models
uv tool install 'did[all] @ git+https://github.com/evidlabel/did.git'      # same, plus every model
did --help
did gui                 # did-app window, or the browser if it is not built
did gui --browser       # always the browser
did gui --agent ''      # Agent pane starts a shell instead of OpenCode
did models da           # fetch only the models a language needs (here Danish + English)
```

`did[gui]` is an empty extra so older install lines keep resolving. A second `did gui` raises the running window. In a checkout, `make tool` reinstalls this tree as the `did` tool and puts `did-app` on PATH. `make app-build` builds the window only.

Agent, in the menu bar, opens a pane on the right. It stays closed until then, so reading the source does not start a shell. With a project open, opening it starts a shell in that project's folder and types `opencode` when OpenCode is installed. Point OpenCode at a local model for this pane: the review sends pseudonymized text, and a local model keeps even that on the machine. `$DID_AGENT` or `did gui --agent` replaces the command; an empty command starts a shell. Selection types the pseudonymized preview into the terminal. Original text stays on this machine. Right-click a selection and choose Annotate. The span joins a queue in the identity pane (`draft/annotations.json`). Send to local model gives that queue and the keyword YAML to the agent, which says what to remove, which identity a span belongs on, and what is new. It does not rewrite the keys file. A span taken from the source view stays on this machine. Full check sends the keyword YAML and every pseudonymized document to that local agent. It lists trivial and non-PII entries to remove, variants of the same person or organisation to group under one identity, and leftovers still in the text. The agent replies in the terminal and does not rewrite the keys file. Original text is not sent. That YAML is not part of the remote handoff. Edit identities, in the same settings menu as the language, is off. It is the page's own controls for change type, merge, add variant, add selection, and do-not-pseudonymize.

The preview shows a document as soon as its text has been read. The header selects Placeholder (`[PERSON 1]`), Tokens (`#(P1V1)`), Fake, Redacted, or Key. Key is the source and stays on this machine. Opening a project, or choosing Key, does not scan. Find identities, or one of the other modes the first time, runs detection on the whole set so one person keeps one token. A saved review is applied without that scan. Until then the text on screen is the source: Selection and the handoff wait for the pseudonymized text. Unchanged documents stay cached for the rest of the GUI session, so opening them again does not wait on extraction or detection.

Missing models never block you. The GUI downloads the model for a document's
language the first time you detect in it, and `did models [da en sv]` installs
them ahead of time. Only the missing model wheels are installed; no other
package changes. In a checkout, `make models` does the same.

## Development

```bash
uv sync --all-extras
uv run pytest -v
```

## Disclaimer

Automated detection is not a guarantee of anonymity. Review before sharing.

## License

License: [MIT](LICENSE). CLI: `did -h`. Agents: [`SKILL.md`](SKILL.md).
