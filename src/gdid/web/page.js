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
  module.exports = { tokenSpans, tokenAt, findWrap, agentPaste, keywordReview, agentSubmit };
}

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const EMBED = params.get("embed") === "1";  // inside the workbench: one case, one project
  if (EMBED) document.body.classList.add("embed");
  function setForcedTheme(name) {  // "light" | "dark" from the workbench; else the system
    if (name === "light" || name === "dark") document.documentElement.dataset.theme = name;
    else delete document.documentElement.dataset.theme;
    try {
      for (const pane of T.list) pane.term.options.theme = termTheme();
    } catch {
      // the terminals are not set up yet (first call, at load)
    }
  }
  setForcedTheme(params.get("theme"));
  window.addEventListener("message", (event) => {
    if (event.source === window.parent && event.data && event.data.type === "workbench-theme") {
      setForcedTheme(event.data.theme);
    }
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
    $("to-evid").disabled = !data.workdir || Boolean(data.read_only);
    $("to-evid").classList.toggle("pending", ho.pending > 0);
    $("to-evid-state").textContent = ho.set
      ? `${ho.set} · ${ho.pending ? ho.pending + " to hand off" : "up to date"}`
      : "";
    if (window.parent !== window && ho.pending !== state.badge) {
      state.badge = ho.pending;  // the workbench shows it on the Anonymization tab
      window.parent.postMessage({ type: "workbench-badge", count: ho.pending }, "*");
    }
    $("keys").textContent = (data.keys && data.keys.label) || "";
    $("keys").title = (data.keys && data.keys.path) || "";
    $("keys-folder").disabled = !(data.keys && data.keys.folder);
    $("lang").value = data.language;
    $("profile").value = data.profile;
    $("format").value = data.format;
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
    }
    $("original").classList.toggle("on", data.preview && data.preview.original);
    $("original").disabled = !(data.preview && data.preview.can_original);
    $("read-only").hidden = !data.read_only;
    const agentDir = data.workdir || "";
    if (agentDir !== state.agentDir) {
      state.agentDir = agentDir;
      termProjectChanged();
    }
    const active = (data.projects || []).find((project) => project.active);
    const hasDocs = Boolean(active && (active.documents || []).length);
    $("redetect").disabled = Boolean(data.read_only) || !hasDocs;
  }

  function renderTree(data) {
    const root = $("tree");
    root.textContent = "";
    for (const project of (data.projects || []).filter((p) => !EMBED || p.active)) {
      const block = document.createElement("div");
      block.className = "project" + (project.active ? " active" : "");
      const head = document.createElement("div");
      head.className = "row";
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
        for (const doc of project.documents || []) {
          block.appendChild(docRow(doc, project));
        }
        if (project.output) {
          const out = document.createElement("div");
          out.className = "row muted";
          out.textContent = `${project.output.mark} Output  ${project.output.mode}`;
          out.oncontextmenu = (event) => {
            event.preventDefault();
            menu(event, [
              ["Copy output to…", () => copyOutput(project.output.path)],
              ["Copy path", () => copyPath(project.output.path)],
              ["Reveal folder", () => api("/api/reveal", { path: project.output.path })],
            ]);
          };
          block.appendChild(out);
          for (const file of project.output.files || []) {
            const row = document.createElement("div");
            row.className = "row child" + (file.selected ? " sel" : "");
            row.textContent = file.name;
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
        const keys = document.createElement("div");
        keys.className = "row link";
        keys.textContent = "Keys";
        keys.onclick = () => showKeys();
        keys.oncontextmenu = (event) => {
          event.preventDefault();
          const folder = (data.keys && data.keys.folder) || "";
          const path = (data.keys && data.keys.path) || "";
          menu(event, [
            ["Show keys (Advanced YAML)", () => showKeys()],
            ["Copy path", () => copyPath(path)],
            ["Reveal folder", () => api("/api/reveal", { path: folder })],
          ]);
        };
        block.appendChild(keys);
        if ((project.excluded || []).length) {
          const excluded = document.createElement("div");
          excluded.className = "row muted";
          excluded.textContent = `Excluded (${project.excluded.length})`;
          excluded.title = project.excluded.join("\n");
          block.appendChild(excluded);
        }
        const meta = document.createElement("div");
        meta.className = "row link";
        meta.textContent = "Meta";
        meta.onclick = () => editMeta(project);
        block.appendChild(meta);
      }
      root.appendChild(block);
    }
    if (!(data.projects || []).length) {
      root.textContent = "No recent projects.";
    }
  }

  function docRow(doc, project) {
    const row = document.createElement("div");
    row.className = "row child" + (doc.selected ? " sel" : "") + (doc.missing ? " missing" : "");
    row.textContent = `${doc.mark || ""} ${doc.name}  ${doc.mode}`;
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
      pre.textContent = "No document.";
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
      };
      body.appendChild(tr);
    }
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

  async function pickDir(start) {
    return pick(start, "dir");
  }

  async function pickFiles(start) {
    return pick(start, "files");
  }

  async function pickProjectFile(start) {
    return pick(start, "project");
  }

  function pick(start, mode) {
    return new Promise((resolve) => {
      const chosen = new Set();
      let projectFile = "";
      const title =
        mode === "dir" ? "Choose a folder" : mode === "project" ? "Open DID project" : "Add documents";
      openModal(title, `<div id="fs"></div>`, async () => {
        if (mode === "dir") resolve($("fs-path").dataset.path);
        else if (mode === "project") resolve(projectFile || null);
        else resolve([...chosen]);
      });
      $("modal-cancel").onclick = () => {
        closeModal();
        resolve(null);
      };
      const box = $("fs");
      async function load(path) {
        const query = path ? `?path=${encodeURIComponent(path)}` : "";
        const listing = await (await fetch("/api/fs" + query)).json();
        box.textContent = "";
        const label = document.createElement("div");
        label.id = "fs-path";
        label.dataset.path = listing.path;
        label.textContent = listing.path;
        box.appendChild(label);
        if (listing.parent) {
          const up = document.createElement("button");
          up.textContent = "Up";
          up.onclick = () => load(listing.parent);
          box.appendChild(up);
        }
        for (const entry of listing.entries) {
          const row = document.createElement("button");
          row.textContent = (entry.dir ? "📁 " : "📄 ") + entry.name;
          const projectName = /\.(yaml|yml)$/i.test(entry.name);
          row.onclick = () => {
            if (entry.dir) {
              load(entry.path);
              return;
            }
            if (mode === "project" && projectName) {
              projectFile = entry.path;
              for (const button of box.querySelectorAll("button")) button.classList.remove("on");
              row.classList.add("on");
              return;
            }
            if (mode === "files") {
              if (chosen.has(entry.path)) chosen.delete(entry.path);
              else chosen.add(entry.path);
              row.classList.toggle("on", chosen.has(entry.path));
            }
          };
          box.appendChild(row);
        }
      }
      load(start || "");
    });
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

  document.addEventListener("click", (event) => {
    const box = $("menu");
    if (box && !box.contains(event.target)) box.remove();
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
  $("redetect").onclick = () => api("/api/detect").then(refresh);
  $("keys-folder").onclick = () => {
    const folder = state.data && state.data.keys && state.data.keys.folder;
    if (folder) api("/api/reveal", { path: folder });
  };
  $("add").onclick = async () => {
    const paths = await pickFiles(sourcesDir());
    if (paths) addDocs(paths);
  };
  $("lang").onchange = () => api("/api/settings", { language: $("lang").value }).then(refresh);
  $("profile").onchange = () => api("/api/settings", { profile: $("profile").value }).then(refresh);
  $("format").onchange = () => api("/api/preview", { format: $("format").value }).then(refresh);
  $("original").onclick = () => {
    const on = !(state.data && state.data.preview && state.data.preview.original);
    api("/api/preview", { original: on }).then(refresh);
  };
  $("save").onclick = async () => {
    await api("/api/save", { mode: $("save-mode").value });
    refresh();
  };
  $("handoff").onclick = async (event) => {
    event.stopPropagation();
    menu(event, [
      ["Copy current Markdown", () => copyLlm("current", "md")],
      ["Copy current JSON", () => copyLlm("current", "json")],
      ["Copy all Markdown", () => copyLlm("all", "md")],
      ["Copy all JSON", () => copyLlm("all", "json")],
      ["Save safe ZIP…", saveZip],
    ]);
  };
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
  $("type-change").onclick = async () => {
    const ids = selectedRows().map((row) => row.dataset.id).filter(Boolean);
    const type = $("type-target").value;
    await api("/api/entities/type", { ids, type });
    refresh();
  };
  $("add-variant").onclick = async () => {
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
  };
  $("merge").onclick = async () => {
    const rows = selectedRows().filter((row) => row.dataset.type === "PERSON" && !row.dataset.variant);
    if (rows.length !== 2) {
      $("status").textContent = "Select two person identities to merge.";
      return;
    }
    await api("/api/entities/merge", { source: rows[0].dataset.id, target: rows[1].dataset.id });
    refresh();
  };
  $("not-name").onclick = async () => {
    const ids = selectedRows().filter((row) => row.dataset.type === "PERSON").map((row) => row.dataset.id);
    await api("/api/entities/not-name", { ids });
    refresh();
  };
  $("from-selection").onclick = async () => {
    const text = String(window.getSelection() || "");
    await api("/api/entities/from-selection", { text, type: "PERSON" });
    refresh();
  };

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
    if (event.target.closest && event.target.closest("#termpane")) {
      if (mod && key === "q" && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        api("/api/quit").catch(() => {});
      }
      return;
    }
    if (mod && key === "f") {
      event.preventDefault();
      $("findbar").hidden = false;
      $("find-q").focus();
      return;
    }
    if (mod && key === "q") {
      event.preventDefault();
      api("/api/quit").catch(() => {});
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

  async function checkPseudonymization() {
    let payload;
    try {
      payload = await api("/api/keys", {});
    } catch (error) {
      termStatus(error.message, true);
      return;
    }
    const text = keywordReview(payload.yaml || "");
    if (!text) {
      termStatus("There are no keywords to review yet.", true);
      return;
    }
    const sent = await sendToAgent(text, true);
    if (sent) termStatus("Sent the keyword YAML to the agent.", false);
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
    $("check-pseudo").onclick = () => {
      checkPseudonymization().catch((error) => termStatus(error.message, true));
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
    if (termLoadFlag("did-term", "1") !== "0") openTerm(true);
  }

  setInterval(() => {
    if (state.data && state.data.busy) refresh().catch(() => {});
  }, 400);
  $("to-evid").onclick = async () => {
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
  };
  termBoot();
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
