"""Decide which tests a change can affect."""

from __future__ import annotations

import fnmatch
import sys
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from karma.config import Config
from karma.git import ChangeSet
from karma.graph import DependencyGraph, matches_any
from karma.languages.python import ImportRef, ModuleResolver

_NOT_TESTS = frozenset({"conftest.py", "__init__.py"})
_NOT_DOCTESTS = frozenset({"conftest.py", "setup.py", "__main__.py"})
# Files that apply to every test beneath their directory: pytest loads conftest.py
# implicitly, and imports a test package's __init__.py before its test modules.
_DIRECTORY_SCOPED = frozenset({"conftest.py", "__init__.py"})
# pytest matches python_files case-insensitively on Windows (fnmatch uses normcase).
_CASE_SENSITIVE = sys.platform != "win32"


def is_test_file(path: str, config: Config) -> bool:
    """Whether a full pytest run would collect ``path``."""
    name = path.rpartition("/")[2]
    if not name.endswith(".py") or not _in_test_scope(path, config):
        return False
    if config.doctest_modules and name not in _NOT_DOCTESTS:
        return True  # every module is collected for its doctests
    return name not in _NOT_TESTS and bool(
        matches_any(path, config.test_patterns, case_sensitive=_CASE_SENSITIVE)
    )


def _in_test_scope(path: str, config: Config) -> bool:
    """Apply ``testpaths`` and ``norecursedirs``, which bound a full pytest run."""
    directories = path.split("/")[:-1]
    if any(fnmatch.fnmatch(d, p) for d in directories for p in config.norecursedirs):
        return False
    if not config.testpaths:
        return True
    return any(_under(path, root) for root in config.testpaths)


def _under(path: str, root: str) -> bool:
    if not root:
        return True
    if any(c in root for c in "*?["):  # testpaths may be globs
        return fnmatch.fnmatchcase(path, root) or fnmatch.fnmatchcase(path, f"{root}/*")
    return path == root or path.startswith(f"{root}/")


@dataclass(frozen=True)
class Selection:
    """The outcome of test selection, with the reason each test was chosen."""

    tests: tuple[str, ...]
    total_tests: int
    changed: tuple[str, ...] = ()
    #: test -> (test, file it depends on, ..., changed file)
    reasons: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: set when a change forced the whole suite to run
    run_all_reason: str | None = None
    #: changed files that no test depends on (docs, assets, ...)
    no_impact: tuple[str, ...] = ()

    @property
    def run_all(self) -> bool:
        return self.run_all_reason is not None

    @property
    def skipped_fraction(self) -> float:
        if not self.total_tests:
            return 0.0
        return 1 - len(self.tests) / self.total_tests

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests": list(self.tests),
            "selected": len(self.tests),
            "total": self.total_tests,
            "run_all": self.run_all,
            "run_all_reason": self.run_all_reason,
            "changed": list(self.changed),
            "no_impact": list(self.no_impact),
            "reasons": {test: list(chain) for test, chain in self.reasons.items()},
        }


def select_tests(changes: ChangeSet, graph: DependencyGraph, config: Config) -> Selection:
    """Select every test that transitively depends on a changed file.

    Besides imports this follows pytest's own rules: a ``conftest.py`` or test-package
    ``__init__.py`` applies to every test beneath its directory, plugins loaded with
    ``-p`` apply to every test, and doctest options make modules and text files tests.
    It also applies the configured ``mappings`` for dependencies imports cannot express,
    and the ``run-all-on`` triggers that make the whole suite run.
    """
    all_tests = sorted(f for f in graph.files if is_test_file(f, config))
    changed = changes.all

    for path in changes.submodules:
        return Selection(
            tests=tuple(all_tests),
            total_tests=len(all_tests),
            changed=changed,
            run_all_reason=f"git submodule {path} changed; its files are not analysed",
        )

    for path in changed:
        pattern = matches_any(path, config.run_all_on)
        if pattern is not None:
            return Selection(
                tests=tuple(all_tests),
                total_tests=len(all_tests),
                changed=changed,
                run_all_reason=f"{path} changed (matches run-all-on pattern {pattern!r})",
            )

    test_set = frozenset(all_tests)
    affected = _Affected(graph, config, all_tests)

    # Breadth-first search from every changed file. `parent` doubles as the visited set
    # and records, for each reached file, the file that led to it.
    parent: dict[str, str | None] = dict.fromkeys(changed)
    queue: deque[str] = deque(changed)
    while queue:
        node = queue.popleft()
        for neighbour in affected(node):
            if neighbour not in parent:
                parent[neighbour] = node
                queue.append(neighbour)

    # Text files collected for doctests (--doctest-glob) are tests of their own.
    for path in changes.modified:
        if config.doctest_globs and matches_any(path, config.doctest_globs):
            parent.setdefault(path, None)
            test_set |= {path}

    selected = tuple(sorted(f for f in parent if f in test_set))
    return Selection(
        tests=selected,
        total_tests=len(all_tests),
        changed=changed,
        reasons={test: _chain(test, parent) for test in selected},
        no_impact=tuple(p for p in changed if not _reaches_test(p, affected, test_set)),
    )


class _Affected:
    """``affected(path)``: the files directly affected when ``path`` changes."""

    def __init__(self, graph: DependencyGraph, config: Config, all_tests: list[str]) -> None:
        self._graph = graph
        self._config = config
        self._all_tests = all_tests
        self._mapped: dict[str, tuple[str, ...]] = {}
        # Modules loaded as pytest plugins (-p NAME, pytest11 entry points) apply to
        # every test, like a conftest.py at the root.
        resolver = ModuleResolver(graph.files, config.source_roots)
        self._plugins = resolver.resolve("conftest.py", [ImportRef(p) for p in config.plugins])

    def __call__(self, node: str) -> list[str]:
        found = set(self._graph.dependents(node))
        directory, _, name = node.rpartition("/")
        if name in _DIRECTORY_SCOPED:
            prefix = f"{directory}/" if directory else ""
            found.update(t for t in self._all_tests if t.startswith(prefix))
        if node in self._plugins:
            found.update(self._all_tests)
        for pattern, targets in self._config.mappings:
            if matches_any(node, [pattern]):
                found.update(self._mapping_targets(pattern, targets))
        found.discard(node)
        return sorted(found)

    def _mapping_targets(self, pattern: str, targets: tuple[str, ...]) -> tuple[str, ...]:
        if pattern not in self._mapped:
            self._mapped[pattern] = tuple(f for f in self._graph.files if matches_any(f, targets))
        return self._mapped[pattern]


def _reaches_test(start: str, affected: _Affected, tests: frozenset[str]) -> bool:
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node in tests:
            return True
        for neighbour in affected(node):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return False


def _chain(node: str, parent: Mapping[str, str | None]) -> tuple[str, ...]:
    chain = [node]
    while (via := parent[chain[-1]]) is not None:
        chain.append(via)
    return tuple(chain)
