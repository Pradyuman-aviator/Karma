"""Measure whether Karma is safe on *your* project before you rely on it.

For a sample of source modules, this breaks every function in the module (the module
stays importable), runs your FULL test suite to find the test files that really fail,
and checks that Karma's selection for that one-file change includes all of them.

    python scripts/safety_check.py --package myproject --samples 20

With ``--granularity function`` it breaks one function or method at a time instead, and
checks every failing *test* against Karma's function-level selection.

Run it from your project's root, in the environment you test with (Karma and pytest
installed). It needs a clean git working tree and restores every file it touches.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Union

Function = Union[ast.FunctionDef, ast.AsyncFunctionDef]
#: the "item" of a failure that belongs to a whole module (a collection error)
MODULE = "<module>"


@dataclass(frozen=True)
class Outcome:
    module: str
    failing: frozenset[str]
    selected: frozenset[str]
    total: int

    @property
    def missed(self) -> frozenset[str]:
        return self.failing - self.selected


@dataclass(frozen=True)
class ItemOutcome:
    mutant: str
    #: failing tests, as (file, top-level item) pairs
    failing: frozenset[tuple[str, str]]
    #: pytest targets Karma selected: "file" or "file::item"
    targets: frozenset[str]
    selected_cases: int
    total_cases: int

    @property
    def missed(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (file, item) for file, item in self.failing if not self._covered(file, item)
        )

    def _covered(self, file: str, item: str) -> bool:
        if file in self.targets or f"{file}::{item}" in self.targets:
            return True
        # The module itself failed (an error on import): running any of its tests shows it.
        return item == MODULE and any(t.startswith(f"{file}::") for t in self.targets)


def mutate(source: str) -> str | None:
    """Insert ``raise RuntimeError`` as the first statement of every function body."""
    tree = ast.parse(source)
    inserts = [
        insert
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for insert in [_insertion(node)]
        if insert is not None
    ]
    return _apply(source, inserts) if inserts else None


def functions(source: str) -> list[tuple[str, Function]]:
    """Top-level functions and the methods of top-level classes, with qualified names."""
    found: list[tuple[str, Function]] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((f"{node.name}.{member.name}", member))
    return found


def mutate_one(source: str, target: Function) -> str | None:
    """Insert ``raise RuntimeError`` as the first statement of one function."""
    insert = _insertion(target)
    return _apply(source, [insert]) if insert is not None else None


def _insertion(node: Function) -> tuple[int, str] | None:
    body = node.body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if len(body) == 1:
            return None
        first = body[1]
    if first.lineno == node.lineno:  # one-line `def f(): ...`
        return None
    indent = " " * first.col_offset
    return first.lineno - 1, f"{indent}raise RuntimeError('karma-safety-check')\n"


def _apply(source: str, inserts: list[tuple[int, str]]) -> str:
    lines = source.splitlines(keepends=True)
    for index, text in sorted(inserts, reverse=True):
        lines.insert(index, text)
    return "".join(lines)


class Project:
    def __init__(self, root: Path, tests: str, python: str) -> None:
        self.root = root
        self.tests = tests
        self.python = python

    def failing_test_files(self) -> frozenset[str]:
        return frozenset(file for file, _, failed in self.cases() if failed)

    def cases(self) -> list[tuple[str, str, bool]]:
        """``(file, top-level item, failed)`` for every test case of the full suite."""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.xml"
            subprocess.run(
                [
                    self.python,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "junit_family=xunit1",
                    f"--junitxml={report}",
                    self.tests,
                ],
                cwd=self.root,
                capture_output=True,
                check=False,
            )
            if not report.exists():
                sys.exit("pytest did not produce a report; run it manually to see why")
            found = []
            for case in ET.parse(report).getroot().iter("testcase"):
                file = self._from_rootdir((case.get("file") or "").replace("\\", "/"))
                failed = any(child.tag in ("failure", "error") for child in case)
                found.append((file, _item(file, case), failed))
            return found

    def _from_rootdir(self, path: str) -> str:
        # pytest reports paths relative to its rootdir, which may be the tests directory.
        for prefix in ("", f"{self.tests.rstrip('/')}/"):
            if (self.root / (prefix + path)).is_file():
                return prefix + path
        return path

    def karma_json(self, module: str, granularity: str = "file") -> str:
        """``karma select --format json`` for a change to ``module``."""
        command = ["karma", "-q", "select", "--files", module, "--format", "json"]
        return subprocess.run(
            [self.python, "-m", *command, "--granularity", granularity],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def karma_selection(self, module: str) -> tuple[frozenset[str], int]:
        data = json.loads(self.karma_json(module))
        return frozenset(data["tests"]), int(data["total"])


def _item(file: str, case: ET.Element) -> str:
    """The top-level test item (function or class) of a JUnit xunit1 test case."""
    name = (case.get("name") or "").partition("[")[0]
    classname = case.get("classname") or ""
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file
    if not classname and (name == module or module.endswith(f".{name}")):
        return MODULE  # pytest reports a collection error as a case named after the module
    # classname is the module's dotted path (relative to pytest's rootdir), plus the
    # class for a method: "tests.test_x.TestA" or "test_x.TestA".
    parts = classname.split(".")
    tail = module.split(".")
    for size in range(len(tail), 0, -1):
        suffix = tail[-size:]
        for start in range(len(parts) - size + 1):
            if parts[start : start + size] == suffix:
                rest = parts[start + size :]
                return rest[0] if rest else name
    return name


def check_functions(project: Project, modules: list[str], samples: int, seed: int) -> int:
    """Break one function at a time; every failing test must be among the selected."""
    root = project.root
    started = time.monotonic()
    baseline = project.cases()
    already = {(file, item) for file, item, failed in baseline if failed}
    if already:
        print(f"ignoring {len(already)} test(s) that already fail")
    per_target: dict[str, int] = {}
    for file, item, _ in baseline:
        for target in (file, f"{file}::{item}"):
            per_target[target] = per_target.get(target, 0) + 1
    candidates = [(m, name) for m in modules for name, _ in functions(_read(root / m))]
    random.Random(seed).shuffle(candidates)

    outcomes: list[ItemOutcome] = []
    for module, name in candidates:
        if len(outcomes) >= samples:
            break
        path = root / module
        original = path.read_bytes()
        source = original.decode("utf-8")
        mutated = mutate_one(source, dict(functions(source))[name])
        if mutated is None:
            continue
        try:
            path.write_text(mutated, encoding="utf-8")
            failing = {(f, i) for f, i, failed in project.cases() if failed} - already
            data = json.loads(project.karma_json(module, "function"))
        finally:
            path.write_bytes(original)
        if not failing:
            continue  # no test exercises this function: nothing to verify
        targets = frozenset(data["targets"])
        selected = sum(per_target.get(t, 0) for t in targets)
        outcome = ItemOutcome(
            f"{module}::{name}", frozenset(failing), targets, selected, len(baseline)
        )
        outcomes.append(outcome)
        verdict = "safe" if not outcome.missed else f"MISSED {sorted(outcome.missed)}"
        print(
            f"{outcome.mutant:<60} {len(failing):>4} failing, "
            f"{selected:>5}/{len(baseline)} tests selected  {verdict}",
            flush=True,
        )

    if not outcomes:
        print("no function could be verified (no test failed for any mutant)")
        return 1
    safe = sum(1 for o in outcomes if not o.missed)
    skipped = sum(1 - o.selected_cases / o.total_cases for o in outcomes) / len(outcomes)
    print(f"\n{safe}/{len(outcomes)} mutants: every failing test was selected")
    print(f"average share of tests Karma would skip: {skipped:.1%}")
    print(f"took {time.monotonic() - started:.0f}s")
    return 0 if safe == len(outcomes) else 1


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def working_tree_is_clean(root: Path) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return status.returncode == 0 and not status.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--package", required=True, help="source directory to mutate, e.g. src/app")
    parser.add_argument("--tests", default="tests", help="test directory (default: tests)")
    parser.add_argument("--samples", type=int, default=20, help="mutants to try (default: 20)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--granularity",
        choices=("file", "function"),
        default="file",
        help="file: break whole modules and check test files (default); function: break "
        "one function at a time and check each failing test",
    )
    args = parser.parse_args()

    root = Path.cwd()
    if not working_tree_is_clean(root):
        print("refusing to run: commit or stash your changes first", file=sys.stderr)
        return 2
    project = Project(root, args.tests, sys.executable)
    if args.granularity == "function":
        sources = sorted(
            p.relative_to(root).as_posix()
            for p in (root / args.package).rglob("*.py")
            if p.name not in ("__main__.py", "conftest.py")
        )
        return check_functions(project, sources, args.samples, args.seed)

    started = time.monotonic()
    baseline = project.failing_test_files()
    if baseline:
        print(f"ignoring {len(baseline)} test file(s) that already fail: {sorted(baseline)}")
    modules = sorted(
        p.relative_to(root).as_posix()
        for p in (root / args.package).rglob("*.py")
        if p.name not in ("__init__.py", "__main__.py", "conftest.py")
    )
    random.Random(args.seed).shuffle(modules)

    outcomes: list[Outcome] = []
    for module in modules:
        if len(outcomes) >= args.samples:
            break
        path = root / module
        original = path.read_bytes()
        mutated = mutate(original.decode("utf-8"))
        if mutated is None:
            continue
        try:
            path.write_text(mutated, encoding="utf-8")
            failing = project.failing_test_files() - baseline
            selected, total = project.karma_selection(module)
        finally:
            path.write_bytes(original)
        if not failing:
            continue  # no test exercises this module: nothing to verify
        outcome = Outcome(module, failing, selected, total)
        outcomes.append(outcome)
        verdict = "safe" if not outcome.missed else f"MISSED {sorted(outcome.missed)}"
        print(
            f"{module:<50} {len(failing):>4} failing, "
            f"{len(selected):>4}/{total} selected  {verdict}",
            flush=True,
        )

    if not outcomes:
        print("no module could be verified (no test failed for any mutant)")
        return 1
    safe = sum(1 for o in outcomes if not o.missed)
    skipped = sum(1 - len(o.selected) / o.total for o in outcomes) / len(outcomes)
    print(f"\n{safe}/{len(outcomes)} mutants: every failing test file was selected")
    print(f"average share of test files Karma would skip: {skipped:.1%}")
    print(f"took {time.monotonic() - started:.0f}s")
    return 0 if safe == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
