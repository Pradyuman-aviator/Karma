"""Shared test helpers."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


@dataclass
class Reply:
    """One canned answer of :class:`FakeAPI`."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


def anthropic_reply(text: str) -> Reply:
    return Reply(body={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})


def openai_reply(text: str) -> Reply:
    return Reply(body={"choices": [{"message": {"role": "assistant", "content": text}}]})


class FakeAPI:
    """A local stand-in for a language model API.

    Answers the queued replies in order (the last one repeats) and records every
    request, with lower-case header names.
    """

    def __init__(self) -> None:
        self.replies: list[Reply] = [Reply(body={})]
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length) or b"null")
                headers = {k.lower(): v for k, v in self.headers.items()}
                with api.lock:
                    api.requests.append({"path": self.path, "headers": headers, "body": body})
                    reply = api.replies.pop(0) if len(api.replies) > 1 else api.replies[0]
                time.sleep(reply.delay)
                raw = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body)
                data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
                self.send_response(reply.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A client that timed out has hung up: nothing to report.
        self.server.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def answer(self, *replies: Reply) -> None:
        self.replies = list(replies)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class GitRepo:
    """A throwaway git repository for integration tests."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            check=True,
            capture_output=True,
            encoding="utf-8",
        ).stdout.strip()

    def write(self, rel: str, content: str = "") -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:  # LF on every OS
            fh.write(content)
        return target

    def delete(self, rel: str) -> None:
        (self.path / rel).unlink()

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def branch(self, name: str) -> None:
        self.git("checkout", "-q", "-b", name)

    def checkout(self, name: str) -> None:
        self.git("checkout", "-q", name)
