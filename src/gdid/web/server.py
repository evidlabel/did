"""Loopback GUI server.

``did gui`` serves ``page.html`` on 127.0.0.1 and opens it in ``did-app`` (or
the browser). A second launch touches the raise file so the window comes
forward. The bind address is fixed: this process holds document text.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import detect_process
from ..project import ProjectError
from .session import ApiError, NeedsAck, Session
from .settings import GuiSettings
from .term import Sessions, agent_name, ws_accept

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 8791
_HEADER = "X-Did"
# One-shot OpenCode launches. `ocprofile launch` does not rewrite the saved profile.
LOCAL_PRESETS = (
    {
        "id": "local-qw38",
        "label": "Local Qwen",
        "command": "ocprofile launch local-qw38",
    },
    {
        "id": "local-nemo",
        "label": "Local Nemotron",
        "command": "ocprofile launch local-nemo",
    },
    {
        "id": "local-nemo-qw38",
        "label": "Nemotron plans, Qwen writes",
        "command": "ocprofile launch local-nemo-qw38",
    },
    {
        "id": "local-qw38-nemo",
        "label": "Qwen plans, Nemotron writes",
        "command": "ocprofile launch local-qw38-nemo",
    },
)
DEFAULT_AGENT = LOCAL_PRESETS[0]["command"]
VENDOR = {
    "xterm.js": "text/javascript; charset=utf-8",
    "xterm.css": "text/css; charset=utf-8",
    "addon-fit.js": "text/javascript; charset=utf-8",
}
_GUI_ENV = ("DID_IN_APP", "DID_RAISE_FILE")
_HELP_URLS = {
    "https://github.com/evidlabel/did",
    "https://github.com/evidlabel/did/tree/main/docs",
    "https://github.com/evidlabel/did/blob/main/LICENSE",
}


class DidApp:
    def __init__(self, settings: GuiSettings | None = None, *, factory=None):
        self.session = Session(settings, factory=factory)


def resolve_agent(agent: str | None = None) -> str:
    """The command typed into a new terminal.

    An explicit ``--agent`` wins, then ``$DID_AGENT`` (including empty, for a
    shell). Otherwise the default is a fully local OpenCode pairing.
    """
    if agent is not None:
        return agent
    if "DID_AGENT" in os.environ:
        return os.environ["DID_AGENT"]
    return DEFAULT_AGENT


def terminal_env(url: str) -> dict:
    """The agent's environment. GUI-only variables stay out of the shell."""
    env = {key: value for key, value in os.environ.items() if key not in _GUI_ENV}
    env.update(TERM="xterm-256color", COLORTERM="truecolor", DID_URL=url)
    return env


AGENT_KEEP = (
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "UV_TOOL_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "FNM_DIR",
)


def agent_home(workdir: Path) -> dict:
    """The agent pane's own XDG data and state, inside the project folder.

    opencode (the default agent) keeps its sessions (``opencode.db``, ``storage/``) under
    XDG data and its prompt history under XDG state. Pointing both into the project's
    ``.agent/`` keeps the case's raw text out of the shared ``~/.local/share/opencode``,
    which other terminals (the workbench's writing agents) can read. Your opencode login
    and tools are linked in; uv and fnm stay where they are."""
    home = Path.home()
    shared_data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    base = Path(workdir) / ".agent"
    data, state = base / "data", base / "state"
    (data / "opencode").mkdir(parents=True, exist_ok=True)
    (state / "opencode").mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    for name in ("auth.json", "bin"):
        src, dst = shared_data / "opencode" / name, data / "opencode" / name
        if src.exists() and not dst.exists() and not dst.is_symlink():
            dst.symlink_to(src)
    env = {"XDG_DATA_HOME": str(data), "XDG_STATE_HOME": str(state)}
    pins = {
        "UV_TOOL_DIR": shared_data / "uv" / "tools",
        "UV_PYTHON_INSTALL_DIR": shared_data / "uv" / "python",
        "FNM_DIR": shared_data / "fnm",
    }
    for key, path in pins.items():
        if key not in os.environ and path.exists():
            env[key] = str(path)
    return env


