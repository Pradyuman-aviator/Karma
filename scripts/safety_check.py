"""Measure whether Karma is safe on *your* project before you rely on it.

For a sample of source modules, this breaks every function in the module (the module
stays importable), runs your FULL test suite to find the test files that really fail,
and checks that Karma's selection for that one-file change includes all of them.

    python scripts/safety_check.py --package myproject --samples 20

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


@dataclass(frozen=True)
class Outcome:
    module: str
    failing: frozenset[str]
    selected: frozenset[str]
    total: int

    @property
    def missed(self) -> frozenset[str]:
        return self.failing - self.selected


def mutate(source: str) -> str | None:
    """Insert ``raise RuntimeError`` as the first statement of every function body."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    inserts: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        first = body[0]
        is_docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
        if is_docstring:
            if len(body) == 1:
                continue
            first = body[1]
        if first.lineno == node.lineno:  # one-line `def f(): ...`
            continue
        indent = " " * first.col_offset
        inserts.append((first.lineno - 1, f"{indent}raise RuntimeError('karma-safety-check')\n"))
    for index, text in sorted(inserts, reverse=True):
        lines.insert(index, text)
    return "".join(lines) if inserts else None


class Project:
    def __init__(self, root: Path, tests: str, python: str) -> None:
        self.root = root
        self.tests = tests
        self.python = python

    def failing_test_files(self) -> frozenset[str]:
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
            failing = set()
            for case in ET.parse(report).getroot().iter("testcase"):
                if any(child.tag in ("failure", "error") for child in case):
                    failing.add(self._from_rootdir((case.get("file") or "").replace("\\", "/")))
            return frozenset(failing)

    def _from_rootdir(self, path: str) -> str:
        # pytest reports paths relative to its rootdir, which may be the tests directory.
        for prefix in ("", f"{self.tests.rstrip('/')}/"):
            if (self.root / (prefix + path)).is_file():
                return prefix + path
        return path

    def karma_selection(self, module: str) -> tuple[frozenset[str], int]:
        proc = subprocess.run(
            [self.python, "-m", "karma", "-q", "select", "--files", module, "--format", "json"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(proc.stdout)
        return frozenset(data["tests"]), int(data["total"])


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
    parser.add_argument("--samples", type=int, default=20, help="modules to try (default: 20)")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    root = Path.cwd()
    if not working_tree_is_clean(root):
        print("refusing to run: commit or stash your changes first", file=sys.stderr)
        return 2
    project = Project(root, args.tests, sys.executable)

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
