"""Persistent per-file cache of parsed imports.

Each entry is keyed by the file's path and a digest of its bytes, so a run only
re-parses the files that actually changed, instead of rebuilding everything whenever
any file changes. Import *resolution* is never cached: it is cheap and depends on
which files exist, which the cache cannot know.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from collections.abc import Collection
from pathlib import Path

from karma import __version__
from karma.languages.python import ImportRef, parse_imports

log = logging.getLogger(__name__)

CACHE_DIR = ".karma_cache"
SCHEMA_VERSION = 2


def _stamp() -> str:
    # Parsing results can differ between Karma versions and Python grammars.
    return f"{SCHEMA_VERSION}/{__version__}/py{sys.version_info[0]}.{sys.version_info[1]}"


def default_cache_path(root: Path) -> Path:
    return root / CACHE_DIR / "imports.json"


def _prepare_directory(directory: Path) -> None:
    """Create the cache directory so that git and backup tools ignore it (like pytest)."""
    if directory.is_dir():
        return
    directory.mkdir()
    (directory / ".gitignore").write_text("# Created by Karma automatically.\n*\n", "utf-8")
    (directory / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n"
        "# This file is a cache directory tag created by Karma.\n",
        "utf-8",
    )


def _digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class ImportCache:
    """Maps ``(path, content digest)`` to the imports parsed from that content."""

    def __init__(self, path: Path | None) -> None:
        """Load the cache stored at ``path``; ``None`` gives an in-memory cache."""
        self.path = path
        self.hits = 0
        self.misses = 0
        self._entries: dict[str, tuple[str, tuple[ImportRef, ...]]] = {}
        self._dirty = False
        if path is not None:
            self._load(path)

    def lookup(self, rel_path: str, data: bytes) -> tuple[ImportRef, ...] | None:
        """Return the cached imports for this exact content, or ``None``."""
        entry = self._entries.get(rel_path)
        if entry is not None and entry[0] == _digest(data):
            self.hits += 1
            return entry[1]
        return None

    def store(self, rel_path: str, data: bytes, refs: tuple[ImportRef, ...]) -> None:
        self.misses += 1
        self._entries[rel_path] = (_digest(data), refs)
        self._dirty = True

    def imports_for(self, rel_path: str, data: bytes) -> tuple[ImportRef, ...]:
        refs = self.lookup(rel_path, data)
        if refs is None:
            refs = parse_imports(data, rel_path)
            self.store(rel_path, data, refs)
        return refs

    def prune(self, keep: Collection[str]) -> None:
        """Forget files that no longer exist."""
        stale = self._entries.keys() - set(keep)
        for rel_path in stale:
            del self._entries[rel_path]
        self._dirty |= bool(stale)

    def save(self) -> None:
        """Write the cache atomically. Failure to write is logged, never fatal."""
        if self.path is None or not self._dirty:
            return
        payload = {
            "version": _stamp(),
            "files": {
                rel: {"digest": digest, "imports": [ref.to_json() for ref in refs]}
                for rel, (digest, refs) in sorted(self._entries.items())
            },
        }
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            _prepare_directory(self.path.parent)
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("could not write cache %s: %s", self.path, exc)
            tmp.unlink(missing_ok=True)
            return
        self._dirty = False
        log.debug("cache saved to %s (%d files)", self.path, len(self._entries))

    def _load(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.debug("ignoring unreadable cache %s: %s", path, exc)
            return
        if not isinstance(payload, dict) or payload.get("version") != _stamp():
            log.debug("ignoring cache %s written by another Karma/Python version", path)
            return
        try:
            files = payload["files"]
            self._entries = {
                rel: (entry["digest"], tuple(ImportRef.from_json(r) for r in entry["imports"]))
                for rel, entry in files.items()
            }
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            log.debug("ignoring malformed cache %s: %s", path, exc)
            self._entries = {}
