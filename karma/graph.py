"""The repository's file-level dependency graph."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from karma.cache import ImportCache
from karma.git import list_files
from karma.languages.python import ImportRef, ModuleResolver, parse_file

log = logging.getLogger(__name__)


class DependencyGraph:
    """``edges[a]`` is the set of repository files that ``a`` imports."""

    def __init__(self, edges: Mapping[str, Iterable[str]]) -> None:
        self.edges: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in edges.items()}
        self._reverse: dict[str, set[str]] = {}
        for source, targets in self.edges.items():
            self._reverse.setdefault(source, set())
            for target in targets:
                self._reverse.setdefault(target, set()).add(source)

    def __repr__(self) -> str:
        return f"DependencyGraph({len(self.edges)} files)"

    @property
    def files(self) -> frozenset[str]:
        """Every analysed file (graph nodes that exist in the working tree)."""
        return frozenset(self.edges)

    def dependencies(self, path: str) -> frozenset[str]:
        return self.edges.get(path, frozenset())

    def dependents(self, path: str) -> frozenset[str]:
        """Files that import ``path`` directly."""
        return frozenset(self._reverse.get(path, ()))

    # ------------------------------------------------------------------ export
    def to_json(self) -> str:
        return json.dumps({k: sorted(v) for k, v in sorted(self.edges.items())}, indent=2)

    def to_dot(self) -> str:
        lines = ["digraph karma {", "  rankdir=LR;", '  node [shape=box, fontname="monospace"];']
        for source in sorted(self.edges):
            lines.append(f"  {json.dumps(source)};")
            lines.extend(
                f"  {json.dumps(source)} -> {json.dumps(target)};"
                for target in sorted(self.edges[source])
            )
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_mermaid(self) -> str:
        ids = {path: f"n{i}" for i, path in enumerate(sorted(self._reverse))}
        lines = ["graph LR"]
        lines.extend(f'  {node}["{path}"]' for path, node in ids.items())
        for source in sorted(self.edges):
            lines.extend(f"  {ids[source]} --> {ids[t]}" for t in sorted(self.edges[source]))
        return "\n".join(lines) + "\n"


def matches_any(path: str, patterns: Iterable[str], *, case_sensitive: bool = True) -> str | None:
    """Return the first glob in ``patterns`` matching ``path``, or ``None``.

    Patterns containing ``/`` match the whole repository-relative path; others match
    only the file name, so ``conftest.py`` matches ``tests/unit/conftest.py``.
    """
    name = path.rpartition("/")[2]
    for pattern in patterns:
        target = path if "/" in pattern else name
        if not case_sensitive:
            target, pattern_cmp = target.lower(), pattern.lower()
        else:
            pattern_cmp = pattern
        if fnmatch.fnmatchcase(target, pattern_cmp):
            return pattern
    return None


def build_graph(
    root: Path,
    *,
    source_roots: Sequence[str] = ("", "src"),
    deleted: Iterable[str] = (),
    cache: ImportCache | None = None,
    jobs: int | None = None,
    doctest_modules: bool = False,
    doctest_globs: Sequence[str] = (),
) -> DependencyGraph:
    """Scan every Python file under ``root`` and resolve its imports.

    ``deleted`` files are known to the resolver even though they no longer exist, so
    files that still import a deleted module keep an edge to it and get re-tested.
    Text files matching ``doctest_globs`` become nodes too, with the imports of their
    ``>>>`` examples; with ``doctest_modules``, docstring examples count as imports.
    Files missing from ``cache`` are parsed in ``jobs`` worker processes (default:
    one per CPU) when there are enough of them to be worth it.
    """
    files = list_files(root)
    if doctest_globs:
        files += [f for f in list_files(root, doctest_globs) if not f.endswith(".py")]
    store = cache if cache is not None else ImportCache(None)

    imports: dict[str, tuple[ImportRef, ...]] = {}
    pending: list[tuple[str, bytes]] = []
    for rel in files:
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            log.warning("cannot read %s: %s", rel, exc)
            data = b""
        refs = store.lookup(rel, data)
        if refs is None:
            pending.append((rel, data))
        else:
            imports[rel] = refs
    for (rel, data), refs in zip(pending, _parse_all(pending, jobs)):
        store.store(rel, data, refs)
        imports[rel] = refs

    resolver = ModuleResolver([*files, *deleted], source_roots, doctests=doctest_modules)
    edges = {rel: resolver.resolve(rel, imports[rel]) for rel in files}

    log.debug("parsed %d files, %d from cache", store.misses, store.hits)
    store.prune(files)
    store.save()
    return DependencyGraph(edges)


# Below this many files, starting worker processes costs more than it saves.
PARALLEL_THRESHOLD = 256


def _parse_all(
    pending: Sequence[tuple[str, bytes]], jobs: int | None
) -> list[tuple[ImportRef, ...]]:
    workers = min(jobs or os.cpu_count() or 1, 32)
    if workers > 1 and len(pending) >= PARALLEL_THRESHOLD:
        names = [rel for rel, _ in pending]
        blobs = [data for _, data in pending]
        chunk = max(1, len(pending) // (workers * 4))
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(parse_file, blobs, names, chunksize=chunk))
        except (OSError, RuntimeError, BrokenProcessPool) as exc:
            # Some sandboxes forbid subprocesses or shared memory; parse serially instead.
            log.debug("parallel parsing unavailable (%s); parsing serially", exc)
    return [parse_file(data, rel) for rel, data in pending]
