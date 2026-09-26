from __future__ import annotations

import sys
from pathlib import Path

import pytest

from karma import runner
from karma.runner import Outcome, RunResult, TestCase, parse_junit, run_pytest

XUNIT1 = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
  <testcase classname="tests.test_x" name="test_ok" file="tests/test_x.py" line="3" time="0.01"/>
  <testcase classname="tests.test_x.TestGroup" name="test_bad" file="tests/test_x.py" line="9"
            time="0.02"><failure message="assert 1 == 2">def test_bad():
&gt;       assert 1 == 2
E       assert 1 == 2</failure></testcase>
  <testcase classname="tests.test_x" name="test_boom" file="tests/test_x.py" line="12" time="x">
    <error message="fixture 'db' not found">setup failed</error></testcase>
  <testcase classname="tests.test_x" name="test_later" file="tests/test_x.py" line="15" time="0">
    <skipped message="not ready"/></testcase>
  <testcase classname="sub.test_broken" name="sub.test_broken" file="sub\\test_broken.py">
    <error message="collection failure">Traceback:
E   ImportError: no module named nope</error></testcase>
</testsuite></testsuites>
"""

XUNIT2 = """<testsuites><testsuite>
  <testcase classname="tests.test_y.TestA" name="test_a" time="0.5"/>
  <testcase classname="" name="bare"/>
</testsuite></testsuites>
"""


def test_parse_xunit1(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(XUNIT1, encoding="utf-8")

    cases = parse_junit(report)

    assert [c.nodeid for c in cases] == [
        "tests/test_x.py::test_ok",
        "tests/test_x.py::TestGroup::test_bad",
        "tests/test_x.py::test_boom",
        "tests/test_x.py::test_later",
        "sub/test_broken.py",  # module-level error; Windows separators normalised
    ]
    assert [c.outcome for c in cases] == [
        Outcome.PASSED,
        Outcome.FAILED,
        Outcome.ERROR,
        Outcome.SKIPPED,
        Outcome.ERROR,
    ]
    ok, bad, boom, later, broken = cases
    assert (ok.file, ok.line, ok.duration) == ("tests/test_x.py", 4, 0.01)
    assert bad.message == "assert 1 == 2"
    assert "E       assert 1 == 2" in bad.details
    assert boom.duration == 0.0
    assert later.message == "not ready"
    assert broken.message == "ImportError: no module named nope"
    assert broken.line is None


def test_parse_xunit2_without_file_attributes(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(XUNIT2, encoding="utf-8")
    assert [c.nodeid for c in parse_junit(report)] == ["tests.test_y.TestA::test_a", "bare"]


def test_malformed_report_is_reported_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    report = tmp_path / "r.xml"
    report.write_text("<testsuites><oops", encoding="utf-8")
    assert parse_junit(report) == []
    assert "could not read pytest report" in caplog.text


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ([0], 0),
        ([5], 5),
        ([0, 5], 0),
        ([0, 1], 1),
        ([1, 2], 2),
        ([4, 3], 3),
        ([0, 7], 7),
    ],
)
def test_combine_exit_codes(codes: list[int], expected: int) -> None:
    assert runner._combine_exit_codes(codes) == expected


def test_batches_respect_the_command_line_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "MAX_COMMAND_LENGTH", 30)
    assert runner._batches(["a" * 8, "b" * 8, "c" * 8], fixed_length=5) == [
        ["a" * 8, "b" * 8],
        ["c" * 8],
    ]
    assert runner._batches([], fixed_length=5) == [[]]


def test_result_properties() -> None:
    result = RunResult(
        exit_code=1,
        cases=(
            TestCase("a", Outcome.PASSED),
            TestCase("b", Outcome.FAILED),
            TestCase("c", Outcome.ERROR),
        ),
    )
    assert result.counts[Outcome.PASSED] == 1
    assert [c.nodeid for c in result.problems] == ["b", "c"]
    assert not result.ok
    assert RunResult(exit_code=5).ok


# ----------------------------------------------------------------- real pytest runs


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    (tmp_path / "test_mixed.py").write_text(
        "import pytest\n"
        "def test_pass():\n    assert True\n"
        "def test_fail():\n    assert 1 == 2\n"
        "@pytest.mark.skip(reason='later')\ndef test_skip():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_green.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_runs_pytest_and_collects_per_test_results(suite: Path) -> None:
    result = run_pytest(["test_mixed.py"], cwd=suite, args=["-p", "no:cacheprovider"])

    assert result.exit_code == 1
    assert not result.crashed
    outcomes = {c.nodeid: c.outcome for c in result.cases}
    assert outcomes == {
        "test_mixed.py::test_pass": Outcome.PASSED,
        "test_mixed.py::test_fail": Outcome.FAILED,
        "test_mixed.py::test_skip": Outcome.SKIPPED,
    }
    failure = result.problems[0]
    assert failure.line == 4
    assert "assert 1 == 2" in failure.message
    assert result.duration > 0


def test_batched_runs_are_merged(suite: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "MAX_COMMAND_LENGTH", len(sys.executable) + 250)
    result = run_pytest(
        ["test_green.py", "test_mixed.py"], cwd=suite, args=["-p", "no:cacheprovider"]
    )
    assert result.exit_code == 1
    assert len(result.cases) == 4


def test_no_tests_collected_is_ok(tmp_path: Path) -> None:
    (tmp_path / "test_empty.py").write_text("x = 1\n", encoding="utf-8")
    result = run_pytest(["test_empty.py"], cwd=tmp_path, args=["-p", "no:cacheprovider"])
    assert result.exit_code == runner.EXIT_NO_TESTS_COLLECTED
    assert result.ok
    assert not result.crashed


def test_pytest_usage_errors_are_flagged(suite: Path) -> None:
    result = run_pytest(["test_green.py"], cwd=suite, args=["--no-such-option"])
    assert result.exit_code == runner.EXIT_USAGE_ERROR
    assert result.crashed


def test_missing_interpreter(suite: Path, caplog: pytest.LogCaptureFixture) -> None:
    result = run_pytest(["test_green.py"], cwd=suite, python=str(suite / "no-python"))
    assert result.crashed
    assert result.exit_code == runner.EXIT_INTERNAL_ERROR
    assert "could not start pytest" in caplog.text


def test_paths_are_rebased_when_pytest_rootdir_is_a_subdirectory(tmp_path: Path) -> None:
    # Like Textualize/rich: tests/pytest.ini makes tests/ the rootdir, so pytest reports
    # "test_x.py" rather than "tests/test_x.py".
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tests / "test_x.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")

    result = run_pytest(["tests/test_x.py"], cwd=tmp_path, args=["-p", "no:cacheprovider"])

    (case,) = result.cases
    assert case.file == "tests/test_x.py"
    assert case.nodeid == "tests/test_x.py::test_bad"


@pytest.mark.parametrize(
    ("known", "expected"),
    [
        (["tests/test_x.py"], "tests/test_x.py"),
        (["a/test_x.py", "b/test_x.py"], "test_x.py"),  # ambiguous: left alone
        ([], "test_x.py"),
    ],
)
def test_rebase_only_on_a_unique_match(tmp_path: Path, known: list[str], expected: str) -> None:
    case = TestCase("test_x.py::test_a", Outcome.FAILED, file="test_x.py")
    assert runner._rebase(case, tmp_path, known).file == expected
