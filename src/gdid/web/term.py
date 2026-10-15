"""The agent pane's terminals: shells on pseudo-terminals, shared over WebSocket.

Taken from evid's pane, which was taken from treedit (same author, MIT).

Standard library only (POSIX). The shell starts on the first connection. With an agent
command, that command is typed into the shell, so quitting the agent leaves a prompt.
Output stays in a scrollback buffer and is replayed when a page connects. The buffer
is not logged.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import signal
import struct
import subprocess
import termios
import threading
from pathlib import Path

SCROLLBACK = 256 * 1024
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def agent_name(command: str) -> str:
    """Short tab label: ``ocprofile launch <profile>`` -> ``<profile>``, else the command's name."""
    words = [
        word
        for word in command.split()
        if "=" not in word and word not in ("env", "exec", "npx", "uvx")
    ]
    if len(words) >= 3 and Path(words[0]).name == "ocprofile" and words[1] == "launch":
        return words[2]
    if not words:
        return command.strip() or "shell"
    return Path(words[0]).name


def ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_recv(rfile):
    """(opcode, payload) of the next client frame; None when the peer is gone."""
    try:
        return _ws_recv(rfile)
    except (OSError, struct.error):
        return None


def _ws_recv(rfile):
    data, first_op = b"", None
    while True:
        head = rfile.read(2)
        if len(head) < 2:
            return None
        fin, op, masked, size = (
            head[0] & 0x80,
            head[0] & 0x0F,
            head[1] & 0x80,
            head[1] & 0x7F,
        )
        if size == 126:
            size = struct.unpack(">H", rfile.read(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", rfile.read(8))[0]
        mask = rfile.read(4) if masked else b""
        raw = rfile.read(size)
        if len(raw) < size:
            return None
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        else:
            payload = raw
        if op >= 8:
            return op, payload
        first_op = op if first_op is None else first_op
        data += payload
        if fin:
            return first_op, data


def ws_frame(op: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 126:
        head = struct.pack(">BB", 0x80 | op, size)
    elif size < 65536:
        head = struct.pack(">BBH", 0x80 | op, 126, size)
    else:
        head = struct.pack(">BBQ", 0x80 | op, 127, size)
    return head + payload


class Client:
    def __init__(self, sock):
        self.sock, self.lock = sock, threading.Lock()

    def send(self, op: int, payload: bytes) -> bool:
        try:
            with self.lock:
                self.sock.sendall(ws_frame(op, payload))
            return True
        except OSError:
            return False


class Terminal:
    def __init__(self, cwd: str, env: dict, agent: str = "", on_change=None):
        self.cwd, self.env, self.agent = cwd, env, agent
        self.on_change = on_change
        self.lock = threading.Lock()
        self.clients: set = set()
        self.buf = bytearray()
        self.proc = None
        self.master = None
        self.size = (24, 80)
        self.gen = 0

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def label(self) -> str:
        if self.agent:
            return agent_name(self.agent)
        return os.path.basename(self.env.get("SHELL") or "sh")

    def start(self) -> None:
        with self.lock:
            if self.alive:
                return
            shell = self.env.get("SHELL") or "/bin/sh"
            master, slave = os.openpty()
            self._winsize(master)

            def ctty():
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            self.proc = subprocess.Popen(
                [shell, "-i"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.cwd,
                env=self.env,
                start_new_session=True,
                preexec_fn=ctty,
                close_fds=True,
            )
            os.close(slave)
            self.master = master
            self.gen += 1
            self.buf.clear()
            for client in list(self.clients):
                client.send(1, json.dumps({"t": "reset"}).encode())
            threading.Thread(
                target=self._pump, args=(master, self.gen), daemon=True
            ).start()
            if self.agent:
                self.write((self.agent + "\r").encode())
        if self.on_change:
            self.on_change()

    def _winsize(self, fd: int) -> None:
        rows, cols = self.size
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)

    def _pump(self, master: int, gen: int) -> None:
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            self._broadcast(chunk)
        try:
            os.close(master)
        except OSError:
            pass
        if gen != self.gen:
            return
        self.master = None
        self._broadcast(
            b"\r\n\x1b[2m[session ended - Restart to start a new one]\x1b[0m\r\n"
        )
        for client in list(self.clients):
            client.send(1, json.dumps({"t": "exit"}).encode())
        if self.on_change:
            self.on_change()

    def _broadcast(self, chunk: bytes) -> None:
        with self.lock:
            self.buf += chunk
            if len(self.buf) > SCROLLBACK:
                del self.buf[: len(self.buf) - SCROLLBACK]
            clients = list(self.clients)
        for client in clients:
            if not client.send(2, chunk):
                self.clients.discard(client)

    def write(self, data: bytes) -> None:
        if self.master is None:
            return
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        self.size = (max(2, rows), max(10, cols))
        if self.master is None:
            return
        try:
            self._winsize(self.master)
        except OSError:
            pass

    def stop(self) -> None:
        if not self.alive:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def restart(self) -> None:
        self.gen += 1
        self.stop()
        self.start()

    def serve(self, sock, rfile) -> None:
        """Replay scrollback, then relay input and resizes for one connection."""
        client = Client(sock)
        with self.lock:
            backlog = bytes(self.buf)
            self.clients.add(client)
        client.send(1, json.dumps({"t": "hello", "label": self.label()}).encode())
        if backlog:
            client.send(2, backlog)
        if not self.alive:
            self.start()
        try:
            while True:
                message = ws_recv(rfile)
                if message is None:
                    break
                op, payload = message
                if op == 8:
                    client.send(8, b"")
                    break
                if op == 9:
                    client.send(10, payload)
                    continue
                if op not in (1, 2):
                    continue
                try:
                    body = json.loads(payload)
                except ValueError:
                    continue
                kind = body.get("t")
                if kind == "in":
                    text = str(body.get("d", ""))
                    self.write(text.encode("utf-8", "surrogateescape"))
                elif kind == "resize":
                    self.resize(int(body.get("rows", 24)), int(body.get("cols", 80)))
                elif kind == "restart":
                    self.restart()
        finally:
            self.clients.discard(client)


class Sessions:
    """Terminals for one project folder, in the order they were added."""

    def __init__(self, cwd: str, env: dict, on_change=None):
        self.cwd, self.env = cwd, env
        self.on_change = on_change
        self.lock = threading.Lock()
        self.terms: dict = {}
        self.next = 1

    def add(self, agent: str = "") -> str:
        with self.lock:
            sid = str(self.next)
            self.next += 1
            self.terms[sid] = Terminal(
                self.cwd, self.env, agent.strip(), self.on_change
            )
        if self.on_change:
            self.on_change()
        return sid

    def get(self, sid: str):
        return self.terms.get(sid)

    def close(self, sid: str) -> bool:
        with self.lock:
            terminal = self.terms.pop(sid, None)
        if terminal is None:
            return False
        terminal.stop()
        for client in list(terminal.clients):
            client.send(1, json.dumps({"t": "closed"}).encode())
            client.send(8, b"")
        if self.on_change:
            self.on_change()
        return True

    def list(self) -> list:
        return [
            {
                "id": sid,
                "label": terminal.label(),
                "agent": terminal.agent,
                "alive": terminal.alive,
            }
            for sid, terminal in list(self.terms.items())
        ]

    def stop(self) -> None:
        for terminal in list(self.terms.values()):
            terminal.stop()
