/* did GUI page. Pure helpers are at the top so node can require them. */

const PREFIX_TYPE = {
  PH: "PHONE_NUMBER",
  DT: "DATE_NUMBER",
  ID: "ID_NUMBER",
  CD: "CODE_NUMBER",
  GN: "GENERAL_NUMBER",
  URL: "URL",
  DOC: "DOCUMENT_TITLE",
  P: "PERSON",
  O: "ORGANIZATION",
  E: "EMAIL_ADDRESS",
  A: "LOCATION",
};

const WORD_TYPE = {
  PHONE: "PHONE_NUMBER",
  DATE: "DATE_NUMBER",
  ID: "ID_NUMBER",
  CODE: "CODE_NUMBER",
  NUMBER: "GENERAL_NUMBER",
  URL: "URL",
  DOCUMENT: "DOCUMENT_TITLE",
  PERSON: "PERSON",
  ORGANIZATION: "ORGANIZATION",
  EMAIL: "EMAIL_ADDRESS",
  ADDRESS: "LOCATION",
};

// Longest prefixes and words first so PH beats P and ORGANIZATION beats a shorter word.
const TOKEN_RE =
  /#\((PH|DT|ID|CD|GN|URL|DOC|P|O|E|A)(\d+)V(\d+)\)|\[(ORGANIZATION|DOCUMENT|ADDRESS|PERSON|NUMBER|PHONE|EMAIL|CODE|DATE|URL|ID) (\d+)\]/g;

function tokenSpans(text) {
  const spans = [];
  TOKEN_RE.lastIndex = 0;
  let match;
  while ((match = TOKEN_RE.exec(text))) {
    const prefix = match[1];
    const number = Number(match[2] || match[5]);
    const variant = match[3] ? Number(match[3]) : null;
    const word = match[4];
    spans.push({
      start: match.index,
      end: match.index + match[0].length,
      type: prefix ? PREFIX_TYPE[prefix] : WORD_TYPE[word],
      number,
      variant,
      prefix: prefix || null,
    });
  }
  return spans;
}

function tokenAt(text, position) {
  return (
    tokenSpans(text).find((span) => span.start <= position && position <= span.end) ||
    null
  );
}

function agentPaste(preview, selection) {
  // Source text and an empty preview stay out of the terminal.
  // UNANON is the source shown while detection is still running.
  if (
    !preview ||
    preview.original ||
    preview.state === "RAW" ||
    preview.state === "UNANON" ||
    preview.state === "EMPTY"
  ) {
    return null;
  }
  const picked = typeof selection === "string" ? selection : "";
  const text = picked.trim() ? picked : preview.text || "";
  if (!String(text).trim()) return null;
  return String(text);
}

function fenced(text, info) {
  let fence = "```";
  while (text.includes(fence)) fence += "`";
  return info ? `${fence}${info}\n${text}\n${fence}` : `${fence}\n${text}\n${fence}`;
}

function fullCheck(yaml, documents, withheld) {
  // One local-agent pass: the identity map, then every pseudonymized document.
  // A blank map and no text is not a check. Source text never belongs here.
  const map = typeof yaml === "string" ? yaml.replace(/\s+$/, "") : "";
  const docs = [];
  for (const doc of Array.isArray(documents) ? documents : []) {
    const text = doc && typeof doc.text === "string" ? doc.text.replace(/\s+$/, "") : "";
    if (!text.trim()) continue;
    const name = String(doc.name == null ? "" : doc.name).replace(/\s+/g, " ").trim() || "document";
    docs.push({ name, text });
  }
  if (!map.trim() && !docs.length) return null;
  const lines = [
    "Full check for the local pseudonymizer. The YAML is the identity map (draft/entities.yaml). Each following block is pseudonymized text, not the source. Tokens such as #(P1V1) and [PERSON 1] stand in for identifiers.",
    "Reply in this terminal with three lists. Do not edit draft/entities.yaml. The GUI holds its own copy and would overwrite a file change. Do not ask for the original text.",
    "1. Remove: trivial entries (a single letter, a stray number) and entries that are not PII.",
    "2. Variants: forms of the same person, or the same organisation, that are separate entries. Name the identity that should keep them, and which entries should be added to its variants list.",
    "3. Leftovers: real identifiers still visible in the pseudonymized text. Name the document.",
    "",
  ];
  if (map.trim()) lines.push("Keyword YAML:", fenced(map, "yaml"), "");
  else lines.push("Keyword YAML: none yet.", "");
  const skipped = Number(withheld) > 0 ? Number(withheld) : 0;
  if (skipped) {
    const noun = skipped === 1 ? "document is" : "documents are";
    lines.push(`${skipped} ${noun} still source text and not included.`, "");
  }
  if (!docs.length) lines.push("Pseudonymized text: none yet. Skip leftovers.");
  else for (const doc of docs) lines.push(doc.name, fenced(doc.text), "");
  return lines.join("\n").replace(/\n+$/, "\n");
}

function annotationQueue(yaml, items) {
  // Spans a person marked. The local model places each one. An empty queue is not a job.
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {
    const text = item && typeof item.text === "string" ? item.text.trim() : "";
    if (!text) continue;
    const name = String(item.document == null ? "" : item.document).replace(/\s+/g, " ").trim();
    const where = item.source ? "source view" : "pseudonymized view";
    rows.push(name ? `${text} — ${name} (${where})` : `${text} — (${where})`);
  }
  if (!rows.length) return null;
  const map = typeof yaml === "string" ? yaml.replace(/\s+$/, "") : "";
  const lines = [
    "These spans were annotated in the review window and queued for you. They are not in the identity map yet.",
    "The YAML is the current map (draft/entities.yaml). Reply in this terminal. Do not edit that file. The GUI holds its own copy and would overwrite a file change.",
    "A span marked source view is original wording. Keep it in this terminal.",
    "For each span, say one of:",
    "1. Remove: it is trivial (a single letter, a stray number) or not PII.",
    "2. Variants: it is the same person or the same organisation as an existing identity. Name that identity and add the span to its variants list.",
    "3. New: it is a real identifier with no identity yet. Name the type.",
    "",
    "Annotations:",
    ...rows.map((row, index) => `${index + 1}. ${row}`),
    "",
  ];
  if (map.trim()) lines.push("Keyword YAML:", fenced(map, "yaml"));
  else lines.push("Keyword YAML: none yet.");
  return lines.join("\n").replace(/\n+$/, "\n");
}

function keywordReview(yaml) {
  // The local agent reviews the identity map. A blank map is not a review.
  const text = typeof yaml === "string" ? yaml.replace(/\s+$/, "") : "";
  if (!text.trim()) return null;
  let fence = "```";
  while (text.includes(fence)) fence += "`";
  return [
    "Review this keyword YAML from the local pseudonymizer (draft/entities.yaml). It maps identifiers to tokens. It is not a source document.",
    "Find entries that are not PII and should be removed, identities or variants that should be combined, a variant on the wrong identity, and an entry with the wrong type.",
    "Reply in this terminal with that list. Do not edit draft/entities.yaml. The GUI holds its own copy and would overwrite a file change.",
    "",
    `${fence}yaml`,
    text,
    fence,
  ].join("\n");
}

