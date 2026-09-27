from __future__ import annotations

import io
import re
from dataclasses import replace
from pathlib import Path

import pytest

from karma import reporter
from karma.diagnose import Diagnosis, Explanation, Hunk, Suspect
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


def test_risk_in_explanation_and_summary() -> None:
    from karma.risk import Risk

    risks = [
        Risk("tests/test_b.py", 0.6, ("the test itself changed",)),
        Risk("tests/test_a.py", 0.1, ()),
    ]
    text = reporter.format_explanation(SELECTION, io.StringIO(), risks)
    assert text.index("tests/test_b.py  (risk 60%: the test itself changed)") < text.index(
        "tests/test_a.py  (risk 10%)"
    )

    markdown = reporter.selection_markdown(SELECTION, risks=risks)
    assert "Ordered by predicted risk" in markdown
    assert "| Test file | Selected because of | Risk |" in markdown
    assert "| risk 60%: the test itself changed |" in markdown


# --------------------------------------------------------------------------- diagnosis

HUNK = Hunk("app/core.py", 1, 3, "@@ -1,2 +1,3 @@\n import os\n-X = 1\n+X = 2\n+Y = ```3```")
DIAGNOSIS = Diagnosis(
    case=FAILED,
    chain=("tests/test_a.py", "app/core.py"),
    history="first failure in 9 recorded runs",
    suspects=(
        Suspect("app/core.py", 2, "changed; the test imports it", HUNK),
        Suspect("app/extra.py", None, "changed; the test imports it"),
    ),
    same_failure=("tests/test_a.py::test_y",),
)
EXPLANATION = Explanation(
    summary="X changed from 1 to 2.",
    cause="The test expects <1>.",
    fix="Set X = 1.",
    kind="regression",
    confidence="high",
    path="app/core.py",
    line=2,
    model="claude-sonnet-5",
)
EXPLAINED = replace(DIAGNOSIS, explanation=EXPLANATION)


class TestDiagnosisOutput:
    def test_console(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        text = reporter.format_diagnoses([EXPLAINED], stream)
        assert "karma diagnosis" in text
        assert "  error      assert <1> == 2" in text
        assert "  ran for    tests/test_a.py ← app/core.py  (changed)" in text
        assert "  suspect    app/core.py:2  changed; the test imports it" in text
        assert "             +X = 2" in text  # the suspect's diff
        assert "             app/extra.py  changed; the test imports it" in text
        assert "  same       also fails this way: tests/test_a.py::test_y" in text
        assert "  ai         X changed from 1 to 2." in text
        assert "(regression, high confidence, claude-sonnet-5)" in text
        assert "             fix: Set X = 1." in text
        assert "look at app/core.py:2" in text

    def test_console_without_unicode_or_suspects(self) -> None:
        bare = Diagnosis(case=FAILED, chain=("tests/test_a.py", "app/core.py"))
        text = reporter.format_diagnoses([bare], io.StringIO())
        assert "tests/test_a.py <- app/core.py" in text
        assert "-" * 17 + " karma diagnosis" in text
        assert f"suspect    {reporter.NO_SUSPECT}" in text

    def test_long_hunks_are_cut_around_the_suspect(self) -> None:
        body = "\n".join(f" line {n}" for n in range(1, 41))
        hunk = Hunk("a.py", 1, 41, f"@@ -1,40 +1,41 @@\n{body}\n+new line 41")
        excerpt = reporter._hunk_excerpt(hunk, 41)
        assert len(excerpt) == reporter.MAX_HUNK_LINES
        assert excerpt[-1] == "+new line 41"

    def test_markdown(self) -> None:
        text = reporter.diagnosis_markdown([EXPLAINED])
        assert text.startswith("### 🔎 Diagnosis\n")
        assert "**<code>tests/test_a.py::test_x</code>**: assert &lt;1&gt; == 2" in text
        assert "- **Why it ran:** <code>tests/test_a.py</code> ← <code>app/core.py</code>" in text
        assert "- **Suspect:** <code>app/core.py:2</code>: changed; the test imports it" in text
        assert "- **Fails the same way:** <code>tests/test_a.py::test_y</code>" in text
        # The diff holds ``` itself, so its fence is longer.
        assert "````diff\n@@ -1,2 +1,3 @@" in text
        assert "> **🤖 claude-sonnet-5** (regression, high confidence)" in text
        assert "> The test expects &lt;1&gt;." in text
        assert "> Fix: Set X = 1." in text
        assert reporter.diagnosis_markdown([]) == ""

    def test_run_markdown_includes_the_diagnosis(self) -> None:
        text = reporter.run_markdown(SELECTION, RESULT, diagnoses=[DIAGNOSIS])
        assert text.index("### Failures") < text.index("### 🔎 Diagnosis")

    def test_annotations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "core.py").write_text("X = 2\n", encoding="utf-8")
        (tmp_path / "app" / "other.py").write_text("Y = 1\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path.parent))
        prefix = f"{tmp_path.name}/app"

        stream = io.StringIO()
        reporter.emit_diagnosis_annotations([EXPLAINED], tmp_path, stream)
        (notice,) = stream.getvalue().splitlines()  # the AI agrees: one notice, both texts
        assert notice.startswith(f"::notice file={prefix}/core.py,line=2,title=Karma%3A may")
        assert "changed; the test imports it. assert <1> == 2%0AX changed from 1 to 2." in notice

        elsewhere = replace(EXPLANATION, path="app/other.py", line=1)
        stream = io.StringIO()
        reporter.emit_diagnosis_annotations(
            [replace(EXPLAINED, explanation=elsewhere)], tmp_path, stream
        )
        assert [line.split(",")[0] for line in stream.getvalue().splitlines()] == [
            f"::notice file={prefix}/core.py",
            f"::notice file={prefix}/other.py",
        ]

        for path in ("app/missing.py", "../outside.py"):  # a model can name any file
            invented = replace(EXPLANATION, path=path, line=1)
            stream = io.StringIO()
            reporter.emit_diagnosis_annotations(
                [replace(EXPLAINED, explanation=invented)], tmp_path, stream
            )
            assert len(stream.getvalue().splitlines()) == 1

        stream = io.StringIO()
        reporter.emit_diagnosis_annotations([Diagnosis(case=FAILED)], tmp_path, stream)
        assert stream.getvalue() == ""
