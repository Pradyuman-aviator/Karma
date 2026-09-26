"""Shared test helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
