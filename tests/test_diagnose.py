"""Failure diagnosis: tracebacks, diffs, suspects and history (karma.diagnose)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from karma.cache import CACHE_DIR
from karma.diagnose import (
    FAILURES_FILE,
    Diagnosis,
    Frame,
    Hunk,
    diagnose,
    infer_test_files,
    load_failures,
    parse_diff,
    relevant_files,
    save_failures,
    to_json,
    traceback_frames,
)
from karma.git import ChangeSet
from karma.graph import DependencyGraph
from karma.history import History, Run, TestRecord
from karma.runner import Outcome, TestCase
from karma.selector import Selection

# Real pytest (xunit1) failure texts, as parsed from its JUnit report on Windows.
ASSERTION = r"""def test_total_with_vat():
>       assert total(100) == 121.0
E       assert 119.0 == 121.0
E        +  where 119.0 = total(100)

tests\test_invoice.py:5: AssertionError"""

RAISED = r"""def test_unknown_country():
>       assert total(100, "XX") == 100
               ^^^^^^^^^^^^^^^^

tests\test_invoice.py:9:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
billing\invoice.py:5: in total
    return round(amount * (1 + rate(country)), 2)
                               ^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

country = 'XX'

    def rate(country):
        if country == "XX":
>           raise ValueError(f"unknown country {country!r}")
E           ValueError: unknown country 'XX'