def external_name() -> str:
    if shutil.which("ptyxis"):
        return "ptyxis"
    if shutil.which("gnome-terminal"):
        return "gnome-terminal"
    return ""


def external_terminal(cwd: Path, env: dict, agent: str) -> str:
    """Open the agent in Ptyxis or GNOME Terminal. Returns a short description."""
    shell = env.get("SHELL") or "/bin/sh"
    keep = [
        f"{key}={value}"
        for key, value in env.items()
        if (key.startswith("DID_") and key != "DID_IN_APP") or key in AGENT_KEEP
    ]
    inner = [shell, "-ic", f"{agent}; exec {shell}"] if agent else [shell, "-i"]
    run = ["env", *keep, *inner]
    if shutil.which("ptyxis"):
        cmd = ["ptyxis", "--new-window", "--working-directory", str(cwd), "--", *run]
    elif shutil.which("gnome-terminal"):
        cmd = ["gnome-terminal", f"--working-directory={cwd}", "--", *run]
    else:
        raise ApiError("neither ptyxis nor gnome-terminal is installed", 404)
    subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return " ".join(cmd[:4])


class ProjectAgents:
    """Terminals kept per open project folder. Other projects keep running."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.lock = threading.Lock()
        self.by_dir: dict[str, Sessions] = {}

    def get(self, workdir: Path, create: bool = True) -> Sessions | None:
        key = str(Path(workdir).resolve())
        with self.lock:
            if key not in self.by_dir and create:
                env = {**terminal_env(self.url), **agent_home(Path(key))}
                self.by_dir[key] = Sessions(key, env)
            return self.by_dir.get(key)

    def stop(self) -> None:
        for sessions in list(self.by_dir.values()):
            sessions.stop()


def list_dir(path: str | None) -> dict:
    raw = Path(path).expanduser() if path else Path.home()
    try:
        current = raw.resolve()
    except OSError:
        current = Path.home()
    if not current.is_dir():
        current = current.parent
    entries = []
    try:
        children = list(current.iterdir())
    except OSError:
        children = []
    for child in sorted(
        children, key=lambda item: (not item.is_dir(), item.name.lower())
    ):
        if child.name.startswith("."):
            continue
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        entries.append({"name": child.name, "path": str(child), "dir": is_dir})
    parent = None if current.parent == current else str(current.parent)
    return {"path": str(current), "parent": parent, "entries": entries}


def existing_dir(path: str | None) -> Path:
    raw = Path(path).expanduser() if path else Path.home()
    try:
        current = raw.resolve()
    except OSError:
        current = Path.home()
    if current.is_dir():
        return current
    if current.parent.is_dir():
        return current.parent
    return Path.home()


# Between paths from `zenity --multiple`. A newline is a legal filename character.
_PICK_SEP = "\x1e"


def file_dialog_command(kind: str, start: str | None) -> list[str]:
    """zenity file dialog. ``directory`` can create a folder."""
    folder = existing_dir(start)
    filename = f"--filename={folder}{os.sep}"
    if kind == "directory":
        return [
            "zenity",
            "--file-selection",
            "--directory",
            "--title=Choose a folder",
            filename,
        ]
    if kind == "files":
        return [
            "zenity",
            "--file-selection",
            "--multiple",
            f"--separator={_PICK_SEP}",
            "--title=Add documents",
            filename,
        ]
    if kind == "project":
        return [
            "zenity",
            "--file-selection",
            "--file-filter=DID project | *.yaml *.yml",
            "--title=Open DID project",
            filename,
        ]
    raise ApiError(f"Unknown picker: {kind}")


def directory_dialog_command(start: str | None) -> list[str]:
    return file_dialog_command("directory", start)


def _dialog_paths(command: list[str], run=None) -> list[str] | None:
    runner = run or subprocess.run
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ApiError("The file picker is not available (zenity).") from exc
    if completed.returncode == 1:
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"exit {completed.returncode}"
        raise ApiError(f"The file picker failed: {detail}")
    text = completed.stdout or ""
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return None
    return [part for part in text.split(_PICK_SEP) if part]


def pick_paths(kind: str, start: str | None, run=None) -> list[str] | None:
    """Open a desktop file dialog. None when the user cancels.

    ``run`` replaces ``subprocess.run`` in tests.
    """
    chosen = _dialog_paths(file_dialog_command(kind, start), run)
    if not chosen:
        return None
    paths = []
    for item in chosen:
        path = Path(item)
        if kind == "directory":
            if not path.is_dir():
                raise ApiError("Choose a folder that exists.")
        elif kind == "project":
            if path.suffix.lower() not in {".yaml", ".yml"} or not path.is_file():
                raise ApiError("Choose a DID project file (.yaml).")
        elif not path.is_file():
            raise ApiError("Choose a document that exists.")
        paths.append(str(path))
    return paths or None


def pick_directory(start: str | None, run=None) -> str | None:
    paths = pick_paths("directory", start, run=run)
    if not paths:
        return None
    return paths[0]


def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    path = Path(base) / "did"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_path() -> Path:
    return _runtime_dir() / "gui.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running_instance() -> dict | None:
    path = lock_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    pid = data.get("pid")
    url = data.get("url")
    if not isinstance(pid, int) or not url or not _pid_alive(pid):
        return None
    return data


def ask_raise(record: dict) -> bool:
    """Tell a running did-app to focus its window. False if nothing is there."""
    target = record.get("raise")
    if not target:
        return False
    path = Path(target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return False
    return True


def find_app() -> str | None:
    """did-app: $DID_APP, else on PATH, else the newest build in app/target."""
    env = os.environ.get("DID_APP")
    if env:
        return env if os.access(env, os.X_OK) else None
    hit = shutil.which("did-app")
    if hit:
        return hit
    root = HERE.parents[2]
    target = root / "app" / "target"
    builds = [
        path
        for path in (target / "release" / "did-app", target / "debug" / "did-app")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if not builds:
        return None
    return str(max(builds, key=lambda path: path.stat().st_mtime))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    app: DidApp
    url = ""
    raise_file: Path | None = None
    window = None
    stopped = threading.Event()
    protocol_version = "HTTP/1.1"
    token = ""
    agent = DEFAULT_AGENT
    agents: ProjectAgents | None = None

    def log_message(self, fmt, *args):
        # Path only. The body can be document text or the identity map.
        sys.stderr.write(f"{self.command} {urlparse(self.path).path}\n")

    def _read_json(self) -> dict:
        if self.headers.get(_HEADER) != "1":
            raise ApiError("Missing X-Did header.", 400)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"Expected JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object.")
        return data

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._json(404, {"error": "missing"})
            return
        self._send(200, data, content_type)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._file(HERE / "page.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/page.js":
            self._file(HERE / "page.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._json(200, self.app.session.state())
            return
        if parsed.path == "/api/fs":
            query = parse_qs(parsed.query)
            path = (query.get("path") or [None])[0]
            self._json(200, list_dir(path))
            return
        if parsed.path.startswith("/vendor/"):
            self._vendor(parsed.path.removeprefix("/vendor/"))
            return
        if parsed.path == "/api/terms":
            self._json(200, self._terms_payload())
            return
        if parsed.path == "/api/term":
            self._upgrade_terminal(parse_qs(parsed.query))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/quit":
                self._json(200, {"ok": True})
                self._request_stop()
                return
            result = self._dispatch(path, body)
        except NeedsAck as exc:
            self._json(409, exc.payload)
            return
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
            return
        except ProjectError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(
            200, result if isinstance(result, dict) else {"ok": True, "result": result}
        )

    def _request_stop(self) -> None:
        Handler.stopped.set()
        if Handler.window is not None:
            Handler.window.terminate()
            return

        def _shutdown():
            time.sleep(0.05)
            with contextlib.suppress(Exception):
                self.server.shutdown()

        threading.Thread(target=_shutdown, daemon=True).start()

    def _dispatch(self, path: str, body: dict):
        session = self.app.session
        if path == "/api/fs/directory":
            return {"path": pick_directory(str(body.get("path") or ""))}
        if path == "/api/fs/pick":
            return {
                "paths": pick_paths(
                    str(body.get("kind") or ""), str(body.get("path") or "")
                )
            }
        if path == "/api/project/new":
            session.new_project(
                str(body.get("name") or ""),
                str(body.get("parent") or ""),
                body.get("language"),
            )
            return {"ok": True}
        if path == "/api/project/open":
            session.open_project(str(body.get("path") or ""))
            return {"ok": True}
        if (
            path == "/api/project/ensure"
        ):  # the workbench: this case's project, created if empty
            session.ensure_project(
                str(body.get("path") or ""),
                str(body.get("name") or ""),
                str(body.get("evid_set") or ""),
            )
            return {"ok": True}
        if path == "/api/handoff":
            return session.handoff(str(body.get("set") or ""))
        if path == "/api/project/rename":
            session.rename_project(str(body.get("name") or ""))
            return {"ok": True}
        if path == "/api/project/remove":
            session.remove_project(str(body.get("path") or ""))
            return {"ok": True}
        if path == "/api/project/delete":
            session.delete_project(
                str(body.get("path") or ""), confirm=bool(body.get("confirm"))
            )
            return {"ok": True}
        if path == "/api/project/save":
            return {"path": session.save_open_project()}
        if path == "/api/project/save-as":
            return {"path": session.save_project_as(str(body.get("parent") or ""))}
        if path == "/api/project/clear-recent":
            session.clear_recent()
            return {"ok": True}
        if path == "/api/project/meta":
            session.set_meta(body.get("metadata") or {})
            return {"ok": True}
        if path == "/api/documents/add":
            session.add_documents(body.get("paths") or [])
            return {"ok": True}
        if path == "/api/documents/remove":
            session.remove_document(str(body.get("path") or ""))
            return {"ok": True}
        if path == "/api/documents/select":
            session.select_document(
                str(body.get("path") or ""), version=body.get("version") or None
            )
            return {"ok": True}
        if path == "/api/preview":
            session.set_preview(
                format=body.get("format"), original=body.get("original")
            )
            return {"ok": True}
        if path == "/api/settings":
            session.set_settings(
                language=body.get("language"), profile=body.get("profile")
            )
            return {"ok": True}
        if path == "/api/detect":
            session.redetect()
            return {"ok": True}
        if path == "/api/keys":
            if "yaml" in body:
                session.replace_keys(str(body.get("yaml") or ""))
                return {"ok": True}
            return session.keys_document()
        if path == "/api/review-bundle":
            return session.review_bundle()
        if path == "/api/annotations":
            return session.add_annotation(
                str(body.get("text") or ""),
                str(body.get("document") or ""),
                bool(body.get("source")),
            )
        if path == "/api/annotations/remove":
            return session.drop_annotation(str(body.get("id") or ""))
        if path == "/api/entities/type":
            session.change_types(body.get("ids") or [], str(body.get("type") or ""))
            return {"ok": True}
        if path == "/api/entities/variant":
            session.add_variant(
                str(body.get("entity_type") or ""),
                str(body.get("id") or ""),
                str(body.get("variant") or ""),
                body.get("position"),
            )
            return {"ok": True}
        if path == "/api/entities/merge":
            session.merge(
                str(body.get("source") or ""),
                str(body.get("target") or ""),
                body.get("variant"),
            )
            return {"ok": True}
        if path == "/api/entities/not-name":
            session.mark_not_names(body.get("ids") or [])
            return {"ok": True}
        if path == "/api/entities/from-selection":
            session.entity_from_selection(
                str(body.get("text") or ""), str(body.get("type") or "PERSON")
            )
            return {"ok": True}
        if path == "/api/entities/add-selection":
            session.add_selection(
                str(body.get("text") or ""),
                str(body.get("entity_type") or ""),
                str(body.get("id") or ""),
            )
            return {"ok": True}
        if path == "/api/save":
            return session.save(str(body.get("mode") or "multi"))
        if path == "/api/verify/ack":
            session.acknowledge()
            return {"ok": True}
        if path == "/api/llm":
            text = session.llm(
                str(body.get("scope") or "all"), str(body.get("form") or "md")
            )
            return {"text": text}
        if path == "/api/llm/zip":
            return {"path": session.llm_zip(str(body.get("dest") or ""))}
        if path == "/api/version/view":
            session.view_version(str(body.get("path") or ""))
            return {"ok": True}
        if path == "/api/version/copy":
            return {
                "path": session.copy_output(
                    str(body.get("version") or ""), str(body.get("parent") or "")
                )
            }
        if path == "/api/open-url":
            url = str(body.get("url") or "")
            if url not in _HELP_URLS:
                raise ApiError("That address is not a DID help page.")
            webbrowser.open(url)
            return {"ok": True}
        if path == "/api/reveal":
            opened = session.reveal(str(body.get("path") or ""))
            return {"opened": opened}
        if path == "/api/term/new":
            return self._term_new(str(body.get("agent") or ""))
        if path == "/api/term/close":
            return self._term_close(str(body.get("id") or ""))
        if path == "/api/term/external":
            return self._term_external()
        raise ApiError("not found", 404)

    def _vendor(self, name: str) -> None:
        if "/" in name or name not in VENDOR:
            self._json(404, {"error": "not found"})
            return
        self._file(HERE / "vendor" / name, VENDOR[name])

    def _workdir(self) -> Path | None:
        session = self.app.session
        with session.lock:
            project = session.project
            if project is None or project.workdir is None:
                return None
            return project.workdir.resolve()

    def _require_agents(self) -> ProjectAgents:
        if Handler.agents is None:
            raise ApiError("the agent pane is off", 404)
        return Handler.agents

    def _open_workdir(self) -> Path:
        workdir = self._workdir()
        if workdir is None:
            raise ApiError("Open a project first.", 400)
        return workdir

    def _terms_payload(self) -> dict:
        workdir = self._workdir()
        terms = []
        if workdir is not None and Handler.agents is not None:
            sessions = Handler.agents.get(workdir, create=False)
            if sessions is not None:
                terms = sessions.list()
        external = external_name()
        return {
            "terms": terms,
            "token": Handler.token,
            "agent": Handler.agent,
            "presets": list(LOCAL_PRESETS),
            "needs_project": workdir is None,
            "workdir": str(workdir) if workdir else None,
            "external": external,
        }

    def _term_new(self, agent: str) -> dict:
        agents = self._require_agents()
        workdir = self._open_workdir()
        sessions = agents.get(workdir, create=True)
        sid = sessions.add(agent)
        label = agent_name(agent) if agent.strip() else sessions.get(sid).label()
        sys.stderr.write(f"agent terminal {sid} {label}\n")
        return {"id": sid, "terms": sessions.list()}

    def _term_close(self, sid: str) -> dict:
        agents = self._require_agents()
        workdir = self._open_workdir()
        sessions = agents.get(workdir, create=False)
        if sessions is None or not sessions.close(sid):
            raise ApiError("no such terminal", 404)
        return {"terms": sessions.list()}

    def _term_external(self) -> dict:
        self._require_agents()
        workdir = self._open_workdir()
        env = {**terminal_env(Handler.url), **agent_home(workdir)}
        cmd = external_terminal(workdir, env, Handler.agent)
        return {"ok": True, "cmd": cmd}

    def _upgrade_terminal(self, query: dict) -> None:
        if Handler.agents is None:
            self._json(404, {"error": "the agent pane is off"})
            return
        workdir = self._workdir()
        if workdir is None:
            self._json(404, {"error": "Open a project first."})
            return
        sessions = Handler.agents.get(workdir, create=False)
        term_id = (query.get("id") or [""])[0]
        terminal = None
        if sessions is not None:
            fallback = next(iter(sessions.terms), "")
            terminal = sessions.get(term_id or fallback)
        if terminal is None:
            self._json(404, {"error": "no such terminal"})
            return
        token = (query.get("token") or [""])[0]
        if not secrets.compare_digest(token, Handler.token):
            self._json(403, {"error": "bad terminal token"})
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin != f"http://{host}":
            self._json(
                403, {"error": "terminal connections must come from the did page"}
            )
            return
        key = self.headers.get("Sec-WebSocket-Key")
        upgrade = self.headers.get("Upgrade") or ""
        if not key or "websocket" not in upgrade.lower():
            self._json(400, {"error": "expected a WebSocket upgrade"})
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept(key))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        with contextlib.suppress(OSError):
            terminal.serve(self.connection, self.rfile)


def _raise_args(path: Path | None) -> list[str]:
    if path is None:
        return []
    return ["--raise-file", str(path)]


def serve_gui(
    *,
    port: int = DEFAULT_PORT,
    browser: bool = False,
    headless: bool = False,
    factory=None,
    settings: GuiSettings | None = None,
    agent: str | None = None,
) -> None:
    """Serve the GUI on 127.0.0.1 and show it in did-app, else the browser.

    Closing the window stops the server. With *headless* nothing is opened;
    the server runs until Ctrl+C or ``POST /api/quit``.
    """
    record = running_instance()
    if record and ask_raise(record):
        print(f"Raised the running did GUI ({record.get('url')}).")
        return

    app = DidApp(settings, factory=factory)
    # The child has its own GIL. Start it on this thread, before any request,
    # so selecting a project never loads spaCy on a GUI request.
    if factory is None:
        detect_process.ensure_started()
    Handler.app = app
    Handler.window = None
    Handler.stopped = threading.Event()
    srv = None
    first = port or DEFAULT_PORT
    for candidate in range(first, first + 20):
        try:
            srv = Server(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    if srv is None:
        sys.exit(f"did: no free port in {first}-{first + 19}")
    url = f"http://127.0.0.1:{srv.server_port}/"
    Handler.url = url
    Handler.token = secrets.token_hex(16)
    Handler.agent = resolve_agent(agent)
    Handler.agents = ProjectAgents(url)
    if os.environ.get("DID_RAISE_FILE"):
        raise_file = Path(os.environ["DID_RAISE_FILE"])
    else:
        raise_file = lock_path().with_suffix(".raise")
    Handler.raise_file = raise_file
    lock = lock_path()
    lock.write_text(
        json.dumps({"url": url, "pid": os.getpid(), "raise": str(raise_file)}),
        encoding="utf-8",
    )
    print(f"did gui\n  open {url}")
    exe = None if headless or browser else find_app()
    try:
        if exe:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            Handler.window = subprocess.Popen(
                [exe, "--url", url, *_raise_args(raise_file)]
            )
            try:
                Handler.window.wait()
            except KeyboardInterrupt:
                Handler.window.terminate()
            finally:
                srv.shutdown()
            return
        if not headless and not browser:
            print(
                "  (did-app not built — `make app-build` or set DID_APP; using the browser)"
            )
        print("  (Ctrl+C to stop)")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        if not headless:
            threading.Timer(0.4, webbrowser.open, [url]).start()
        with contextlib.suppress(KeyboardInterrupt):
            while not Handler.stopped.wait(0.3):
                pass
    finally:
        if Handler.agents is not None:
            Handler.agents.stop()
            Handler.agents = None
        with contextlib.suppress(Exception):
            srv.shutdown()
        srv.server_close()
        with contextlib.suppress(OSError):
            current = json.loads(lock.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                lock.unlink()
        with contextlib.suppress(OSError):
            raise_file.unlink()
