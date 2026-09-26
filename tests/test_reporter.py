from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from karma import reporter
from karma.runner import Outcome, RunResult, TestCase
from karma.selector import Selection

SELECTION = Selection(
    tests=("tests/test_a.py", "tests/test_b.py"),
    total_tests=8,
    changed=("app/core.py", "README.md"),
    reasons={
        "tests/test_a.py": ("tests/test_a.py", "app/core.py"),
        "tests/test_b.py": ("tests/test_b.py",),
    },
    no_impact=("README.md",),
)
RUN_ALL = Selection(tests=("t.py",), total_tests=1, run_all_reason="pyproject.toml changed")
FAILED = TestCase(
    "tests/test_a.py::test_x",
    Outcome.FAILED,
    message="assert <1> == 2",
    details="E  assert <1> == 2\nmore",
    file="tests/test_a.py",
    line=7,
)
RESULT = RunResult(
    exit_code=1,
    cases=(TestCase("tests/test_b.py::test_ok", Outcome.PASSED), FAILED),
    duration=1.25,
)


class TestConsole:
    def test_describe_selection(self) -> None:
        assert reporter.describe_selection(SELECTION) == (
            "2 of 8 test files selected (75.0% skipped) for 2 changed files"
        )
        assert reporter.describe_selection(RUN_ALL) == (
            "running all 1 test file: pyproject.toml changed"
        )

    def test_explanation_ascii_fallback(self) -> None:
        stream = io.StringIO()  # no encoding -> ASCII-only
        text = reporter.format_explanation(SELECTION, stream)
        assert text == (
            "tests/test_a.py\n"
            "    <- app/core.py  (changed)\n"
            "tests/test_b.py\n"
            "    (changed)\n"
            "No test depends on: README.md\n"
        )

    def test_explanation_unicode(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        assert "← app/core.py" in reporter.format_explanation(SELECTION, stream)

    def test_explanation_edge_cases(self) -> None:
        stream = io.StringIO()
        assert reporter.format_explanation(RUN_ALL, stream).startswith("All tests selected")
        empty = Selection(tests=(), total_tests=3)
        assert reporter.format_explanation(empty, stream) == "No tests selected.\n"

    def test_run_summary(self) -> None:
        stream = io.StringIO()
        reporter.print_run_summary(RESULT, stream)
        text = stream.getvalue()
        assert re.search(r"passed\s+1", text)
        assert re.search(r"failed\s+1", text)
        assert "FAILED tests/test_a.py::test_x - assert <1> == 2" in text

    def test_run_summary_for_a_crash(self) -> None:
        stream = io.StringIO()
        reporter.print_run_summary(RunResult(exit_code=4, crashed=True), stream)
        assert "without producing results" in stream.getvalue()


class TestGitHub:
    def test_outputs_use_multiline_safe_delimiters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))

        reporter.write_github_outputs({"a": "one two", "b": "line1\nline2"})

        pairs = re.findall(r"(\w+)<<(\S+)\n(.*?)\n\2\n", out.read_text(encoding="utf-8"), re.S)
        assert [(name, value) for name, _, value in pairs] == [
            ("a", "one two"),
            ("b", "line1\nline2"),
        ]

    def test_outputs_and_summary_are_no_ops_outside_actions(self, tmp_path: Path) -> None:
        reporter.write_github_outputs({"a": "b"})
        reporter.append_step_summary("text")
        assert list(tmp_path.iterdir()) == []

    def test_step_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        reporter.append_step_summary("one\n")
        reporter.append_step_summary("two\n")
        assert summary.read_text(encoding="utf-8") == "one\ntwo\n"

    def test_annotations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
        stream = io.StringIO()
        no_file = TestCase("x::y", Outcome.ERROR)

        reporter.emit_annotations([FAILED, no_file], tmp_path / "service", stream)

        first, second = stream.getvalue().splitlines()
        assert first == (
            "::error file=service/tests/test_a.py,line=7,"
            "title=tests/test_a.py%3A%3Atest_x failed::E  assert <1> == 2%0Amore"
        )
        assert second == "::error title=x%3A%3Ay error::x::y error"

    def test_selection_markdown(self) -> None:
        text = reporter.selection_markdown(SELECTION, base="origin/main")
        assert "**2 of 8** test files selected (**75.0%** skipped)" in text
        assert "against <code>origin/main</code>" in text
        assert "| <code>tests/test_a.py</code> | <code>app/core.py</code> |" in text
        assert "| <code>tests/test_b.py</code> | changed directly |" in text
        assert "No test depends on: <code>README.md</code>" in text
        assert "Running **all 1** test files" in reporter.selection_markdown(RUN_ALL)

    def test_run_markdown_escapes_failure_output(self) -> None:
        text = reporter.run_markdown(SELECTION, RESULT)
        assert text.startswith("## ⚡ Karma: ❌ 1 test failed")
        assert "<pre>E  assert &lt;1&gt; == 2\nmore</pre>" in text
        assert "| ❌ Failed | 1 |" in text

    def test_run_markdown_variants(self) -> None:
        assert "no tests affected" in reporter.run_markdown(SELECTION, None)
        passed = RunResult(exit_code=0, cases=(TestCase("a", Outcome.PASSED),))
        assert "✅ 1 test passed" in reporter.run_markdown(SELECTION, passed)
        crashed = reporter.run_markdown(SELECTION, RunResult(exit_code=4, crashed=True))
        assert "exited with code 4" in crashed

    def test_long_lists_are_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reporter, "MAX_SUMMARY_ROWS", 1)
        many = RunResult(exit_code=1, cases=(FAILED, FAILED))
        text = reporter.run_markdown(SELECTION, many)
        assert "… and 1 more |" in text
        assert "… and 1 more." in text


def test_plural() -> None:
    assert reporter.plural(1, "file") == "1 file"
    assert reporter.plural(0, "file") == "0 files"