billing\tax.py:6: ValueError"""

DIFF = "\n".join(
    [
        "diff --git a/billing/tax.py b/billing/tax.py",
        "index 1111111..2222222 100644",
        "--- a/billing/tax.py",
        "+++ b/billing/tax.py",
        "@@ -1,5 +1,7 @@",
        "-RATE = 0.21",
        "+RATE = 0.19",
        " ",
        " ",
        " def rate(country):",
        '+    if country == "XX":',
        '+        raise ValueError(f"unknown country {country!r}")',
        "     return RATE",
    ]
)

TEST = "tests/test_invoice.py"
CHAIN = (TEST, "billing/invoice.py", "billing/tax.py")
SELECTION = Selection(
    tests=(TEST,), total_tests=4, changed=("billing/tax.py",), reasons={TEST: CHAIN}
)
CHANGES = ChangeSet(modified=("billing/tax.py",))
GRAPH = DependencyGraph(
    {
        TEST: ["billing/invoice.py"],
        "billing/invoice.py": ["billing/tax.py"],
        "billing/tax.py": [],
    }
)


def failure(nodeid: str, message: str, details: str, line: int | None = 4) -> TestCase:
    return TestCase(nodeid, Outcome.FAILED, message=message, details=details, file=TEST, line=line)


VAT = failure(f"{TEST}::test_total_with_vat", "assert 119.0 == 121.0", ASSERTION)
COUNTRY = failure(f"{TEST}::test_unknown_country", "ValueError: unknown country 'XX'", RAISED, 8)


# --------------------------------------------------------------------------- tracebacks


class TestTracebackFrames:
    def test_pytest_long_format_with_windows_paths(self, tmp_path: Path) -> None:
        assert traceback_frames(RAISED, tmp_path) == [
            Frame(TEST, 9),
            Frame("billing/invoice.py", 5, "total"),
            Frame("billing/tax.py", 6),
        ]

    def test_error_and_source_lines_are_not_frames(self, tmp_path: Path) -> None:
        details = "E   AssertionError: see app.py:3: here\n>   x = 'b.py:4: y'\napp/c.py:7: in f"
        assert traceback_frames(details, tmp_path) == [Frame("app/c.py", 7, "f")]

    def test_native_python_format(self, tmp_path: Path) -> None:
        details = (
            "Traceback (most recent call last):\n"
            f'  File "{tmp_path / "billing" / "tax.py"}", line 6, in rate\n'
            "    raise ValueError(x)\n"
            "ValueError: x"
        )
        assert traceback_frames(details, tmp_path) == [Frame("billing/tax.py", 6, "rate")]

    def test_absolute_paths_inside_and_outside_the_project(self, tmp_path: Path) -> None:
        inside = tmp_path / "app" / "core.py"
        details = (
            f"{inside}:3: in f\n"
            "/usr/lib/python3.12/site-packages/requests/api.py:59: in get\n"
            f"{tmp_path.parent / 'elsewhere.py'}:1: in g"
        )
        frames = traceback_frames(details, tmp_path)
        assert frames[0] == Frame("app/core.py", 3, "f")
        assert not frames[1].inside
        assert frames[1].path.endswith("requests/api.py")
        assert not frames[2].inside

    def test_paths_relative_to_another_rootdir_are_mapped_back(self, tmp_path: Path) -> None:
        # pytest's rootdir was tests/ (tests/pytest.ini), so it printed "test_x.py".
        frames = traceback_frames("test_x.py:3: AssertionError", tmp_path, ["tests/test_x.py"])
        assert frames == [Frame("tests/test_x.py", 3)]

    def test_consecutive_duplicates_collapse(self, tmp_path: Path) -> None:
        details = "a.py:1: in f\na.py:1: in f\nb.py:2: in g"
        assert [f.path for f in traceback_frames(details, tmp_path)] == ["a.py", "b.py"]


# --------------------------------------------------------------------------- diffs


class TestParseDiff:
    def test_modified_file(self) -> None:
        (hunk,) = parse_diff(DIFF)["billing/tax.py"]
        assert (hunk.start, hunk.length) == (1, 7)
        assert hunk.text.splitlines()[0] == "@@ -1,5 +1,7 @@"
        assert hunk.touched == (1, 5, 6)
        assert [hunk.distance(n) for n in (1, 3, 6, 9)] == [0, 2, 0, 3]

    def test_hunk_bodies_are_read_by_their_line_counts(self) -> None:
        # A removed "-- note" line reads "--- note": it must not start a new file.
        diff = "\n".join(
            [
                "diff --git a/x.sql b/x.sql",
                "--- a/x.sql",
                "+++ b/x.sql",
                "@@ -1,2 +1,2 @@",
                "--- note",
                "+++ new note",
                " select 1;",
                "\\ No newline at end of file",
            ]
        )
        (hunk,) = parse_diff(diff)["x.sql"]
        assert hunk.text.splitlines()[1:3] == ["--- note", "+++ new note"]
        assert hunk.touched == (1,)

    def test_new_deleted_binary_and_several_files(self) -> None:
        diff = "\n".join(
            [
                "diff --git a/new.py b/new.py",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/new.py",
                "@@ -0,0 +1,2 @@",
                "+A = 1",
                "+B = 2",
                "diff --git a/old.py b/old.py",
                "deleted file mode 100644",
                "--- a/old.py",
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "-GONE = 1",
                "diff --git a/logo.png b/logo.png",
                "Binary files a/logo.png and b/logo.png differ",
                "diff --git a/app.py b/app.py",
                "--- a/app.py",
                "+++ b/app.py",
                "@@ -3 +3 @@ def f():",
                "-    return 1",
                "+    return 2",
                "@@ -10,0 +11 @@ def g():",
                "+    pass",
            ]
        )
        hunks = parse_diff(diff)
        assert sorted(hunks) == ["app.py", "new.py"]
        assert hunks["new.py"][0].touched == (1, 2)
        assert [(h.start, h.touched) for h in hunks["app.py"]] == [(3, (3,)), (11, (11,))]

    def test_quoted_paths(self) -> None:
        diff = '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -1 +1 @@\n-a\n+b'
        assert list(parse_diff(diff)) == ["café.py"]

    def test_nothing_to_parse(self) -> None:
        assert parse_diff("") == {}


# --------------------------------------------------------------------------- suspects


def run_diagnose(
    *failures: TestCase,
    root: Path,
    selection: Selection = SELECTION,
    changes: ChangeSet = CHANGES,
    diff: str = DIFF,
    graph: DependencyGraph | None = GRAPH,
    history: History | None = None,
    limit: int = 3,
) -> list[Diagnosis]:
    return diagnose(
        list(failures),
        selection=selection,
        changes=changes,
        root=root,
        diff=diff,
        graph=graph,
        history=history,
        limit=limit,
    )


class TestSuspects:
    def test_the_changed_line_that_raised_the_error(self, tmp_path: Path) -> None:
        (d,) = run_diagnose(COUNTRY, root=tmp_path)
        suspect = d.suspects[0]
        assert (suspect.path, suspect.line) == ("billing/tax.py", 6)
        assert suspect.reason == "changed, and where the error was raised"
        assert suspect.hunk is not None
        assert "raise ValueError" in suspect.hunk.text
        assert d.chain == CHAIN

    def test_a_change_the_test_only_imports(self, tmp_path: Path) -> None:
        (d,) = run_diagnose(VAT, root=tmp_path)
        suspect = d.suspects[0]
        assert (suspect.path, suspect.line) == ("billing/tax.py", 1)
        assert suspect.reason == "changed; the test imports it indirectly (2 imports away)"

    def test_the_traceback_wins_even_without_an_import(self, tmp_path: Path) -> None:
        # e.g. a plugin or a fixture loaded by name: the graph cannot see it, the traceback can
        selection = Selection(tests=(TEST,), total_tests=4, changed=("billing/tax.py",))
        (d,) = run_diagnose(COUNTRY, root=tmp_path, selection=selection, graph=None)
        assert (d.suspects[0].path, d.suspects[0].line) == ("billing/tax.py", 6)

    def test_near_and_far_from_the_change(self, tmp_path: Path) -> None:
        near = failure(f"{TEST}::a", "E", "billing/tax.py:8: in rate")
        far = failure(f"{TEST}::b", "F", "billing/tax.py:40: in rate")
        first, second = run_diagnose(near, far, root=tmp_path)
        assert first.suspects[0].reason == "where the error was raised, next to a change"
        assert second.suspects[0].reason == (
            "where the error was raised; the file changed at lines 1-6"
        )

    def test_outer_frames_are_suspects_too(self, tmp_path: Path) -> None:
        details = "billing/tax.py:6: in rate\nlib/other.py:3: TypeError"
        changes = ChangeSet(modified=("billing/tax.py", "lib/other.py"))
        case = failure(f"{TEST}::c", "TypeError", details)
        (d,) = run_diagnose(case, root=tmp_path, changes=changes)
        assert [(s.path, s.line, s.reason) for s in d.suspects[:2]] == [
            ("lib/other.py", 3, "where the error was raised; this file changed"),  # no diff read
            ("billing/tax.py", 6, "changed, and in the traceback"),
        ]

    def test_without_a_diff_whole_files_are_suspects(self, tmp_path: Path) -> None:
        (first,) = run_diagnose(COUNTRY, root=tmp_path, diff="")
        assert first.suspects[0].reason == "where the error was raised; this file changed"
        (second,) = run_diagnose(VAT, root=tmp_path, diff="")
        assert (second.suspects[0].path, second.suspects[0].line) == ("billing/tax.py", None)

    def test_a_changed_test(self, tmp_path: Path) -> None:
        selection = Selection(
            tests=(TEST,), total_tests=4, changed=(TEST,), reasons={TEST: (TEST,)}
        )
        diff = f"--- a/{TEST}\n+++ b/{TEST}\n@@ -5 +5 @@\n-    assert x == 1\n+    assert x == 2"
        (d,) = run_diagnose(
            VAT, root=tmp_path, selection=selection, changes=ChangeSet(modified=(TEST,)), diff=diff
        )
        assert (d.suspects[0].path, d.suspects[0].line) == (TEST, 5)
        assert d.suspects[0].reason == "changed, and where the error was raised"

    def test_a_changed_test_that_did_not_raise_the_error(self, tmp_path: Path) -> None:
        selection = Selection(
            tests=(TEST,), total_tests=4, changed=(TEST,), reasons={TEST: (TEST,)}
        )
        diff = f"--- a/{TEST}\n+++ b/{TEST}\n@@ -1 +1 @@\n-import a\n+import b"
        (d,) = run_diagnose(
            COUNTRY,
            root=tmp_path,
            selection=selection,
            changes=ChangeSet(modified=(TEST,)),
            diff=diff,
        )
        # The traceback passes through the test (line 9), far from its change (line 1).
        assert d.suspects[0].reason == "in the traceback; the file changed at line 1"

    def test_a_deleted_module(self, tmp_path: Path) -> None:
        changes = ChangeSet(deleted=("billing/tax.py",))
        (d,) = run_diagnose(VAT, root=tmp_path, changes=changes, diff="")
        assert d.suspects[0].reason == "deleted; the test imports it indirectly (2 imports away)"

    def test_nothing_changed_nothing_suspected(self, tmp_path: Path) -> None:
        selection = Selection(tests=(TEST,), total_tests=4)
        (d,) = run_diagnose(VAT, root=tmp_path, selection=selection, changes=ChangeSet(), diff="")
        assert d.suspects == ()
        assert d.chain == ()


class TestGrouping:
    def test_same_error_and_suspects_are_one_diagnosis(self, tmp_path: Path) -> None:
        nested = failure(f"{TEST}::TestGroup::test_nested", "assert 59.5 == 60.5", ASSERTION)
        diagnoses = run_diagnose(VAT, COUNTRY, nested, root=tmp_path)
        assert [d.case.nodeid for d in diagnoses] == [VAT.nodeid, COUNTRY.nodeid]
        assert diagnoses[0].same_failure == (nested.nodeid,)

    def test_limit(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("INFO", logger="karma")
        diagnoses = run_diagnose(VAT, COUNTRY, root=tmp_path, limit=1)
        assert [d.case.nodeid for d in diagnoses] == [VAT.nodeid]
        assert "diagnosing the first 1 of 2 distinct failures" in caplog.text


# --------------------------------------------------------------------------- history


def history(*runs: tuple[str, str, tuple[str, ...]], flaky: tuple[str, ...] = ()) -> History:
    """Runs of (outcome of the test file, commit, changed files)."""
    return History(
        None,
        [
            Run(
                timestamp=float(i),
                tests={TEST: TestRecord(outcome)},
                changed=changed,
                flaky=flaky if i == 0 else (),
            )
            for i, (outcome, _, changed) in enumerate(runs)
        ],
    )


class TestHistoryNote:
    def test_no_history(self, tmp_path: Path) -> None:
        (d,) = run_diagnose(VAT, root=tmp_path, history=History(None, []))
        assert d.history is None

    def test_first_failure(self, tmp_path: Path) -> None:
        past = history(("passed", "", ()), ("passed", "", ()), ("passed", "", ()))
        (d,) = run_diagnose(VAT, root=tmp_path, history=past)
        assert d.history == "first failure in 3 recorded runs"

    def test_failed_before_and_with_these_files(self, tmp_path: Path) -> None:
        past = history(("failed", "", ("billing/tax.py",)), ("passed", "", ()), ("passed", "", ()))
        (d,) = run_diagnose(VAT, root=tmp_path, history=past)
        assert d.history == (
            "failed in 1 of 3 recorded runs, most recently 3 runs ago; "
            "it failed 1 of 1 times before when billing/tax.py changed"
        )

    def test_failed_last_time_and_flaky(self, tmp_path: Path) -> None:
        past = history(("passed", "", ()), ("failed", "", ()), flaky=(VAT.nodeid,))
        (d,) = run_diagnose(VAT, root=tmp_path, history=past)
        assert d.history == (
            "failed in 1 of 2 recorded runs, including the last one; "
            "passed on retry in 1 earlier run, so it is flaky"
        )

    def test_flipping_outcomes(self, tmp_path: Path) -> None:
        past = history(*[("passed" if i % 2 else "failed", "", ()) for i in range(6)])
        (d,) = run_diagnose(VAT, root=tmp_path, history=past)
        assert d.history is not None
        assert "its outcome flipped 5 times, so it may be flaky" in d.history

    def test_unknown_test(self, tmp_path: Path) -> None:
        other = History(
            None, [Run(timestamp=1.0, tests={"tests/test_other.py": TestRecord("passed")})]
        )
        (d,) = run_diagnose(VAT, root=tmp_path, history=other)
        assert d.history == "no earlier results for this test file in 1 recorded run"


# --------------------------------------------------------------------------- files, output


def test_relevant_files_puts_the_traceback_first(tmp_path: Path) -> None:
    changes = ChangeSet(
        modified=("billing/invoice.py", "billing/tax.py", "docs/x.md"), deleted=("gone.py",)
    )
    selection = Selection(tests=(TEST,), total_tests=4, changed=changes.all, reasons={TEST: CHAIN})
    graph = DependencyGraph(
        {TEST: ["billing/invoice.py", "gone.py"], "billing/invoice.py": ["billing/tax.py"]}
    )
    details = "billing/tax.py:6: ValueError"
    files = relevant_files(
        [failure(f"{TEST}::x", "E", details)], selection, changes, tmp_path, graph
    )
    assert files == ["billing/tax.py", "billing/invoice.py"]  # deleted files have no diff


def test_failures_survive_until_the_next_run(tmp_path: Path) -> None:
    save_failures(tmp_path, [VAT, COUNTRY], recorded_at=1234.56789)
    loaded, recorded = load_failures(tmp_path)
    assert loaded == [VAT, COUNTRY]
    assert recorded == 1234.568
    assert (tmp_path / CACHE_DIR / ".gitignore").exists()

    save_failures(tmp_path, [])  # a passing run clears them
    assert load_failures(tmp_path) == ([], None)


def test_unreadable_failures_are_ignored(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / CACHE_DIR / FAILURES_FILE
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    assert load_failures(tmp_path) == ([], None)
    assert "ignoring unreadable" in caplog.text


def test_to_json(tmp_path: Path) -> None:
    (d,) = run_diagnose(COUNTRY, root=tmp_path)
    (data,) = to_json([d])
    assert data["test"] == COUNTRY.nodeid
    assert data["chain"] == list(CHAIN)
    assert data["suspects"][0]["file"] == "billing/tax.py"
    assert data["suspects"][0]["line"] == 6
    assert "raise ValueError" in data["suspects"][0]["diff"]
    assert data["explanation"] is None
    json.dumps(data)  # plain data all the way down


def test_files_are_inferred_for_reports_that_lack_them() -> None:
    known = ["tests/test_core.py", "tests/pkg/test_deep.py"]
    xunit2 = [
        TestCase("tests.test_core::test_quad", Outcome.FAILED),
        TestCase("tests.pkg.test_deep.TestX::test_y", Outcome.FAILED),
        TestCase("tests.test_gone::test_z", Outcome.ERROR),  # unknown: left alone
        TestCase("tests/test_core.py::test_quad", Outcome.FAILED, file="tests/test_core.py"),
    ]
    inferred = infer_test_files(xunit2, known)
    assert [(c.nodeid, c.file) for c in inferred] == [
        ("tests/test_core.py::test_quad", "tests/test_core.py"),
        ("tests/pkg/test_deep.py::TestX::test_y", "tests/pkg/test_deep.py"),
        ("tests.test_gone::test_z", None),
        ("tests/test_core.py::test_quad", "tests/test_core.py"),
    ]


def test_hunk_distance_without_touched_lines() -> None:
    hunk = Hunk("a.py", 10, 1, "@@ -10 +10 @@\n context")
    assert hunk.touched == ()
    assert hunk.distance(13) == 3
