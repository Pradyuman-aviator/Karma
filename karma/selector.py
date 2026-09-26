"""Decide which tests a change can affect."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from karma.config import Config
from karma.git import ChangeSet
from karma.graph import DependencyGraph, matches_any

_NOT_TESTS = frozenset({"conftest.py", "__init__.py"})


def is_test_file(path: str, patterns: Iterable[str]) -> bool:
    """Whether pytest would collect ``path`` as a test module."""
    name = path.rpartition("/")[2]
    return name.endswith(".py") and name not in _NOT_TESTS and bool(matches_any(path, patterns))


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

    Besides imports this follows pytest's own rules (a ``conftest.py`` applies to every
    test beneath its directory), the configured ``mappings`` for dependencies imports
    cannot express, and ``run-all-on`` triggers that make the whole suite run.
    """
    all_tests = sorted(f for f in graph.files if is_test_file(f, config.test_patterns))
    changed = changes.all

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

    def __call__(self, node: str) -> list[str]:
        found = set(self._graph.dependents(node))
        directory, _, name = node.rpartition("/")
        if name == "conftest.py":
            # A conftest.py applies to every test in its directory and below.
            prefix = f"{directory}/" if directory else ""
            found.update(t for t in self._all_tests if t.startswith(prefix))
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
