"""HTTP tests for the loopback GUI. Detection is a scripted stand-in."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from gdid import pipeline
from gdid.web.server import (
    DEFAULT_AGENT,
    DidApp,
    Handler,
    ProjectAgents,
    Server,
    ask_raise,
    directory_dialog_command,
    external_terminal,
    file_dialog_command,
    lock_path,
    pick_directory,
    pick_paths,
    resolve_agent,
    running_instance,
    serve_gui,
    terminal_env,
)
from gdid.web.session import ApiError, Session
from gdid.web.settings import GuiSettings
from gdid.web.term import agent_name


class Scripted:
    """Anonymizer stand-in. ``leak`` leaves the source text in the output."""

    seen: list[tuple] = []
    leak = False
    gate = None

    batches: list[int] = []

    def __init__(self, language="da", detection_profile=None):
        self.language = language
        self.detection_profile = detection_profile
        self.texts: list[str] = []

    def detect_entities(self, texts):
        # Construction is not a scan. Only this call is Presidio.
        Scripted.seen.append((self.language, self.detection_profile))
        Scripted.batches.append(len(list(texts)))
        self.texts = list(texts)
        gate = Scripted.gate
        if gate is not None and not gate["used"]:
            gate["used"] = True
            gate["started"].set()
            assert gate["release"].wait(5)

    def generate_yaml(self):
        blob = "\n".join(self.texts)
        people = []
        if "John Doe" in blob:
            people.append("John Doe")
        if "Jane Roe" in blob:
            people.append("Jane Roe")
        if not people:
            people.append("Anon")
        lines = ["PERSON:"]
        for index, name in enumerate(people, 1):
            lines.append(f'  - id: "PERSON_{index}"')
            lines.append("    variants:")
            lines.append(f'      - "{name}"')
        return "\n".join(lines) + "\n"

    def load_replacements(self, config):
        self.loaded = config

    def anonymize(self, text):
        if Scripted.leak:
            return text, {}
        return text.replace("John Doe", "#(P1V1)").replace("Jane Roe", "#(P2V1)"), {}


def _write_outputs(
    files, anonymizer, yaml_text, mode, version_stage, source_texts=None
):
    stage = Path(version_stage)
    output = stage / "output"
    output.mkdir(parents=True, exist_ok=True)
    texts = source_texts or {}
    written = []
    for path in files:
        body = texts.get(path, "")
        if not Scripted.leak:
            body = body.replace("John Doe", "#(P1V1)").replace("Jane Roe", "#(P2V1)")
        if mode == "single":
            written.append(output / "combined.typ")
            written[-1].write_text(body + "\n", encoding="utf-8")
            break
        if mode == "pdf":
            written.append(output / f"{path.stem}_pseudo.pdf")
            written[-1].write_bytes(b"%PDF-1.4\n")
            continue
        written.append(output / f"{path.stem}_pseudonymized.typ")
        written[-1].write_text(body + "\n", encoding="utf-8")
    if _write_outputs.fake:
        (output / "shared_fakevars.typ").write_text("// fake\n", encoding="utf-8")
    return output


_write_outputs.fake = True


class Client:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path: str, timeout=None) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
            assert response.status == 200
            assert response.headers.get("Access-Control-Allow-Origin") is None
            return json.loads(response.read().decode())

    def post(self, path: str, body=None, *, header=True):
        data = json.dumps(body or {}).encode()
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Did"] = "1"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            return exc.code, json.loads(raw) if raw else {}

    def wait(self, timeout=5.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.get("/api/state")
            if not last["busy"]:
                return last
            time.sleep(0.02)
        raise AssertionError(last)


@pytest.fixture
def client(monkeypatch):
    Scripted.seen = []
    Scripted.batches = []
    Scripted.leak = False
    Scripted.gate = None
    _write_outputs.fake = True
    monkeypatch.setattr(pipeline, "save_version_outputs", _write_outputs)
    Handler.app = DidApp(factory=Scripted)
    Handler.window = None
    Handler.stopped = threading.Event()
    Handler.token = secrets.token_hex(16)
    Handler.agent = ""
    Handler.agents = ProjectAgents("http://127.0.0.1/")
    httpd = Server(("127.0.0.1", 0), Handler)
    Handler.url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    Handler.agents.url = Handler.url
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(httpd.server_address[1])
    finally:
        Handler.agents.stop()
        Handler.agents = None
        httpd.shutdown()
        httpd.server_close()


def _new(client: Client, parent: Path, name="Case", language=None):
    parent.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "parent": str(parent)}
    if language:
        body["language"] = language
    status, payload = client.post("/api/project/new", body)
    assert status == 200, payload
    return client.wait()


def _add(client: Client, path: Path, text: str):
    """Add a document and scan it. Adding alone does not run Presidio."""
    path.write_text(text, encoding="utf-8")
    status, payload = client.post("/api/documents/add", {"paths": [str(path)]})
    assert status == 200, payload
    client.wait()
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    return client.wait()


def _person(state, name):
    return next(
        row
        for row in state["entities"]
        if row["type"] == "PERSON" and not row["child"] and name in row["variants"]
    )


def test_server_binds_loopback_only(client):
    # The fixture server is the one the page talks to.
    assert client.get("/api/state")["active"] is None
    status, payload = client.post("/api/project/new", {"name": "X"}, header=False)
    assert status == 400
    assert "X-Did" in payload["error"]


def test_new_project_defaults_to_danish(client, tmp_path):
    state = _new(client, tmp_path / "cases")
    assert state["language"] == "da"
    assert state["profile"] == "thorough"
    assert state["active"]
    assert state["project_name"] == "Case"
    assert state["projects"][0]["mark"] == "○"


def test_toolbar_language_reaches_detection(client, tmp_path):
    status, payload = client.post("/api/settings", {"language": "sv"})
    assert status == 200, payload
    _new(client, tmp_path / "cases")
    _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    assert Scripted.seen[-1][0] == "sv"


def test_startup_lists_recent_projects_and_opens_none(client, tmp_path):
    _new(client, tmp_path / "cases", name="Kept")
    fresh = Session(factory=Scripted)
    state = fresh.state()
    assert state["active"] is None
    assert [node["name"] for node in state["projects"]] == ["Kept"]


def test_missing_recent_project_is_pruned(tmp_path):
    settings = GuiSettings()
    settings.add_recent_project(tmp_path / "gone" / "project.did-project.yaml")
    state = Session(factory=Scripted).state()
    assert state["projects"] == []
    assert GuiSettings().recent == []


def test_duplicate_project_names_are_disambiguated(client, tmp_path):
    _new(client, tmp_path / "alpha", name="Case")
    _new(client, tmp_path / "beta", name="Case")
    labels = [node["label"] for node in client.get("/api/state")["projects"]]
    assert len(labels) == 2
    assert len(set(labels)) == 2
    assert all(label.startswith("Case (") for label in labels)


def test_remove_keeps_the_workdir_and_delete_needs_confirm(client, tmp_path):
    parent = tmp_path / "cases"
    state = _new(client, parent, name="Case")
    path = state["active"]
    workdir = Path(path).parent
    assert workdir.is_dir()
    status, _payload = client.post("/api/project/remove", {"path": path})
    assert status == 200
    assert workdir.is_dir()
    assert client.get("/api/state")["projects"] == []
    status, payload = client.post("/api/project/delete", {"path": path})
    assert status == 409
    assert workdir.is_dir()
    status, payload = client.post(
        "/api/project/delete", {"path": path, "confirm": True}
    )
    assert status == 200, payload
    assert not workdir.exists()


def test_added_document_merges_review(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "one.txt", "John Doe was here.")
    john = _person(state, "John Doe")
    status, payload = client.post(
        "/api/entities/variant",
        {"entity_type": "PERSON", "id": john["id"], "variant": "Johnny"},
    )
    assert status == 200, payload
    state = _add(client, tmp_path / "two.txt", "Jane Roe arrived.")
    john = _person(state, "John Doe")
    jane = _person(state, "Jane Roe")
    assert "Johnny" in john["variants"]
    assert jane["id"] != john["id"]
    draft = Path(state["active"]).parent / "draft" / "entities.yaml"
    text = draft.read_text(encoding="utf-8")
    assert "Johnny" in text
    assert "Jane Roe" in text


def test_stale_detection_does_not_clobber_the_open_project(client, tmp_path):
    _new(client, tmp_path / "first", name="First")
    Scripted.gate = {
        "used": False,
        "started": threading.Event(),
        "release": threading.Event(),
    }
    letter = tmp_path / "letter.txt"
    letter.write_text("John Doe was here.", encoding="utf-8")
    status, payload = client.post("/api/documents/add", {"paths": [str(letter)]})
    assert status == 200, payload
    assert client.wait()["preview"]["state"] == "UNANON"
    assert not Scripted.gate["started"].is_set()
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    assert Scripted.gate["started"].wait(5)
    _new(client, tmp_path / "second", name="Second")
    Scripted.gate["release"].set()
    state = client.wait()
    assert state["project_name"] == "Second"
    assert state["entities"] == []
    assert state["error"] is None


def test_review_edits_persist_in_the_draft(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    john = _person(state, "John Doe")
    jane = _person(state, "Jane Roe")
    status, payload = client.post(
        "/api/entities/variant",
        {"entity_type": "PERSON", "id": john["id"], "variant": "Johnny"},
    )
    assert status == 200, payload
    status, payload = client.post(
        "/api/entities/type",
        {"ids": [john["id"], jane["id"]], "type": "ORGANIZATION"},
    )
    assert status == 200, payload
    state = client.get("/api/state")
    kinds = {row["type"] for row in state["entities"] if not row["child"]}
    assert kinds == {"ORGANIZATION"}
    draft = Path(state["active"]).parent / "draft" / "entities.yaml"
    assert "ORGANIZATION" in draft.read_text(encoding="utf-8")

    _new(client, tmp_path / "other", name="Other")
    state = _add(client, tmp_path / "other.txt", "John Doe met Jane Roe.")
    john = _person(state, "John Doe")
    jane = _person(state, "Jane Roe")
    status, payload = client.post(
        "/api/entities/merge", {"source": jane["id"], "target": john["id"]}
    )
    assert status == 200, payload
    state = client.get("/api/state")
    people = [
        row for row in state["entities"] if row["type"] == "PERSON" and not row["child"]
    ]
    assert len(people) == 1
    assert "Jane Roe" in people[0]["variants"]
    status, payload = client.post("/api/entities/not-name", {"ids": [people[0]["id"]]})
    assert status == 200, payload
    workdir = Path(client.get("/api/state")["active"]).parent
    yaml_text = (workdir / "draft" / "entities.yaml").read_text(encoding="utf-8")
    assert "John Doe" not in yaml_text
    assert "Jane Roe" not in yaml_text
    assert "John Doe" in (workdir / "draft" / "not_names.json").read_text(
        encoding="utf-8"
    )


def test_original_preview_is_the_source_and_version_resolves_by_stem(client, tmp_path):
    _new(client, tmp_path / "cases")
    source = "John Doe met Jane Roe."
    state = _add(client, tmp_path / "letter.txt", source)
    status, payload = client.post("/api/preview", {"original": True})
    assert status == 200, payload
    state = client.get("/api/state")
    assert state["preview"]["original"] is True
    assert state["preview"]["text"] == source
    assert state["preview"]["state"] == "RAW"
    status, payload = client.post("/api/preview", {"original": False})
    assert status == 200, payload
    version = next(
        node["output"]["path"]
        for node in client.get("/api/state")["projects"]
        if node["active"]
    )
    status, payload = client.post("/api/version/view", {"path": version})
    assert status == 200, payload
    status, payload = client.post("/api/preview", {"original": True})
    assert status == 200, payload
    preview = client.get("/api/state")["preview"]
    assert preview["name"].startswith("letter")
    assert preview["text"] == source


def test_formats_change_the_preview_and_the_handoff_stays_tokenized(client, tmp_path):
    _new(client, tmp_path / "cases")
    _add(client, tmp_path / "secret-letter-name.txt", "John Doe met Jane Roe.")
    status, payload = client.post("/api/preview", {"format": "plain"})
    assert status == 200, payload
    plain = client.wait()["preview"]["text"]
    assert "[PERSON 1]" in plain
    assert "#(P1V1)" not in plain
    status, payload = client.post("/api/preview", {"format": "redacted"})
    assert status == 200, payload
    assert "[REDACTED]" in client.wait()["preview"]["text"]
    status, payload = client.post("/api/preview", {"format": "synthetic"})
    assert status == 200, payload
    synthetic = client.wait()["preview"]["text"]
    assert "John Doe" not in synthetic
    status, payload = client.post("/api/preview", {"format": "typst"})
    assert status == 200, payload
    status, handoff = client.post("/api/llm", {"scope": "all", "form": "md"})
    assert status == 200, handoff
    assert "#(P1V1)" in handoff["text"]
    assert "#(DOC1V1)" in handoff["text"]
    assert "secret-letter-name" not in handoff["text"]
    assert "John Doe" not in handoff["text"]


def test_output_label_and_select_keep_the_documents(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    active = next(node for node in state["projects"] if node["active"])
    docs = [doc["name"] for doc in active["documents"]]
    assert docs == ["letter.txt"]
    output = active["output"]
    chosen = next(item for item in output["files"] if item["label"] == "letter")
    status, payload = client.post(
        "/api/documents/select",
        {"path": chosen["path"], "version": output["path"]},
    )
    assert status == 200, payload
    state = client.get("/api/state")
    active = next(node for node in state["projects"] if node["active"])
    assert [doc["name"] for doc in active["documents"]] == docs
    assert state["read_only"] is True
    assert any(item["selected"] for item in active["output"]["files"])
    status, payload = client.post(
        "/api/documents/select", {"path": active["documents"][0]["path"]}
    )
    assert status == 200, payload
    state = client.get("/api/state")
    assert state["read_only"] is False
    assert state["preview"]["name"] == "letter.txt"


def test_synthetic_preview_renders_off_the_request(client, monkeypatch, tmp_path):
    """Changing review text must not run Faker on the thread that serves the click."""
    _new(client, tmp_path / "cases")
    _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    release = threading.Event()
    names = []
    real = pipeline.to_synthetic

    def blocked(text, yaml_text, language="en", seed="did"):
        names.append(threading.current_thread().name)
        assert release.wait(5)
        return real(text, yaml_text, language, seed)

    monkeypatch.setattr(pipeline, "to_synthetic", blocked)
    status, payload = client.post("/api/preview", {"format": "synthetic"})
    assert status == 200, payload
    state = client.get("/api/state", timeout=2)
    assert state["busy"]
    assert "#(P1V1)" in state["preview"]["text"]
    release.set()
    done = client.wait()
    assert names
    assert set(names) == {"did-gui-preview"}
    assert "John Doe" not in done["preview"]["text"]
    assert "#(P1V1)" not in done["preview"]["text"]


def test_verification_gates_save_and_an_edit_drops_the_ack(client, tmp_path):
    Scripted.leak = True
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    assert state["verification"]["clean"] is False
    assert any(node["output"] for node in state["projects"])
    status, payload = client.post("/api/save", {"mode": "multi"})
    assert status == 409
    assert payload["needs_ack"] is True
    status, payload = client.post("/api/verify/ack")
    assert status == 200, payload
    status, payload = client.post("/api/save", {"mode": "multi"})
    assert status == 200, payload
    client.wait()
    john = _person(client.get("/api/state"), "John Doe")
    status, payload = client.post(
        "/api/entities/variant",
        {"entity_type": "PERSON", "id": john["id"], "variant": "Johnny"},
    )
    assert status == 200, payload
    status, payload = client.post("/api/save", {"mode": "multi"})
    assert status == 409
    assert payload["needs_ack"] is True


def test_clean_output_saves_without_an_ack(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    assert state["verification"]["clean"] is True
    output = next(node["output"] for node in state["projects"] if node["active"])
    assert output["mode"] == "PSEUDO/FAKE"
    assert output["mark"] == "✓"
    _write_outputs.fake = False
    status, payload = client.post("/api/save", {"mode": "single"})
    assert status == 200, payload
    state = client.wait()
    output = next(node["output"] for node in state["projects"] if node["active"])
    assert output["mode"] == "PSEUDO"
    assert any(item["name"] == "combined.typ" for item in output["files"])
    status, payload = client.post("/api/save", {"mode": "pdf"})
    assert status == 200, payload
    state = client.wait()
    names = [
        item["name"]
        for node in state["projects"]
        if node["active"]
        for item in node["output"]["files"]
    ]
    assert names == ["letter_pseudo.pdf"]


def test_llm_zip_has_no_source_names(client, tmp_path):
    _new(client, tmp_path / "cases")
    _add(client, tmp_path / "secret-letter-name.txt", "John Doe met Jane Roe.")
    dest = tmp_path / "handoff.zip"
    status, payload = client.post("/api/llm/zip", {"dest": str(dest)})
    assert status == 200, payload
    with zipfile.ZipFile(payload["path"]) as archive:
        names = set(archive.namelist())
        blob = "\n".join(archive.read(name).decode() for name in names)
    assert names == {
        "combined.md",
        "session.json",
        "README.txt",
        "documents/document-001.md",
    }
    assert "secret-letter-name" not in blob
    assert "#(DOC1V1)" in blob
    assert "John Doe" not in blob


def test_sync_mark_for_a_missing_source(client, tmp_path):
    _new(client, tmp_path / "cases")
    letter = tmp_path / "letter.txt"
    _add(client, letter, "John Doe met Jane Roe.")
    letter.unlink()
    document = client.get("/api/state")["projects"][0]["documents"][0]
    assert document["mark"] == "!"
    assert document["missing"] is True


def test_raise_helper_touches_the_raise_file(tmp_path):
    target = tmp_path / "raise"
    assert ask_raise({"raise": str(target)}) is True
    assert target.is_file()


def test_second_launch_raises_the_running_server(capsys):
    target = Path(os.environ["XDG_RUNTIME_DIR"]) / "raise"
    lock_path().write_text(
        json.dumps(
            {"url": "http://127.0.0.1:9/", "pid": os.getpid(), "raise": str(target)}
        ),
        encoding="utf-8",
    )
    serve_gui(port=8791, headless=True, factory=Scripted)
    assert "Raised" in capsys.readouterr().out
    assert target.is_file()
    assert running_instance()["pid"] == os.getpid()


def test_headless_quit_stops_the_server():
    import socket
    from urllib.parse import urlparse

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    def run():
        serve_gui(port=port, headless=True, factory=Scripted)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    record = None
    for _ in range(50):
        record = running_instance()
        if record:
            break
        time.sleep(0.05)
    assert record
    status, payload = Client(urlparse(record["url"]).port).post("/api/quit")
    assert status == 200, payload
    thread.join(3)
    assert not thread.is_alive()
    assert running_instance() is None


def test_qt_ini_imports_once(tmp_path):
    root = Path(os.environ["XDG_CONFIG_HOME"])
    ini = root / "evidlabel" / "DID Pseudonymizer.conf"
    ini.parent.mkdir(parents=True)
    ini.write_text(
        "[General]\nrecentProjects=/a, /b\n\n[directories]\nexports=/tmp/out\n",
        encoding="utf-8",
    )
    settings = GuiSettings()
    assert [str(path) for path in settings.recent_projects()] == ["/a", "/b"]
    assert settings.directory("exports") == "/tmp/out"
    ini.write_text("[General]\nrecentProjects=/c\n", encoding="utf-8")
    again = GuiSettings()
    assert [str(path) for path in again.recent_projects()] == ["/a", "/b"]

    numbered = tmp_path / "numbered.conf"
    numbered.write_text(
        "[recentProjects]\n1\\path=/one\n2\\path=/two\n",
        encoding="utf-8",
    )
    from gdid.web.settings import import_qt_settings

    imported = import_qt_settings(numbered)
    assert imported["recent"] == ["/one", "/two"]


def test_filesystem_listing_skips_dotfiles(client, tmp_path):
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    listing = client.get("/api/fs?path=" + str(tmp_path))
    assert listing["path"] == str(tmp_path)
    assert [entry["name"] for entry in listing["entries"]] == ["visible.txt"]


def test_directory_dialog_is_the_desktop_folder_picker(tmp_path):
    nested = tmp_path / "cases"
    nested.mkdir()
    command = directory_dialog_command(str(nested / "missing"))
    assert command[:3] == ["zenity", "--file-selection", "--directory"]
    assert "--title=Choose a folder" in command
    filename = next(part for part in command if part.startswith("--filename="))
    assert filename == f"--filename={nested}{os.sep}"

    chosen = nested / "fresh"
    chosen.mkdir()
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=str(chosen) + "\n", stderr="")

    assert pick_directory(str(nested), run=run) == str(chosen)
    assert seen["cmd"][2] == "--directory"

    def cancel(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    assert pick_directory(str(nested), run=cancel) is None

    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")

    def a_file(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=str(note) + "\n", stderr="")

    with pytest.raises(ApiError):
        pick_directory(str(nested), run=a_file)


def test_directory_endpoint_returns_the_chosen_folder(client, monkeypatch, tmp_path):
    monkeypatch.setattr("gdid.web.server.pick_directory", lambda path: str(tmp_path))
    status, payload = client.post("/api/fs/directory", {"path": str(tmp_path)})
    assert status == 200, payload
    assert payload["path"] == str(tmp_path)


def test_file_dialogs_use_the_same_picker(tmp_path):
    letter = tmp_path / "letter.txt"
    other = tmp_path / "other.txt"
    letter.write_text("a", encoding="utf-8")
    other.write_text("b", encoding="utf-8")
    command = file_dialog_command("files", str(tmp_path))
    assert command[:2] == ["zenity", "--file-selection"]
    assert "--multiple" in command
    assert "--directory" not in command
    assert "--title=Add documents" in command
    sep = next(part for part in command if part.startswith("--separator="))
    sep = sep.removeprefix("--separator=")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"{letter}{sep}{other}\n", stderr=""
        )

    assert pick_paths("files", str(tmp_path), run=run) == [str(letter), str(other)]

    project = tmp_path / "project.did-project.yaml"
    project.write_text("name: Case\n", encoding="utf-8")
    command = file_dialog_command("project", str(tmp_path))
    assert command[:2] == ["zenity", "--file-selection"]
    assert "--file-filter=DID project | *.yaml *.yml" in command
    assert "--title=Open DID project" in command

    def open_project(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=str(project) + "\n", stderr=""
        )

    assert pick_paths("project", str(tmp_path), run=open_project) == [str(project)]

    def not_a_project(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=str(letter) + "\n", stderr="")

    with pytest.raises(ApiError):
        pick_paths("project", str(tmp_path), run=not_a_project)
    with pytest.raises(ApiError):
        file_dialog_command("nope", str(tmp_path))


def test_pick_endpoint_returns_chosen_files(client, monkeypatch, tmp_path):
    letter = tmp_path / "letter.txt"
    monkeypatch.setattr("gdid.web.server.pick_paths", lambda kind, path: [str(letter)])
    status, payload = client.post(
        "/api/fs/pick", {"kind": "files", "path": str(tmp_path)}
    )
    assert status == 200, payload
    assert payload["paths"] == [str(letter)]


def test_document_mode_labels():
    session = Session(factory=Scripted)
    path = Path("letter.txt")
    assert session._document_mode(path, {}, {path: object()}, "pending") == "UNANON"
    assert session._document_mode(path, {path: "x"}, {}, "pending") == "PSEUDO"
    assert session._document_mode(path, {}, {}, "synced") == "PSEUDO"
    assert session._document_mode(path, {}, {}, "pending") == "PENDING"


def test_open_save_and_save_as(client, tmp_path):
    state = _new(client, tmp_path / "cases", name="Case")
    saved = Path(state["active"])
    status, payload = client.post("/api/project/save")
    assert status == 200, payload
    assert Path(payload["path"]) == saved

    other = tmp_path / "elsewhere"
    status, payload = client.post("/api/project/save-as", {"parent": str(other)})
    assert status == 200, payload
    copied = Path(payload["path"])
    assert copied.is_file()
    assert copied != saved
    assert saved.is_file()
    state = client.get("/api/state")
    assert Path(state["active"]) == copied
    assert state["project_name"] == "Case"

    status, payload = client.post("/api/project/open", {"path": str(saved)})
    assert status == 200, payload
    state = client.wait()
    assert Path(state["active"]) == saved.resolve()


def test_clear_recent_keeps_the_open_project(client, tmp_path):
    _new(client, tmp_path / "a", name="Alpha")
    _new(client, tmp_path / "b", name="Beta")
    state = client.get("/api/state")
    assert {project["name"] for project in state["projects"]} == {"Alpha", "Beta"}
    status, payload = client.post("/api/project/clear-recent")
    assert status == 200, payload
    state = client.get("/api/state")
    assert [project["name"] for project in state["projects"]] == ["Beta"]
    assert state["project_name"] == "Beta"


def test_rerun_detection_merges_the_review(client, tmp_path):
    _new(client, tmp_path / "cases")
    status, payload = client.post("/api/detect")
    assert status == 400
    assert "document" in payload["error"].lower()
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    assert any("John Doe" in row["variants"] for row in state["entities"])
    before = len(Scripted.seen)
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    state = client.wait()
    assert len(Scripted.seen) > before
    assert "Merged review" in state["status"]
    assert any("John Doe" in row["variants"] for row in state["entities"])
    assert state["keys"]["folder"].endswith("/draft")
    assert state["keys"]["path"].endswith("/draft/entities.yaml")


def test_annotation_queue_stays_in_the_draft(client, tmp_path):
    _new(client, tmp_path / "cases")
    status, payload = client.post(
        "/api/annotations",
        {"text": "  Jane   Roe ", "document": "letter.txt", "source": True},
    )
    assert status == 200, payload
    assert payload["added"] is True
    assert payload["annotations"][0]["text"] == "Jane Roe"
    assert payload["annotations"][0]["source"] is True
    state = client.get("/api/state")
    assert state["annotations"][0]["text"] == "Jane Roe"
    stored = Path(state["workdir"]) / "draft" / "annotations.json"
    assert "Jane Roe" in stored.read_text(encoding="utf-8")
    status, again = client.post(
        "/api/annotations",
        {"text": "jane roe", "document": "letter.txt", "source": True},
    )
    assert again["added"] is False
    assert len(again["annotations"]) == 1
    status, payload = client.post("/api/annotations", {"text": "x" * 401})
    assert status == 400
    status, payload = client.post(
        "/api/annotations/remove", {"id": state["annotations"][0]["id"]}
    )
    assert status == 200, payload
    assert payload["annotations"] == []
    assert client.get("/api/state")["annotations"] == []


def test_review_bundle_sends_tokens_not_source(client, tmp_path):
    _new(client, tmp_path / "cases")
    _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    status, payload = client.post("/api/review-bundle", {})
    assert status == 200, payload
    assert "John Doe" in payload["yaml"]
    assert payload["withheld"] == 0
    assert payload["documents"] == [
        {
            "name": "letter.txt",
            "text": payload["documents"][0]["text"],
        }
    ]
    body = payload["documents"][0]["text"]
    assert "John Doe" not in body
    assert "Jane Roe" not in body
    assert "#(P1V1)" in body
    assert "#(P2V1)" in body


def test_keys_round_trip_and_version_is_read_only(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    status, payload = client.post("/api/keys")
    assert status == 200, payload
    assert "John Doe" in payload["yaml"]
    assert payload["read_only"] is False
    status, payload = client.post("/api/keys", {"yaml": payload["yaml"]})
    assert status == 200, payload
    version = state["projects"][0]["output"]["path"]
    status, payload = client.post("/api/version/view", {"path": version})
    assert status == 200, payload
    status, payload = client.post("/api/keys")
    assert payload["read_only"] is True
    status, payload = client.post("/api/keys", {"yaml": "PERSON: []\n"})
    assert status == 400


def test_copy_output_stays_inside_the_project(client, tmp_path):
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    version = state["projects"][0]["output"]["path"]
    parent = tmp_path / "copies"
    parent.mkdir()
    status, payload = client.post(
        "/api/version/copy", {"version": version, "parent": str(parent)}
    )
    assert status == 200, payload
    assert Path(payload["path"]).is_dir()
    status, payload = client.post(
        "/api/version/copy", {"version": str(tmp_path), "parent": str(parent)}
    )
    assert status == 400
    status, payload = client.post(
        "/api/version/copy", {"version": version, "parent": str(parent)}
    )
    assert status == 400
    assert "already exists" in payload["error"]


def test_state_returns_while_detection_settles(client, monkeypatch, tmp_path):
    """The page poll must not wait on merge, pseudonymize, or verify."""
    started = threading.Event()
    release = threading.Event()
    real = pipeline.settle_detection

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "settle_detection", blocked)
    _new(client, tmp_path / "cases")
    letter = tmp_path / "letter.txt"
    letter.write_text("John Doe met Jane Roe.", encoding="utf-8")
    status, payload = client.post("/api/documents/add", {"paths": [str(letter)]})
    assert status == 200, payload
    state = client.wait()
    assert not started.is_set()
    assert state["preview"]["state"] == "UNANON"
    assert state["preview"]["text"] == "John Doe met Jane Roe."
    assert Scripted.seen == []
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    assert started.wait(5)
    state = client.get("/api/state", timeout=1)
    assert state["busy"]
    assert state["preview"]["state"] == "UNANON"
    assert state["preview"]["original"] is False
    assert state["preview"]["text"] == "John Doe met Jane Roe."
    documents = next(node["documents"] for node in state["projects"] if node["active"])
    assert documents[0]["mode"] == "UNANON"
    release.set()
    state = client.wait()
    assert state["busy"] is None
    assert state["preview"]["state"] == "PSEUDONYMIZED"
    assert "#(P1V1)" in state["preview"]["text"]
    assert "John Doe" not in state["preview"]["text"]
    assert any("John Doe" in row["variants"] for row in state["entities"])


def test_reopen_uses_the_extract_and_detection_cache(client, tmp_path, monkeypatch):
    """Unchanged files are shown from memory. A changed file is read again."""
    calls = {"n": 0}
    real = pipeline.extract_one

    def counted(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(pipeline, "extract_one", counted)
    _new(client, tmp_path / "cases")
    letter = tmp_path / "letter.txt"
    state = _add(client, letter, "John Doe met Jane Roe.")
    assert calls["n"] == 1
    assert state["preview"]["state"] == "PSEUDONYMIZED"
    seen = len(Scripted.seen)
    status, payload = client.post("/api/project/open", {"path": state["active"]})
    assert status == 200, payload
    state = client.wait()
    assert calls["n"] == 1
    assert len(Scripted.seen) == seen
    assert state["preview"]["state"] == "PSEUDONYMIZED"
    assert state["preview"]["original"] is False
    assert "#(P1V1)" in state["preview"]["text"]
    letter.write_text("Jane Roe wrote back.", encoding="utf-8")
    status, payload = client.post("/api/project/open", {"path": state["active"]})
    assert status == 200, payload
    state = client.wait()
    assert calls["n"] == 2
    assert len(Scripted.seen) == seen
    assert state["preview"]["state"] == "PSEUDONYMIZED"
    assert "#(P2V1)" in state["preview"]["text"]
    assert "wrote back" in state["preview"]["text"]


def test_showing_the_source_does_not_scan_the_set(client, tmp_path):
    """Original, and opening the documents, must not run Presidio on the set."""
    _new(client, tmp_path / "cases")
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_text("John Doe met Jane Roe.", encoding="utf-8")
    two.write_text("Jane Roe wrote back.", encoding="utf-8")
    status, payload = client.post("/api/documents/add", {"paths": [str(one), str(two)]})
    assert status == 200, payload
    state = client.wait()
    assert state["preview"]["state"] == "UNANON"
    assert "John Doe" in state["preview"]["text"]
    assert state["scanned"] is False
    assert state["keys"]["count"] == 0
    assert state["keys"]["label"] == "Keys: none yet."
    assert Scripted.seen == []
    assert Scripted.batches == []
    status, payload = client.post("/api/preview", {"original": True})
    assert status == 200, payload
    state = client.get("/api/state")
    assert state["preview"]["original"] is True
    assert state["preview"]["text"].startswith("John Doe")
    assert Scripted.seen == []
    status, payload = client.post("/api/project/open", {"path": state["active"]})
    assert status == 200, payload
    state = client.wait()
    assert state["preview"]["state"] == "UNANON"
    assert Scripted.seen == []
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    state = client.wait()
    assert Scripted.batches == [2]
    assert state["scanned"] is True
    assert "#(P1V1)" in state["preview"]["text"]


def test_original_stops_a_running_scan(client, tmp_path):
    _new(client, tmp_path / "cases")
    Scripted.gate = {
        "used": False,
        "started": threading.Event(),
        "release": threading.Event(),
    }
    letter = tmp_path / "letter.txt"
    letter.write_text("John Doe met Jane Roe.", encoding="utf-8")
    status, payload = client.post("/api/documents/add", {"paths": [str(letter)]})
    assert status == 200, payload
    client.wait()
    status, payload = client.post("/api/detect")
    assert status == 200, payload
    assert Scripted.gate["started"].wait(5)
    status, payload = client.post("/api/preview", {"original": True})
    assert status == 200, payload
    Scripted.gate["release"].set()
    state = client.wait()
    assert state["error"] is None
    assert state["preview"]["original"] is True
    assert state["preview"]["text"] == "John Doe met Jane Roe."
    assert state["preview"]["state"] == "RAW"
    assert state["entities"] == []
    assert state["scanned"] is False


def test_state_returns_while_a_review_edit_rewrites(client, monkeypatch, tmp_path):
    """Changing an identity must not run pseudonymize on the request thread."""
    _new(client, tmp_path / "cases")
    state = _add(client, tmp_path / "letter.txt", "John Doe met Jane Roe.")
    john = _person(state, "John Doe")
    started = threading.Event()
    release = threading.Event()
    real = pipeline.pseudonymize_all

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "pseudonymize_all", blocked)
    status, payload = client.post(
        "/api/entities/variant",
        {"entity_type": "PERSON", "id": john["id"], "variant": "Johnny"},
    )
    assert status == 200, payload
    assert started.wait(5)
    state = client.get("/api/state", timeout=1)
    assert state["busy"]
    assert any("Johnny" in row["variants"] for row in state["entities"])
    release.set()
    state = client.wait()
    assert state["busy"] is None
    assert any("Johnny" in row["variants"] for row in state["entities"])
    assert state["error"] is None


def test_help_links_are_allowlisted(client, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "gdid.web.server.webbrowser.open", lambda url: opened.append(url)
    )
    status, payload = client.post(
        "/api/open-url", {"url": "https://github.com/evidlabel/did"}
    )
    assert status == 200, payload
    assert opened == ["https://github.com/evidlabel/did"]
    status, payload = client.post("/api/open-url", {"url": "https://example.com"})
    assert status == 400
    assert opened == ["https://github.com/evidlabel/did"]


def test_vendor_files(client):
    for name in ("xterm.js", "xterm.css", "addon-fit.js"):
        with urllib.request.urlopen(client.base + "/vendor/" + name) as response:
            assert response.status == 200
            assert int(response.headers["Content-Length"]) > 100
    for path in (
        "/vendor/nope.js",
        "/vendor/../server.py",
        "/vendor/LICENSE-xterm.txt",
    ):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(client.base + path)
        assert caught.value.code == 404


def test_resolve_agent_defaults_to_the_local_qwen_pairing(monkeypatch):
    monkeypatch.delenv("DID_AGENT", raising=False)
    assert resolve_agent(None) == DEFAULT_AGENT
    assert DEFAULT_AGENT == "ocprofile launch local-qw38"
    monkeypatch.setenv("DID_AGENT", "")
    assert resolve_agent(None) == ""
    monkeypatch.setenv("DID_AGENT", "echo hi")
    assert resolve_agent(None) == "echo hi"
    assert resolve_agent("echo other") == "echo other"


def test_agent_name_uses_the_local_preset():
    assert agent_name("ocprofile launch local-qw38") == "local-qw38"
    assert agent_name("ocprofile launch local-nemo-qw38") == "local-nemo-qw38"
    assert agent_name("echo hi") == "echo"
    assert agent_name("") == "shell"


def test_terminal_env_strips_gui_variables(monkeypatch):
    monkeypatch.setenv("DID_IN_APP", "1")
    monkeypatch.setenv("DID_RAISE_FILE", "/tmp/did.raise")
    env = terminal_env("http://127.0.0.1:9/")
    assert "DID_IN_APP" not in env
    assert "DID_RAISE_FILE" not in env
    assert env["TERM"] == "xterm-256color"
    assert env["COLORTERM"] == "truecolor"
    assert env["DID_URL"] == "http://127.0.0.1:9/"


def test_external_terminal_uses_ptyxis_without_opening_one(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gdid.web.server.shutil.which",
        lambda name: "/usr/bin/ptyxis" if name == "ptyxis" else None,
    )
    opened = {}

    def fake_popen(cmd, **kwargs):
        opened["cmd"] = cmd
        opened["cwd"] = kwargs.get("cwd")

    monkeypatch.setattr("gdid.web.server.subprocess.Popen", fake_popen)
    desc = external_terminal(tmp_path, {"SHELL": "/bin/sh"}, "echo hi")
    assert opened["cmd"][:4] == [
        "ptyxis",
        "--new-window",
        "--working-directory",
        str(tmp_path),
    ]
    assert opened["cwd"] == str(tmp_path)
    assert desc.startswith("ptyxis ")


def test_terminal_requires_an_open_project(client):
    payload = client.get("/api/terms")
    assert payload["needs_project"] is True
    assert payload["terms"] == []
    assert payload["agent"] == ""
    assert payload["presets"][0]["command"] == DEFAULT_AGENT
    status, body = client.post("/api/term/new", {"agent": "echo hi"})
    assert status == 400
    assert "project" in body["error"].lower()
    status, body = client.post("/api/term/external", {})
    assert status == 400


def test_terminals_stay_with_their_project(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    first = _new(client, tmp_path / "one", "One")
    first_file = first["active"]
    workdir = first["workdir"]
    assert client.get("/api/terms")["terms"] == []
    status, made = client.post("/api/term/new", {"agent": "echo one"})
    assert status == 200, made
    assert made["id"] == "1"
    assert made["terms"][0]["label"] == "echo"
    assert made["terms"][0]["alive"] is False
    second = _new(client, tmp_path / "two", "Two")
    assert second["workdir"] != workdir
    assert client.get("/api/terms")["terms"] == []
    assert client.post("/api/term/close", {"id": made["id"]})[0] == 404
    status, payload = client.post("/api/project/open", {"path": first_file})
    assert status == 200, payload
    client.wait()
    listed = client.get("/api/terms")
    assert listed["workdir"] == workdir
    assert [item["id"] for item in listed["terms"]] == ["1"]
    status, closed = client.post("/api/term/close", {"id": "1"})
    assert status == 200, closed
    assert closed["terms"] == []


def test_terminal_websocket(client, tmp_path, monkeypatch):
    import base64
    import socket

    from gdid.web.term import ws_frame

    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PS1", "$ ")
    opened = _new(client, tmp_path / "case", "Case")
    workdir = opened["workdir"]
    status, made = client.post("/api/term/new", {"agent": "echo hi-there"})
    assert status == 200, made
    host, port = client.base.removeprefix("http://").split(":")
    token = client.get("/api/terms")["token"]

    def handshake(origin, tok=token):
        sock = socket.create_connection((host, int(port)), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET /api/term?token={tok}&id={made['id']} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\nOrigin: {origin}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n\r\n"
            ).encode()
        )
        return sock

    for bad in (
        handshake("http://evil.example"),
        handshake(f"http://{host}:{port}", tok="wrong"),
    ):
        assert b"403" in bad.recv(1024)
        bad.close()
    missing = socket.create_connection((host, int(port)), timeout=5)
    missing.sendall(
        (
            f"GET /api/term?token={token}&id=missing HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\nOrigin: http://{host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n\r\n"
        ).encode()
    )
    assert b"404" in missing.recv(1024)
    missing.close()

    sock = handshake(f"http://{host}:{port}")
    try:
        data, deadline = b"", time.time() + 10
        while (
            b"hi-there" not in data.split(b"echo hi-there")[-1]
            and time.time() < deadline
        ):
            data += sock.recv(65536)
        assert b"101 Switching Protocols" in data
        assert b'"hello"' in data

        def send(op, payload):
            frame = ws_frame(op, payload)
            header = frame[2 : 2 + (len(frame) - 2 - len(payload))]
            sock.sendall(
                frame[:1] + bytes([frame[1] | 0x80]) + header + b"\0\0\0\0" + payload
            )

        send(1, json.dumps({"t": "resize", "rows": 30, "cols": 100}).encode())
        send(1, json.dumps({"t": "in", "d": "echo [$DID_URL][$PWD]\r"}).encode())
        data, deadline = b"", time.time() + 10
        want = f"[{Handler.url}][{workdir}]".encode()
        while want not in data and time.time() < deadline:
            data += sock.recv(65536)
        assert want in data
        sessions = Handler.agents.get(Path(workdir), create=False)
        assert sessions.get(made["id"]).size == (30, 100)
        send(8, b"")
    finally:
        sock.close()
        Handler.agents.stop()


def test_ensure_project_creates_then_opens_the_case_folder(client, tmp_path):
    """The workbench opens each case's own did folder: created when empty, opened after."""
    case = tmp_path / "did" / "2026-014"
    case.mkdir(parents=True)
    status, _ = client.post(
        "/api/project/ensure",
        {"path": str(case), "name": "Tenancy dispute", "evid_set": "2026-014-anon"},
    )
    assert status == 200
    state = client.wait()
    assert (
        state["workdir"] == str(case.resolve())
        and state["project_name"] == "Tenancy dispute"
    )
    assert (case / "project.did-project.yaml").is_file()
    assert state["metadata"]["evid_set"] == "2026-014-anon"
    assert state["handoff"] == {
        "set": "2026-014-anon",
        "at": 0,
        "handed": 0,
        "pending": 0,
    }
    _add(client, tmp_path / "letter.txt", "Ole Hansen kept the deposit.")
    state = client.wait()
    assert (
        state["handoff"]["pending"] == 1
    )  # one document not handed off yet: the workbench badge
    # opening again keeps it (no new project)
    assert client.post("/api/project/ensure", {"path": str(case)})[0] == 200
    assert client.wait()["workdir"] == str(case.resolve())
    full = tmp_path / "busy"
    (full / "x").mkdir(parents=True)
    status, payload = client.post("/api/project/ensure", {"path": str(full)})
    assert status == 400 and "not empty" in payload["error"]


