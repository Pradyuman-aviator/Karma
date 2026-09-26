from __future__ import annotations

import json
from pathlib import Path

import pytest

from karma import graph as graph_module
from karma.cache import ImportCache
from karma.graph import DependencyGraph, build_graph, matches_any
from tests.helpers import GitRepo


@pytest.fixture
def project(repo: GitRepo) -> GitRepo:
    repo.write("app/__init__.py")
    repo.write("app/core.py", "from app import util\n")
    repo.write("app/util.py", "import os\n")
    repo.write("tests/test_core.py", "from app.core import thing\n")
    repo.write("build/generated.py", "import app.core\n")
    return repo


def test_build_graph_resolves_repository_imports(project: GitRepo) -> None:
    graph = build_graph(project.path)

    assert graph.dependencies("app/core.py") == {"app/__init__.py", "app/util.py"}
    assert graph.dependencies("tests/test_core.py") == {"app/__init__.py", "app/core.py"}
    assert graph.dependents("app/core.py") == {"tests/test_core.py", "build/generated.py"}
    assert graph.dependents("app/util.py") == {"app/core.py"}
    assert graph.dependents("not/in/graph.py") == frozenset()


def test_exclude_patterns(project: GitRepo) -> None:
    graph = build_graph(project.path, exclude=["build/*"])
    assert "build/generated.py" not in graph.files


def test_deleted_modules_keep_their_importers_edges(project: GitRepo) -> None:
    project.delete("app/util.py")

    graph = build_graph(project.path, deleted=["app/util.py"])

    assert "app/util.py" not in graph.files
    assert graph.dependents("app/util.py") == {"app/core.py"}


def test_uses_and_saves_the_cache(project: GitRepo) -> None:
    path = project.path / ".karma_cache.json"
    build_graph(project.path, cache=ImportCache(path))
    assert path.exists()

    warm = ImportCache(path)
    build_graph(project.path, cache=warm)
    assert warm.misses == 0
    assert warm.hits == 5


def test_unreadable_files_are_treated_as_empty(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.name == "core.py":
            raise PermissionError("denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    graph = build_graph(project.path)

    assert graph.dependencies("app/core.py") == frozenset()
    assert "cannot read app/core.py" in caplog.text


class TestExport:
    graph = DependencyGraph({"a.py": {"b.py"}, "b.py": set()})

    def test_json(self) -> None:
        assert json.loads(self.graph.to_json()) == {"a.py": ["b.py"], "b.py": []}

    def test_dot(self) -> None:
        dot = self.graph.to_dot()
        assert dot.startswith("digraph karma {")
        assert '"a.py" -> "b.py";' in dot

    def test_mermaid(self) -> None:
        assert self.graph.to_mermaid() == 'graph LR\n  n0["a.py"]\n  n1["b.py"]\n  n0 --> n1\n'

    def test_repr(self) -> None:
        assert repr(self.graph) == "DependencyGraph(2 files)"


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("tests/unit/conftest.py", ["conftest.py"], "conftest.py"),
        ("requirements-dev.txt", ["requirements*.txt"], "requirements*.txt"),
        ("docs/conf.py", ["build/*", "docs/*"], "docs/*"),
        ("src/docs/conf.py", ["docs/*"], None),
        ("README.md", [], None),
    ],
)
def test_matches_any(path: str, patterns: list[str], expected: str | None) -> None:
    assert matches_any(path, patterns) == expected


class TestParallelParsing:
    def test_matches_serial_results(
        self, project: GitRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph_module, "PARALLEL_THRESHOLD", 1)

        parallel = build_graph(project.path, jobs=2)

        assert parallel.edges == build_graph(project.path, jobs=1).edges

    def test_falls_back_to_serial_when_processes_are_unavailable(
        self, project: GitRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class NoProcesses:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise OSError("sandboxed")

        monkeypatch.setattr(graph_module, "PARALLEL_THRESHOLD", 1)
        monkeypatch.setattr(graph_module, "ProcessPoolExecutor", NoProcesses)

        graph = build_graph(project.path, jobs=4)

        assert graph.dependencies("app/core.py") == {"app/__init__.py", "app/util.py"}