function agentSubmit(text) {
  // One bracketed paste, then Enter after the terminal has taken the text.
  // A cooked shell would otherwise run each YAML line on its own.
  const body = String(text == null ? "" : text).replace(/\u001b\[201~/g, "");
  return {
    paste: `\u001b[200~${body}\u001b[201~`,
    delay: Math.min(1500, 200 + Math.ceil(body.length / 80)),
  };
}

// Preview header. Key is the source. The others are renderings of the review.
const PREVIEW_MODES = [
  { id: "placeholder", format: "plain" },
  { id: "tokens", format: "typst" },
  { id: "fake", format: "synthetic" },
  { id: "redacted", format: "redacted" },
  { id: "key", format: null },
];

function previewModeId(format, showingSource) {
  if (showingSource) return "key";
  const match = PREVIEW_MODES.find((item) => item.format && item.format === format);
  return match ? match.id : "tokens";
}

function previewModeBody(mode) {
  const row = PREVIEW_MODES.find((item) => item.id === mode);
  if (row && row.id === "key") return { original: true };
  return { original: false, format: (row && row.format) || "typst" };
}

function findWrap(text, query, fromIndex, backward) {
  if (!query) return null;
  const hay = text.toLowerCase();
  const needle = query.toLowerCase();
  const start = Number.isFinite(fromIndex) ? fromIndex : 0;
  if (!backward) {
    let at = hay.indexOf(needle, start);
    if (at < 0 && start > 0) at = hay.indexOf(needle, 0);
    if (at < 0) return null;
    return { start: at, end: at + needle.length };
  }
  const slice = hay.slice(0, Math.max(0, start));
  let at = slice.lastIndexOf(needle);
  if (at < 0) at = hay.lastIndexOf(needle);
  if (at < 0) return null;
  return { start: at, end: at + needle.length };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    tokenSpans, tokenAt, findWrap, agentPaste, keywordReview, fullCheck, annotationQueue, agentSubmit,
    PREVIEW_MODES, previewModeId, previewModeBody,
  };
}

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const EMBED = params.get("embed") === "1";  // inside the workbench: hide the agent pane (Write owns terminals)
  if (EMBED) document.body.classList.add("embed");
  function setForcedTheme(name) {  // "light" | "dark"; else the system
    if (name === "light" || name === "dark") document.documentElement.dataset.theme = name;
    else delete document.documentElement.dataset.theme;
    try {
      for (const pane of T.list) pane.term.options.theme = termTheme();
    } catch {
      // the terminals are not set up yet (first call, at load)
    }
  }
  // In the workbench the theme arrives as data: a light or dark base, the workbench's core colours
  // (mapped onto this page's own) and its fonts. Alone, did stays on ?theme= or the system.
  const WB_VARS = {
    bg: ["--bg"], panel: ["--panel", "--control"], sunk: ["--panel2", "--hover"], line: ["--line"], ink: ["--fg"],
    muted: ["--muted", "--control-line"], accent: ["--accent", "--accent-hover"], "accent-low": ["--sel", "--control-hover"],
    "on-accent": ["--accent-fg"], warn: ["--warn"], err: ["--err"],
  };
  function workbenchTheme(data) {
    const style = document.documentElement.style, tokens = data.tokens || {}, fonts = data.fonts || {};
    for (const [key, vars] of Object.entries(WB_VARS)) {
      for (const v of vars) tokens[key] ? style.setProperty(v, tokens[key]) : style.removeProperty(v);
    }
    for (const [v, x] of [["--sans", fonts.sans], ["--sans-weight", fonts.weight], ["--mono", fonts.mono]]) {
      x ? style.setProperty(v, x) : style.removeProperty(v);
    }
    if (/^https?:\/\//.test(fonts.css || "")) {
      let link = document.getElementById("wb-fonts");
      if (!link) {
        link = document.createElement("link");
        link.id = "wb-fonts";
        link.rel = "stylesheet";
        document.head.append(link);
      }
      if (link.href !== fonts.css) link.href = fonts.css;
    }
    setForcedTheme(data.scheme);
  }
  setForcedTheme(params.get("theme"));
  window.addEventListener("message", (event) => {
    if (event.source === window.parent && event.data && event.data.type === "workbench-theme") workbenchTheme(event.data);
  });
  if (window.parent !== window) window.parent.postMessage({ type: "workbench-hello" }, "*");
  const state = {
    badge: -1,
    data: null,
    findFrom: 0,
    findQuery: "",
    entitiesKey: "",
    treeKey: "",
    agentDir: null,
    outputOpen: false,
  };
  let modalCallback = null;

  function headers() {
    return { "Content-Type": "application/json", "X-Did": "1" };
  }

  async function api(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 409 && payload.needs_ack) {
      const proceed = await askVerification(payload);
      if (!proceed) return payload;
      await api("/api/verify/ack");
      return api(path, body);
    }
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }

  async function refresh() {
    const response = await fetch("/api/state");
    state.data = await response.json();
    render(state.data);
  }

  function render(data) {
    $("status").textContent = data.error ? data.error : data.status || "";
    $("status").classList.toggle("err", Boolean(data.error));
    $("project-name").textContent = data.project_name || "No project";
    const ho = data.handoff || { pending: 0, set: "" };
    $("handoff").disabled = !data.can_handoff;
    $("handoff").classList.toggle("pending", ho.pending > 0);
    const evidNote = ho.set
      ? `${ho.set} · ${ho.pending ? ho.pending + " to hand off" : "up to date"}`
      : "";
    $("handoff").title = evidNote
      ? `Copy, package, or send the reviewed text. ${evidNote}`
      : "Copy, package, or send the reviewed text. Identity mappings are never included.";
    if (window.parent !== window && ho.pending !== state.badge) {
      state.badge = ho.pending;  // the workbench shows it on the Anonymization tab
      window.parent.postMessage({ type: "workbench-badge", count: ho.pending }, "*");
    }
    $("keys").textContent = (data.keys && data.keys.label) || "";
    $("keys").title = (data.keys && data.keys.path) || "";
    $("lang").value = data.language;
    $("profile").value = data.profile;
    const busy = Boolean(data.busy);
    document.body.classList.toggle("busy", busy);
    $("busy").hidden = !busy;
    $("busy").textContent = busy ? data.busy.message : "";
    const treeKey = JSON.stringify(data.projects);
    if (treeKey !== state.treeKey) {
      state.treeKey = treeKey;
      renderTree(data);
    }
    renderPreview(data);
    const entitiesKey = JSON.stringify(data.entities) + (data.entity_error || "");
    if (entitiesKey !== state.entitiesKey) {
      state.entitiesKey = entitiesKey;
      renderEntities(data);
      renderAnnotations(data);
    }
    const active = (data.projects || []).find((project) => project.active);
    const hasDocs = Boolean(active && (active.documents || []).length);
    const preview = data.preview || {};
    const finding = Boolean(data.busy && /Finding identities/i.test(data.busy.message || ""));
    const showingSource =
      preview.state === "RAW" || Boolean(preview.original) ||
      (preview.state === "UNANON" && !finding);
    const picker = $("view-mode");
    const mode = previewModeId(data.format, showingSource);
    if (picker.value !== mode) picker.value = mode;
    const keyOpt = picker.querySelector('option[value="key"]');
    const canKey = Boolean(preview.can_original || preview.state === "UNANON");
    if (keyOpt) keyOpt.disabled = !canKey;
    picker.disabled = !hasDocs && !canKey;
    $("read-only").hidden = !data.read_only;
    const langLabel = ($("lang").selectedOptions[0] && $("lang").selectedOptions[0].textContent) || "Settings";
    $("settings").textContent = langLabel;
    const saveLabel = ($("save-mode").selectedOptions[0] && $("save-mode").selectedOptions[0].textContent) || "";
    $("save").hidden = !data.scanned;
    $("save").disabled = Boolean(data.read_only) || busy || !data.scanned;
    $("save").title = saveLabel
      ? `Write the pseudonymized output (${saveLabel}). It replaces the previous output.`
      : "Write the pseudonymized output for this project.";
    const scanning = busy && data.busy && /Finding identities/i.test(data.busy.message || "");
    $("scan").hidden = Boolean(data.scanned) || Boolean(data.read_only) || !hasDocs;
    $("scan").disabled = busy || Boolean(data.read_only) || !hasDocs;
    $("scan").textContent = scanning ? "Finding identities…" : "Find identities";
    $("scan-again").hidden = !data.scanned || Boolean(data.read_only);
    const noIdentities = !(data.entities || []).length;
    const excluded = (active && active.excluded) || [];
    $("entity-excluded").hidden = !excluded.length;
    $("entity-excluded").textContent = excluded.length ? `Excluded (${excluded.length})` : "";
    $("entity-excluded").title = excluded.join("\n");
    $("entity-empty").hidden = !noIdentities;
    $("entities").hidden = noIdentities;
    $("entity-search").hidden = noIdentities;
    $("entity-type").hidden = noIdentities;
    $("entity-tools").classList.toggle("quiet", noIdentities);
    const canAsk = Boolean(data.scanned) || Boolean(data.read_only) || !noIdentities;
    $("ask-agent").hidden = !canAsk;
    syncEntityEdit();
    const agentDir = data.workdir || "";
    if (agentDir !== state.agentDir) {
      state.agentDir = agentDir;
      termProjectChanged();
    }
  }

  function renderTree(data) {
    const root = $("tree");
    root.textContent = "";
    for (const project of (data.projects || [])) {
      const block = document.createElement("div");
      block.className = "project" + (project.active ? " active" : "");
      const head = document.createElement("div");
      head.className = "row head";
      head.innerHTML = `<span class="mark">${project.mark || ""}</span><b></b>`;
      head.querySelector("b").textContent = project.label || project.name;
      head.title = project.path;
      head.onclick = () => api("/api/project/open", { path: project.path }).then(refresh);
      head.oncontextmenu = (event) => {
        event.preventDefault();
        projectMenu(event, project);
      };
      block.appendChild(head);
      if (project.active) {
        block.appendChild(groupLabel("Documents"));
        for (const doc of project.documents || []) {
          block.appendChild(docRow(doc, project));
        }
        if (project.output) {
          const files = project.output.files || [];
          const out = document.createElement("div");
          out.className = "row fold";
          out.textContent = `${state.outputOpen ? "▾" : "▸"} Output · ${files.length} · read-only`;
          out.onclick = () => {
            state.outputOpen = !state.outputOpen;
            renderTree(data);
          };
          out.oncontextmenu = (event) => {
            event.preventDefault();
            menu(event, [
              ["Copy output to…", () => copyOutput(project.output.path)],
              ["Copy path", () => copyPath(project.output.path)],
              ["Reveal folder", () => api("/api/reveal", { path: project.output.path })],
            ]);
          };
          block.appendChild(out);
          if (state.outputOpen) {
            for (const file of files) {
              const row = document.createElement("div");
              row.className = "row output-file" + (file.selected ? " sel" : "");
              row.textContent = file.label || file.name;
              row.title = file.name;
              row.onclick = () =>
                api("/api/documents/select", {
                  path: file.path,
                  version: project.output.path,
                }).then(refresh);
              row.oncontextmenu = (event) => {
                event.preventDefault();
                menu(event, [
                  ["View original text", () => viewOriginal(file, project.output.path)],
                  ["Copy path", () => copyPath(file.path)],
                  ["Reveal folder", () => api("/api/reveal", { path: file.path })],
                ]);
              };
              block.appendChild(row);
            }
          }
        }
      }
      root.appendChild(block);
    }
    if (!(data.projects || []).length) {
      root.textContent = "No recent projects.";
    }
  }

  function groupLabel(text) {
    const row = document.createElement("div");
    row.className = "row group";
    row.textContent = text;
    return row;
  }

  function docRow(doc, project) {
    const row = document.createElement("div");
    row.className = "row child" + (doc.selected ? " sel" : "") + (doc.missing ? " missing" : "");
    const modeLabel = { UNANON: "source", PSEUDO: "review", PENDING: "" }[doc.mode] || "";
    row.textContent = `${doc.mark || ""} ${doc.name}${modeLabel ? "  " + modeLabel : ""}`;
    row.title = doc.path;
    row.onclick = () => api("/api/documents/select", { path: doc.path }).then(refresh);
    row.oncontextmenu = (event) => {
      event.preventDefault();
      menu(event, [
        ["View original text", () => viewOriginal(doc)],
        ["Copy path", () => copyPath(doc.path)],
        ["Reveal folder", () => api("/api/reveal", { path: doc.path })],
        ["Remove document", () => removeDocument(doc.path)],
      ]);
    };
    return row;
  }

  function projectMenu(event, project) {
    menu(event, [
      ["Add documents", () => pickFiles(sourcesDir()).then((paths) => paths && addDocs(paths))],
      ["Rename", () => rename(project)],
      ["Meta", () => editMeta(project)],
      ["Copy path", () => copyPath(project.path)],
      ["Reveal folder", () => api("/api/reveal", { path: project.path })],
      ["Reveal keys folder", () => {
        const folder = (state.data && state.data.keys && state.data.keys.folder) || "";
        if (folder) api("/api/reveal", { path: folder });
      }],
      ["Remove from list", () => api("/api/project/remove", { path: project.path }).then(refresh)],
      [
        "Delete workdir…",
        async () => {
          if (!(await confirmBox(`Delete ${project.name}? This removes the workdir.`))) return;
          await api("/api/project/delete", { path: project.path, confirm: true });
          refresh();
        },
      ],
    ]);
  }

  function menu(event, items) {
    event.stopPropagation();
    const existing = $("menu");
    if (existing) existing.remove();
    const box = document.createElement("div");
    box.id = "menu";
    box.className = "menu";
    box.style.left = event.clientX + "px";
    box.style.top = event.clientY + "px";
    for (const [label, action] of items) {
      const button = document.createElement("button");
      button.textContent = label;
      button.disabled = !action;
      button.onclick = () => {
        box.remove();
        if (action) action();
      };
      box.appendChild(button);
    }
    document.body.appendChild(box);
  }

  function renderPreview(data) {
    const preview = data.preview || { text: "", state: "EMPTY" };
    $("badge").textContent = preview.state || "";
    $("badge").dataset.state = preview.state || "EMPTY";
    $("badge").title =
      preview.state === "UNANON"
        ? "Source text. It stays on this machine until detection replaces it."
        : "";
    $("doc-name").textContent = preview.name || "";
    const pre = $("preview");
    const text = preview.text || "";
    const plain =
      preview.original ||
      preview.state === "RAW" ||
      preview.state === "UNANON" ||
      preview.state === "EMPTY";
    if (
      pre.dataset.raw === text &&
      pre.dataset.plain === (plain ? "1" : "0") &&
      !preview.original
    ) {
      return;
    }
    pre.dataset.raw = text;
    pre.dataset.plain = plain ? "1" : "0";
    pre.textContent = "";
    if (!text) {
      pre.textContent = data.project_name
        ? "Add a document to read it here."
        : "Open a project to begin.";
      return;
    }
    if (plain) {
      pre.textContent = text;
      return;
    }
    const spans = tokenSpans(text);
    let cursor = 0;
    for (const span of spans) {
      pre.appendChild(document.createTextNode(text.slice(cursor, span.start)));
      const button = document.createElement("button");
      button.className = "tok p-" + (span.prefix || "W");
      button.textContent = text.slice(span.start, span.end);
      button.onclick = () => highlightEntity(span);
      pre.appendChild(button);
      cursor = span.end;
    }
    pre.appendChild(document.createTextNode(text.slice(cursor)));
  }

  function highlightEntity(span) {
    for (const row of document.querySelectorAll("#entities tbody tr")) {
      const match =
        row.dataset.type === span.type &&
        Number(row.dataset.number) === span.number &&
        (span.variant == null || Number(row.dataset.variantIndex) === span.variant);
      row.classList.toggle("hit", match);
      if (match) row.scrollIntoView({ block: "nearest" });
    }
  }

  function renderAnnotations(data) {
    const box = $("anno-queue");
    const list = $("anno-list");
    if (!box || !list) return;
    const items = data.annotations || [];
    box.hidden = !items.length;
    $("anno-count").textContent = items.length ? `Queue · ${items.length}` : "Queue";
    const key = JSON.stringify(items);
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    list.textContent = "";
    for (const item of items) {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = item.text;
      li.appendChild(span);
      if (item.document) {
        const doc = document.createElement("span");
        doc.className = "doc";
        doc.textContent = ` · ${item.document}`;
        li.appendChild(doc);
      }
      li.title = item.source
        ? "Annotated from the source view. It stays on this machine until the local model answers."
        : "Annotated from the pseudonymized view.";
      li.oncontextmenu = (event) => {
        event.preventDefault();
        menu(event, [["Remove", () => dropAnnotation(item.id)]]);
      };
      list.appendChild(li);
    }
  }

  function renderEntities(data) {
    const body = $("entities").querySelector("tbody");
    body.textContent = "";
    $("entity-error").textContent = data.entity_error || "";
    const filter = $("entity-search").value.trim().toLowerCase();
    const type = $("entity-type").value;
    for (const row of data.entities || []) {
      const blob = `${row.type} ${row.token} ${(row.variants || []).join(" ")}`.toLowerCase();
      if (type && row.type !== type) continue;
      if (filter && !blob.includes(filter)) continue;
      const tr = document.createElement("tr");
      tr.dataset.id = row.id;
      tr.dataset.type = row.type;
      tr.dataset.number = row.number;
      tr.dataset.variant = row.variant || "";
      tr.dataset.variantIndex = row.variant_index || "";
      if (row.child) tr.classList.add("child");
      tr.innerHTML = "<td></td><td></td><td></td><td></td>";
      const cells = tr.children;
      cells[0].textContent = row.child ? "" : row.type;
      cells[1].textContent = row.token;
      cells[1].title = row.id ? `YAML id ${row.id}` : "";
      cells[2].textContent = (row.variants || []).join(", ");
      cells[3].textContent = row.confidence == null ? "—" : `${Math.round(row.confidence * 100)}%`;
      tr.onclick = (event) => {
        if (!event.shiftKey && !event.metaKey && !event.ctrlKey) {
          for (const other of body.querySelectorAll("tr")) other.classList.remove("sel");
        }
        tr.classList.toggle("sel");
        syncEntityEdit();
      };
      body.appendChild(tr);
    }
    syncEntityEdit();
  }

  function manualEdits() {
    const box = $("manual-edits");
    return Boolean(box && box.checked);
  }

  function selectionInPreview() {
    const preview = $("preview");
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount || !preview) return false;
    const node = sel.anchorNode;
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return Boolean(el && preview.contains(el));
  }

  function syncEntityEdit() {
    const button = $("entity-edit");
    if (!button) return;
    button.hidden = !manualEdits() || (selectedRows().length === 0 && !selectionInPreview());
  }

  function editMenu(event) {
    const items = [];
    if (selectedRows().length) {
      for (const type of ["PERSON", "ORGANIZATION", "LOCATION", "EMAIL_ADDRESS", "PHONE_NUMBER"]) {
        items.push([`Change type to ${type}`, () => changeType(type)]);
      }
      items.push(["Add variant", () => addVariant()]);
      items.push(["Merge", () => mergeRows()]);
      items.push(["Do not pseudonymize", () => markNotName()]);
    }
    if (selectionInPreview()) items.push(["Add selection", () => addFromSelection()]);
    menu(event, items);
  }

  function selectedRows() {
    return [...document.querySelectorAll("#entities tbody tr.sel")];
  }

  function openModal(title, html, onOk) {
    $("modal-title").textContent = title;
    $("modal-body").innerHTML = html;
    $("modal").hidden = false;
    modalCallback = onOk;
    const field = $("modal-body").querySelector("input, textarea");
    if (field) field.focus();
  }

  function closeModal() {
    $("modal").hidden = true;
    modalCallback = null;
    $("modal-ok").textContent = "OK";
    $("modal-cancel").hidden = false;
  }

  function confirmBox(message) {
    return new Promise((resolve) => {
      openModal("Confirm", `<p></p>`, () => resolve(true));
      $("modal-body").querySelector("p").textContent = message;
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(false);
      };
    });
  }

  function askVerification(payload) {
    const lines = (payload.findings || [])
      .map((finding) => `${finding.severity}: “${finding.text}” — ${finding.document} line ${finding.line}`)
      .join("\n");
    const more = payload.more ? `\n…and ${payload.more} more` : "";
    return new Promise((resolve) => {
      openModal(
        "Output verification",
        `<p></p><pre class="findings"></pre>`,
        () => resolve(true),
      );
      $("modal-body").querySelector("p").textContent =
        (payload.summary || "The sweep found something.") +
        "\n\nIdentifiers are still readable in the pseudonymized output. Continue if they are false positives.";
      $("modal-body").querySelector("pre").textContent = lines + more;
      $("modal-ok").textContent = "Continue anyway";
      $("modal-cancel").onclick = () => {
        $("modal-ok").textContent = "OK";
        closeModal();
        resolve(false);
      };
    });
  }

  async function pickDialog(kind, start) {
    try {
      const result = await api("/api/fs/pick", { kind, path: start || "" });
      if (!result || result.paths == null) return null;
      return result.paths;
    } catch (error) {
      $("status").textContent = error.message;
      $("status").classList.add("err");
      return null;
    }
  }

  async function pickDir(start) {
    const paths = await pickDialog("directory", start);
    return (paths && paths[0]) || null;
  }

  async function pickFiles(start) {
    return pickDialog("files", start);
  }

  async function pickProjectFile(start) {
    const paths = await pickDialog("project", start);
    return (paths && paths[0]) || null;
  }

  async function addDocs(paths) {
    if (!paths || !paths.length) return;
    await api("/api/documents/add", { paths });
    refresh();
  }

  async function rename(project) {
    const name = await new Promise((resolve) => {
      openModal("Rename project", `<input id="rename" value="">`, () => resolve($("rename").value));
      $("rename").value = project.name;
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (!name) return;
    await api("/api/project/rename", { name });
    refresh();
  }

  async function editMeta(project) {
    const meta = project.metadata || {};
    const extra = { ...meta };
    delete extra.description;
    delete extra.tags;
    delete extra.instructions;
    openModal(
      "Project meta",
      `<label>Description <input id="m-desc"></label>
       <label>Tags <input id="m-tags"></label>
       <label>LLM instructions <textarea id="m-inst"></textarea></label>
       <label>Additional JSON <textarea id="m-extra"></textarea></label>`,
      async () => {
        let more = {};
        const raw = $("m-extra").value.trim();
        if (raw) more = JSON.parse(raw);
        const tags = $("m-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
        await api("/api/project/meta", {
          metadata: {
            ...more,
            description: $("m-desc").value.trim(),
            tags,
            instructions: $("m-inst").value.trim(),
          },
        });
        refresh();
      },
    );
    $("m-desc").value = meta.description || "";
    $("m-tags").value = Array.isArray(meta.tags) ? meta.tags.join(", ") : meta.tags || "";
    $("m-inst").value = meta.instructions || "";
    $("m-extra").value = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
  }

  function runFind(backward) {
    const text = $("preview").dataset.raw || "";
    const query = $("find-q").value;
    if (query !== state.findQuery) {
      state.findQuery = query;
      state.findFrom = 0;
    }
    const hit = findWrap(text, query, state.findFrom, backward);
    if (!hit) return;
    state.findFrom = backward ? hit.start : hit.end;
    const node = $("preview");
    const range = document.createRange();
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let seen = 0;
    let startNode = null;
    let endNode = null;
    let startOffset = 0;
    let endOffset = 0;
    while (walker.nextNode()) {
      const piece = walker.currentNode;
      const next = seen + piece.textContent.length;
      if (!startNode && hit.start >= seen && hit.start <= next) {
        startNode = piece;
        startOffset = hit.start - seen;
      }
      if (!endNode && hit.end >= seen && hit.end <= next) {
        endNode = piece;
        endOffset = hit.end - seen;
      }
      seen = next;
    }
    if (!startNode || !endNode) return;
    const selection = window.getSelection();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  document.addEventListener("selectionchange", () => syncEntityEdit());
  document.addEventListener("click", (event) => {
    const box = $("menu");
    if (box && !box.contains(event.target)) box.remove();
    const pop = $("settings-pop");
    if (pop && !pop.hidden && !pop.contains(event.target) && event.target !== $("settings")) {
      pop.hidden = true;
    }
  });

  $("modal-ok").onclick = () => {
    const action = modalCallback;
    const cancel = $("modal-cancel").onclick;
    closeModal();
    $("modal-ok").textContent = "OK";
    if (cancel) $("modal-cancel").onclick = closeModal;
    if (action) Promise.resolve(action()).catch((error) => {
      $("status").textContent = error.message;
      $("status").classList.add("err");
    });
  };
  $("modal-cancel").onclick = closeModal;

  function projectsDir() {
    return (state.data && state.data.directories && state.data.directories.projects) || "";
  }

  function sourcesDir() {
    return (state.data && state.data.directories && state.data.directories.sources) || "";
  }

  function activeProject() {
    return ((state.data && state.data.projects) || []).find((project) => project.active) || null;
  }

  async function newProject() {
    const parent = await pickDir(projectsDir());
    if (!parent) return;
    const name = await new Promise((resolve) => {
      openModal("New project", `<input id="new-name" value="Untitled project">`, () =>
        resolve($("new-name").value),
      );
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (!name) return;
    await api("/api/project/new", { name, parent, language: $("lang").value });
    refresh();
  }

  async function openProject(path) {
    if (!path) path = await pickProjectFile(projectsDir());
    if (!path) return;
    await api("/api/project/open", { path });
    refresh();
  }

  async function saveProject() {
    await api("/api/project/save");
    refresh();
  }

  async function saveProjectAs() {
    const parent = await pickDir(projectsDir());
    if (!parent) return;
    await api("/api/project/save-as", { parent });
    refresh();
  }

  async function removeDocument(path) {
    const active = activeProject();
    if (!path && active) {
      const selected = (active.documents || []).find((doc) => doc.selected);
      path = selected && selected.path;
    }
    if (!path) return;
    await api("/api/documents/remove", { path });
    refresh();
  }

  async function viewOriginal(doc, version) {
    await api("/api/documents/select", { path: doc.path, version: version || null });
    await api("/api/preview", { original: true });
    refresh();
  }

  async function copyPath(path) {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      $("status").textContent = `Copied path ${path}.`;
      $("status").classList.remove("err");
    } catch (error) {
      openModal("Path", `<input id="copied-path" readonly>`, () => {});
      $("copied-path").value = path;
      $("copied-path").select();
    }
  }

  async function copyOutput(version) {
    const parent = await pickDir();
    if (!parent) return;
    await api("/api/version/copy", { version, parent });
    refresh();
  }

  async function showKeys() {
    const payload = await api("/api/keys", {});
    const edited = await new Promise((resolve) => {
      openModal("Keys", `<textarea id="keys-yaml" rows="16"></textarea>`, () =>
        resolve($("keys-yaml").value),
      );
      const field = $("keys-yaml");
      field.value = payload.yaml || "";
      field.readOnly = Boolean(payload.read_only);
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (edited == null || payload.read_only || edited === (payload.yaml || "")) return;
    await api("/api/keys", { yaml: edited });
    refresh();
  }

  function fileMenu(event) {
    const recent = (state.data && state.data.projects) || [];
    const items = [
      ["New Project", newProject],
      ["Open Project…", () => openProject()],
    ];
    if (recent.length) {
      for (const project of recent) items.push([`Recent: ${project.label || project.name}`, () => openProject(project.path)]);
      items.push(["Clear Recent Projects", () => api("/api/project/clear-recent").then(refresh)]);
    } else {
      items.push(["No recent projects", null]);
    }
    items.push(
      ["Save Project", saveProject],
      ["Save Project As…", saveProjectAs],
      ["Rename Project…", () => { const project = activeProject(); if (project) rename(project); }],
      ["Project Meta…", () => { const project = activeProject(); if (project) editMeta(project); }],
      ["Add Documents…", () => pickFiles(sourcesDir()).then((paths) => paths && addDocs(paths))],
      ["Remove Selected Document", () => removeDocument()],
    );
    menu(event, items);
  }

  function handoffMenu(event) {
    const ho = (state.data && state.data.handoff) || {};
    const evid = ho.set
      ? `Send to evid · ${ho.set}${ho.pending ? ` (${ho.pending})` : ""}`
      : "Send to evid…";
    menu(event, [
      ["Copy this document", () => copyLlm("current", "md")],
      ["Copy this document as JSON", () => copyLlm("current", "json")],
      ["Copy every document", () => copyLlm("all", "md")],
      ["Copy every document as JSON", () => copyLlm("all", "json")],
      ["Save a ZIP…", saveZip],
      [evid, () => sendToEvid()],
    ]);
  }

  async function sendToEvid() {
    const ho = (state.data && state.data.handoff) || {};
    let set = ho.set || "";
    if (!set) {
      set = (window.prompt("Hand off to which evid set? (the case's anon set, e.g. 2026-014-anon)", "") || "").trim();
      if (!set) return;
    }
    try {
      await api("/api/handoff", { set });
    } catch (error) {
      $("status").textContent = error.message;
    }
    refresh();
  }

  function helpMenu(event) {
    menu(event, [
      ["Documentation", () => api("/api/open-url", { url: "https://github.com/evidlabel/did/tree/main/docs" })],
      ["DID on GitHub", () => api("/api/open-url", { url: "https://github.com/evidlabel/did" })],
      ["MIT License", () => api("/api/open-url", { url: "https://github.com/evidlabel/did/blob/main/LICENSE" })],
      ["About DID", aboutDid],
    ]);
  }

  function aboutDid() {
    const version = (state.data && state.data.version) || "";
    openModal("About DID", `<p></p>`, () => {});
    $("modal-body").querySelector("p").textContent =
      `DID Pseudonymizer ${version}\n\nLocal-first document pseudonymization and reviewed LLM handoffs.\n\nReleased under the MIT License.\nhttps://github.com/evidlabel/did`;
    $("modal-cancel").hidden = true;
  }

  function typingTarget(event) {
    const el = event.target;
    return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  }

  $("side-new").onclick = () => newProject();
  $("side-open").onclick = () => openProject();
  $("menu-file").onclick = (event) => fileMenu(event);
  $("menu-help").onclick = (event) => helpMenu(event);
  $("scan").onclick = () => api("/api/detect").then(refresh);
  $("view-mode").onchange = () => {
    const body = previewModeBody($("view-mode").value);
    const data = state.data || {};
    const hasReview = (data.entities || []).some((row) => row.id);
    const ready = Boolean(body.original || data.scanned || data.read_only || hasReview);
    const posted = api("/api/preview", body);
    (ready ? posted : posted.then(() => api("/api/detect"))).then(refresh);
  };
  $("settings").onclick = (event) => {
    event.stopPropagation();
    const pop = $("settings-pop");
    pop.hidden = !pop.hidden;
    if (!pop.hidden) {
      const rect = event.currentTarget.getBoundingClientRect();
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - 240));
      pop.style.left = left + "px";
      pop.style.top = rect.bottom + 4 + "px";
    }
  };
  $("ask-agent").onclick = () => {
    reviewByAgent().catch((error) => termStatus(error.message, true));
  };
  $("scan-again").onclick = () => {
    $("settings-pop").hidden = true;
    api("/api/detect").then(refresh);
  };
  $("review-yaml").onclick = () => {
    $("settings-pop").hidden = true;
    showKeys();
  };
  $("add").onclick = async () => {
    const paths = await pickFiles(sourcesDir());
    if (paths) addDocs(paths);
  };
  $("lang").onchange = () => api("/api/settings", { language: $("lang").value }).then(refresh);
  $("profile").onchange = () => api("/api/settings", { profile: $("profile").value }).then(refresh);
  $("save-mode").onchange = () => {
    if (state.data) render(state.data);
  };
  $("manual-edits").checked = termLoadFlag("did-manual-edits", "0") === "1";
  $("manual-edits").onchange = () => {
    termStore("did-manual-edits", $("manual-edits").checked ? "1" : "0");
    syncEntityEdit();
  };
  $("save").onclick = async () => {
    await api("/api/save", { mode: $("save-mode").value });
    refresh();
  };
  $("handoff").onclick = (event) => handoffMenu(event);
  $("find").onclick = () => {
    $("findbar").hidden = false;
    $("find-q").focus();
  };
  $("find-next").onclick = () => runFind(false);
  $("find-prev").onclick = () => runFind(true);
  $("find-q").onkeydown = (event) => {
    if (event.key === "Enter") runFind(event.shiftKey);
    if (event.key === "Escape") $("findbar").hidden = true;
  };
  $("entity-search").oninput = () => state.data && renderEntities(state.data);
  $("entity-type").onchange = () => state.data && renderEntities(state.data);
  $("entity-edit").onclick = (event) => editMenu(event);
  $("preview").oncontextmenu = (event) => {
    const text = selectedPreviewText();
    if (!text) return;
    event.preventDefault();
    menu(event, [["Annotate", () => annotateSelection().catch((error) => {
      $("status").textContent = error.message;
      $("status").classList.add("err");
    })]]);
  };
  $("anno-send").onclick = () => {
    sendAnnotationQueue().catch((error) => termStatus(error.message, true));
  };

  async function changeType(type) {
    const ids = selectedRows().map((row) => row.dataset.id).filter(Boolean);
    await api("/api/entities/type", { ids, type });
    refresh();
  }

  async function addVariant() {
    const row = selectedRows()[0];
    if (!row) return;
    const variant = await new Promise((resolve) => {
      openModal("Add variant", `<input id="variant">`, () => resolve($("variant").value));
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (!variant) return;
    await api("/api/entities/variant", {
      entity_type: row.dataset.type,
      id: row.dataset.id,
      variant,
      position: Number(row.dataset.number),
    });
    refresh();
  }

  async function mergeRows() {
    const rows = selectedRows().filter((row) => row.dataset.type === "PERSON" && !row.dataset.variant);
    if (rows.length !== 2) {
      $("status").textContent = "Select two person identities to merge.";
      return;
    }
    await api("/api/entities/merge", { source: rows[0].dataset.id, target: rows[1].dataset.id });
    refresh();
  }

  async function markNotName() {
    const ids = selectedRows().filter((row) => row.dataset.type === "PERSON").map((row) => row.dataset.id);
    await api("/api/entities/not-name", { ids });
    refresh();
  }

  async function addFromSelection() {
    const text = String(window.getSelection() || "");
    await api("/api/entities/from-selection", { text, type: "PERSON" });
    refresh();
  }

  function selectedPreviewText() {
    if (!selectionInPreview()) return "";
    return String(window.getSelection() || "").replace(/\s+/g, " ").trim();
  }

  function previewIsSource() {
    const preview = state.data && state.data.preview;
    return Boolean(
      preview && (preview.original || preview.state === "RAW" || preview.state === "UNANON")
    );
  }

  async function annotateSelection() {
    const text = selectedPreviewText();
    if (!text) {
      $("status").textContent = "Select some text, then right-click Annotate.";
      $("status").classList.add("err");
      return;
    }
    const preview = state.data && state.data.preview;
    await api("/api/annotations", {
      text,
      document: (preview && preview.name) || "",
      source: previewIsSource(),
    });
    refresh();
  }

  async function dropAnnotation(id) {
    await api("/api/annotations/remove", { id });
    refresh();
  }

  async function sendAnnotationQueue() {
    const items = (state.data && state.data.annotations) || [];
    let yaml = "";
    try {
      const keys = await api("/api/keys", {});
      yaml = keys.yaml || "";
    } catch (error) {
      termStatus(error.message, true);
      return;
    }
    const text = annotationQueue(yaml, items);
    if (!text) {
      termStatus("Nothing is queued. Select text and right-click Annotate.", true);
      return;
    }
    const sent = await sendToAgent(text, true);
    if (sent) termStatus("Sent the annotation queue to the local model.", false);
  }

  async function copyLlm(scope, form) {
    const payload = await api("/api/llm", { scope, form });
    if (payload && payload.text && navigator.clipboard) {
      await navigator.clipboard.writeText(payload.text);
    }
    refresh();
  }

  async function saveZip() {
    const name = (state.data && state.data.project_name) || "did";
    const dest = await new Promise((resolve) => {
      openModal(
        "Save safe ZIP",
        `<input id="zip-dest">`,
        () => resolve($("zip-dest").value),
      );
      $("zip-dest").value = `${name}-llm-session.zip`;
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (!dest) return;
    await api("/api/llm/zip", { dest });
    refresh();
  }

  document.addEventListener("keydown", (event) => {
    const key = event.key.toLowerCase();
    const mod = event.ctrlKey || event.metaKey;
    if (window.parent !== window && event.ctrlKey && !event.shiftKey && !event.altKey && !event.metaKey && (event.key === "PageUp" || event.key === "PageDown")) {
      event.preventDefault();  // the workbench cycles its tabs
      window.parent.postMessage({ type: "workbench-tab", step: event.key === "PageDown" ? 1 : -1 }, "*");
      return;
    }
    if (event.target.closest && event.target.closest("#termpane")) {
      if (mod && key === "q" && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        if (window.parent !== window) window.parent.postMessage({ type: "workbench-quit" }, "*");
        else api("/api/quit").catch(() => {});
      }
      return;
    }
    if (mod && key === "f") {
      event.preventDefault();
      $("findbar").hidden = false;
      $("find-q").focus();
      return;
    }
    if (mod && !event.shiftKey && !event.altKey && (key === "q" || (key === "w" && window.parent !== window))) {
      event.preventDefault();
      if (window.parent !== window) window.parent.postMessage({ type: "workbench-quit" }, "*");
      else api("/api/quit").catch(() => {});
      return;
    }
    if (mod && event.shiftKey && key === "o") {
      event.preventDefault();
      openProject();
      return;
    }
    if (mod && event.shiftKey && key === "s") {
      event.preventDefault();
      saveProjectAs();
      return;
    }
    if (mod && key === "s") {
      event.preventDefault();
      saveProject();
      return;
    }
    if (typingTarget(event)) return;
    if (mod && key === "n") {
      event.preventDefault();
      newProject();
    } else if (mod && key === "o") {
      event.preventDefault();
      pickFiles(sourcesDir()).then((paths) => paths && addDocs(paths));
    } else if (event.key === "F2") {
      const project = activeProject();
      if (project) rename(project);
    } else if (event.key === "F1") {
      api("/api/open-url", { url: "https://github.com/evidlabel/did/tree/main/docs" });
    } else if (event.key === "Delete") {
      removeDocument();
    }
  });

  // Agent pane. Terminals belong to the open project's folder and keep running
  // when the pane is hidden or another project is selected.
  const T = {
    open: false,
    list: [],
    active: null,
    workdir: "",
    token: "",
    agent: "",
    presets: [],
    external: "",
    started: new Set(),
    starting: new Set(),
    gen: 0,
  };
  const LIGHT_ANSI = {
    black: "#2e2b26",
    red: "#b23b2e",
    green: "#2f7d4f",
    yellow: "#946400",
    blue: "#3f6fb5",
    magenta: "#8a3ab9",
    cyan: "#0f7b85",
    white: "#8a857b",
    brightBlack: "#6b665d",
    brightRed: "#cf4436",
    brightGreen: "#38945c",
    brightYellow: "#b07a00",
    brightBlue: "#4f82cc",
    brightMagenta: "#a050d0",
    brightCyan: "#14909e",
    brightWhite: "#a9a397",
  };

  function termStore(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      /* private mode */
    }
  }

  function termLoadFlag(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  function termStatus(message, isError) {
    $("status").textContent = message;
    $("status").classList.toggle("err", Boolean(isError));
  }

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (name === "class") node.className = value;
      else if (name === "text") node.textContent = value;
      else if (name.startsWith("on") && typeof value === "function") {
        node.addEventListener(name.slice(2), value);
      } else if (value != null) node.setAttribute(name, value);
    }
    for (const kid of kids) if (kid) node.append(kid);
    return node;
  }

  function darkMode() {
    const forced = document.documentElement.dataset.theme;
    if (forced) return forced === "dark";
    return matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function termTheme() {
    const style = getComputedStyle(document.documentElement);
    const value = (name) => style.getPropertyValue(name).trim();
    return {
      background: value("--bg"),
      foreground: value("--fg"),
      cursor: value("--accent"),
      cursorAccent: value("--bg"),
      selectionBackground: darkMode() ? "rgba(142,197,214,.35)" : "rgba(28,74,92,.25)",
      ...(darkMode() ? {} : LIGHT_ANSI),
    };
  }

  function selectedCommand() {
    const preset = $("t-preset");
    if (preset && preset.dataset.ready) return preset.value;
    return T.agent || "";
  }

  function fillPresets() {
    const preset = $("t-preset");
    if (!preset || preset.dataset.ready) return;
    const agent = T.agent || "";
    const presets = T.presets || [];
    const known = new Set(presets.map((item) => item.command));
    if (agent && !known.has(agent)) {
      preset.append(el("option", { value: agent, text: agent }));
    }
    for (const item of presets) {
      preset.append(el("option", { value: item.command, text: item.label }));
    }
    preset.append(el("option", { value: "", text: "Shell" }));
    const match = [...preset.options].some((option) => option.value === agent);
    preset.value = match ? agent : presets[0] ? presets[0].command : "";
    preset.dataset.ready = "1";
  }

  function startLabel() {
    const command = selectedCommand();
    if (!command) return "Start a shell";
    const preset = (T.presets || []).find((item) => item.command === command);
    return `Start ${preset ? preset.label : command}`;
  }

  function termMake(info) {
    const pane = {
      id: info.id,
      agent: info.agent,
      ws: null,
      retry: 0,
      queue: [],
      closed: false,
    };
    pane.dot = el("span", { class: "tdot" });
    pane.lab = el("b", { text: info.label });
    pane.body = el("div", { class: "tbody" });
    const restart = el("button", {
      type: "button",
      text: "↻",
      title: "End this session and start a new one",
      onclick: async () => {
        const label = pane.lab.textContent;
        if (await confirmBox(`End "${label}" and start it again?`)) {
          termSend(pane, { t: "restart" });
        }
      },
    });
    const close = el("button", {
      type: "button",
      text: "×",
      title: "Close this terminal (ends its session)",
      onclick: () => termClose(pane),
    });
    const head = el("div", { class: "thead" }, pane.dot, pane.lab, restart, close);
    head.addEventListener("click", (event) => {
      if (event.target === head || event.target === pane.lab) {
        termActive(pane);
        pane.term.focus();
      }
    });
    pane.el = el("div", { class: "tsess" }, head, pane.body);
    pane.el.addEventListener("focusin", () => termActive(pane));
    pane.el.addEventListener("mousedown", () => termActive(pane));
    pane.term = new Terminal({
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--mono"),
      fontSize: 13,
      lineHeight: 1.15,
      cursorBlink: true,
      scrollback: 5000,
      theme: termTheme(),
    });
    pane.fit = new FitAddon.FitAddon();
    pane.term.loadAddon(pane.fit);
    pane.term.onData((data) => termSend(pane, { t: "in", d: data }));
    pane.term.attachCustomKeyEventHandler((event) => {
      if (event.ctrlKey && !event.shiftKey && !event.altKey && event.code === "KeyQ") return false;
      if (window.parent !== window && event.ctrlKey && !event.shiftKey && !event.altKey && (event.key === "PageUp" || event.key === "PageDown")) return false;  // tab cycling, above
      if (event.type !== "keydown" || !event.ctrlKey || !event.shiftKey) return true;
      if (event.code === "KeyC") {
        const text = pane.term.getSelection();
        if (text) navigator.clipboard?.writeText(text).catch(() => {});
        return false;
      }
      if (event.code === "KeyV") {
        navigator.clipboard?.readText().then((text) => pane.term.paste(text)).catch(() => {
          termStatus("Paste blocked — try middle-click", true);
        });
        return false;
      }
      return true;
    });
    new ResizeObserver(() => termFit(pane)).observe(pane.body);
    return pane;
  }

  function termSync(infos) {
    const ids = new Set(infos.map((info) => info.id));
    for (const pane of T.list.filter((item) => !ids.has(item.id))) termDrop(pane);
    for (const info of infos) {
      if (T.list.some((item) => item.id === info.id)) continue;
      if (typeof Terminal !== "function" || typeof FitAddon === "undefined") continue;
      const pane = termMake(info);
      T.list.push(pane);
      $("terms").append(pane.el);
      pane.term.open(pane.body);
      if (T.open) termConnect(pane);
    }
    if (!T.list.includes(T.active)) termActive(T.list[0] || null);
    requestAnimationFrame(() => T.list.forEach(termFit));
  }

  function termDrop(pane) {
    pane.closed = true;
    T.list = T.list.filter((item) => item !== pane);
    try {
      pane.ws?.close();
    } catch (error) {
      /* already gone */
    }
    pane.term.dispose();
    pane.el.remove();
  }

  function termActive(pane) {
    T.active = pane;
    for (const item of T.list) {
      item.el.classList.toggle("active", item === pane && T.list.length > 1);
    }
  }

  function termFit(pane) {
    if (!T.open || pane.closed) return;
    try {
      pane.fit.fit();
    } catch (error) {
      return;
    }
    termSend(pane, { t: "resize", rows: pane.term.rows, cols: pane.term.cols });
  }

  function termSend(pane, message) {
    if (pane && pane.ws && pane.ws.readyState === 1) {
      pane.ws.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  function termState(pane, name) {
    pane.dot.className = "tdot " + name;
    pane.dot.title = { live: "connected", ended: "session ended", off: "disconnected" }[name] || "";
  }

  function termConnect(pane) {
    if (pane.closed || (pane.ws && pane.ws.readyState <= 1)) return;
    const query = new URLSearchParams({ token: T.token, id: pane.id });
    const socket = new WebSocket(`ws://${location.host}/api/term?${query}`);
    socket.binaryType = "arraybuffer";
    pane.ws = socket;
    socket.onopen = () => {
      pane.retry = 0;
      termState(pane, "live");
      termFit(pane);
      const queued = pane.queue;
      pane.queue = [];
      queued.forEach((run) => run());
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") {
        pane.term.write(new Uint8Array(event.data));
        return;
      }
      const message = JSON.parse(event.data);
      if (message.t === "hello") {
        pane.term.reset();
        pane.lab.textContent = message.label;
      } else if (message.t === "reset") {
        pane.term.reset();
        termState(pane, "live");
      } else if (message.t === "exit") termState(pane, "ended");
      else if (message.t === "closed") termLoad();
    };
    socket.onclose = () => {
      if (pane.ws !== socket) return;
      pane.ws = null;
      termState(pane, "off");
      if (T.open && !pane.closed) {
        setTimeout(() => termConnect(pane), Math.min(5000, 400 * 2 ** pane.retry++));
      }
    };
  }

  async function apiGet(path) {
    const response = await fetch(path);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }

  function termProjectChanged() {
    for (const pane of T.list.slice()) termDrop(pane);
    T.workdir = "";
    T.active = null;
    if (T.open) termLoad();
  }

  async function termLoad() {
    const gen = ++T.gen;
    let info;
    try {
      info = await apiGet("/api/terms");
    } catch (error) {
      termStatus(error.message, true);
      return;
    }
    if (gen !== T.gen) return;
    T.token = info.token || "";
    T.agent = info.agent || "";
    T.presets = info.presets || [];
    T.external = info.external || "";
    fillPresets();
    const external = $("t-ext");
    external.hidden = !T.external;
    external.textContent = T.external === "ptyxis" ? "Ptyxis" : "Terminal";
    const name = (state.data && state.data.project_name) || "";
    $("t-title").textContent = name ? `Agent · ${name}` : "Agent";
    const dir = info.workdir || "";
    if (dir !== T.workdir) {
      for (const pane of T.list.slice()) termDrop(pane);
      T.workdir = dir;
      T.active = null;
    }
    const infos = info.terms || [];
    let start = false;
    if (T.open && dir && !infos.length && !T.started.has(dir) && !T.starting.has(dir)) {
      T.starting.add(dir);
      start = true;
    }
    termSync(infos);
    termEmpty(info);
    if (!start) return;
    try {
      await termNew(selectedCommand());
      T.started.add(dir);
    } catch (error) {
      termStatus(error.message, true);
    } finally {
      T.starting.delete(dir);
    }
  }

  function termEmpty(info) {
    const empty = !T.list.length;
    $("tempty").hidden = !empty;
    if (!empty) return;
    const blocked = !info || info.needs_project || !info.workdir;
    $("tempty-msg").textContent = blocked
      ? "Open a project first. The agent starts in that project's folder."
      : "No agent in this project yet. It runs in the project folder. Selection types the pseudonymized preview only. Original text stays on this machine.";
    $("t-start").hidden = blocked;
    $("t-agent").hidden = blocked;
    $("t-start").textContent = startLabel();
  }

  async function termNew(command) {
    const payload = await api("/api/term/new", { agent: command });
    termSync(payload.terms || []);
    termEmpty({ needs_project: false, workdir: T.workdir });
    const pane = T.list.find((item) => item.id === payload.id);
    if (pane) {
      termActive(pane);
      requestAnimationFrame(() => pane.term.focus());
    }
  }

  async function termAdd() {
    if (!state.data || !state.data.workdir) {
      termStatus("Open a project first.", true);
      return;
    }
    const command = await new Promise((resolve) => {
      openModal(
        "Add an agent",
        `<input id="agent-cmd" placeholder="ocprofile launch local-qw38">`,
        () => resolve($("agent-cmd").value),
      );
      $("agent-cmd").value = selectedCommand();
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
    });
    if (command === null) return;
    try {
      await termNew(command);
    } catch (error) {
      termStatus(error.message, true);
    }
  }

  async function termClose(pane) {
    const label = pane.lab.textContent;
    if (!(await confirmBox(`Close "${label}"? Its session ends.`))) return;
    try {
      const payload = await api("/api/term/close", { id: pane.id });
      termSync(payload.terms || []);
      termEmpty({ needs_project: !T.workdir, workdir: T.workdir });
    } catch (error) {
      termStatus(error.message, true);
    }
  }

  async function openTerm(open) {
    T.open = open;
    termStore("did-term", open ? "1" : "0");
    $("termpane").hidden = $("divider2").hidden = !open;
    $("b-term").classList.toggle("on", open);
    if (!open) return;
    await termLoad();
    T.list.forEach(termConnect);
    requestAnimationFrame(() => {
      T.list.forEach(termFit);
      T.active?.term.focus();
    });
  }

  function previewSelection() {
    const preview = state.data && state.data.preview;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && selection.rangeCount) {
      const anchor = selection.anchorNode;
      const box = $("preview");
      const node = anchor && anchor.nodeType === 1 ? anchor : anchor && anchor.parentNode;
      if (node && box && box.contains(node)) return agentPaste(preview, selection.toString());
    }
    return agentPaste(preview, "");
  }

  async function sendToAgent(text, enter) {
    if (!T.open) await openTerm(true);
    const pane = T.active;
    if (!pane) {
      termStatus("No agent in this project yet — start one in the Agent pane.", true);
      return false;
    }
    const payload = enter ? agentSubmit(text) : null;
    const go = () => {
      termSend(pane, { t: "in", d: payload ? payload.paste : text });
      if (payload) setTimeout(() => termSend(pane, { t: "in", d: "\r" }), payload.delay);
      pane.term.focus();
    };
    if (!termSend(pane, { t: "resize", rows: pane.term.rows, cols: pane.term.cols })) {
      pane.queue.push(go);
    } else go();
    return true;
  }

  async function reviewByAgent() {
    let payload;
    try {
      payload = await api("/api/review-bundle", {});
    } catch (error) {
      termStatus(error.message, true);
      return;
    }
    const text = fullCheck(payload.yaml || "", payload.documents || [], payload.withheld);
    if (!text) {
      termStatus("Nothing to check yet.", true);
      return;
    }
    const sent = await sendToAgent(text, true);
    if (sent) termStatus("Sent the keyword YAML and the pseudonymized text to the local agent.", false);
  }

  function termBoot() {
    if (typeof Terminal !== "function") {
      $("tempty").hidden = false;
      $("tempty-msg").textContent = "The terminal library did not load.";
      $("t-start").hidden = true;
      $("t-agent").hidden = true;
    }
    $("b-term").onclick = () => openTerm(!T.open);
    $("t-close").onclick = () => openTerm(false);
    $("t-add").onclick = () => termAdd();
    $("t-agent").onclick = () => termAdd();
    $("t-start").onclick = () => termNew(selectedCommand()).catch((error) => termStatus(error.message, true));
    $("t-preset").onchange = () => {
      $("t-start").textContent = startLabel();
    };
    $("t-sel").onclick = () => {
      const preview = state.data && state.data.preview;
      if (preview && (preview.original || preview.state === "RAW" || preview.state === "UNANON")) {
        termStatus("Original text stays on this machine. Show the pseudonymized preview, then send that.", true);
        return;
      }
      const text = previewSelection();
      if (!text) {
        termStatus("Nothing pseudonymized to send yet.", true);
        return;
      }
      sendToAgent(text);
    };
    $("t-ext").onclick = async () => {
      try {
        await api("/api/term/external", {});
        termStatus("Opened in a new terminal window.", false);
      } catch (error) {
        termStatus(error.message, true);
      }
    };
    const saved = termLoadFlag("did-term-w", "");
    if (saved) document.documentElement.style.setProperty("--term-w", `${saved}px`);
    $("divider2").onmousedown = (event) => {
      event.preventDefault();
      const startX = event.clientX;
      const startW = $("termpane").offsetWidth;
      document.body.style.userSelect = "none";
      const move = (ev) => {
        const width = Math.max(280, startW - (ev.clientX - startX));
        document.documentElement.style.setProperty("--term-w", `${width}px`);
        termStore("did-term-w", String(width));
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        document.body.style.userSelect = "";
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    };
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      for (const pane of T.list) pane.term.options.theme = termTheme();
    });
    if (termLoadFlag("did-term", "0") === "1") openTerm(true);
  }

  setInterval(() => {
    if (state.data && state.data.busy) refresh().catch(() => {});
  }, 400);
  if (!EMBED) termBoot();  // embedded in the workbench: no agent pane (the Write tab owns terminals)
  (async () => {
    const project = params.get("project");
    if (project) {
      await api("/api/project/ensure", {
        path: project,
        name: params.get("name") || "",
        evid_set: params.get("set") || "",
      });
    }
    await refresh();
  })().catch((error) => {
    $("status").textContent = error.message;
  });
}