def test_handoff_needs_a_set(client, tmp_path):
    _new(client, tmp_path / "projects")
    _add(client, tmp_path / "a.txt", "Ole Hansen kept the deposit.")
    status, payload = client.post("/api/handoff", {})
    assert status == 400 and "Which evid set" in payload["error"]


def test_agent_pane_keeps_its_own_opencode_data(tmp_path, monkeypatch):
    """did's agent sees raw text: its opencode sessions and prompt history stay in the project."""
    from gdid.web.server import agent_home

    shared = tmp_path / "share"
    (shared / "opencode" / "bin").mkdir(parents=True)
    (shared / "opencode" / "auth.json").write_text("{}")
    (shared / "uv" / "tools").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(shared))
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    work = tmp_path / "did" / "2026-014"
    work.mkdir(parents=True)
    env = agent_home(work)
    assert env["XDG_DATA_HOME"] == str(work / ".agent" / "data")
    assert env["XDG_STATE_HOME"] == str(work / ".agent" / "state")
    assert (
        work / ".agent" / "data" / "opencode" / "auth.json"
    ).resolve() == shared / "opencode" / "auth.json"
    assert (work / ".agent" / "data" / "opencode" / "bin").is_symlink()
    assert env["UV_TOOL_DIR"] == str(shared / "uv" / "tools") and "FNM_DIR" not in env
    assert oct((work / ".agent").stat().st_mode & 0o777) == "0o700"
    assert agent_home(work) == env  # again: same, no error
