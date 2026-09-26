from __future__ import annotations

import json
from pathlib import Path

import pytest

from karma import history as history_module
from karma.history import (
    FAILED,
    PASSED,
    SKIPPED,
    History,
    Run,
    TestRecord,
    default_history_path,
    records_from,
    run_from,
)
from karma.runner import Outcome, RunResult, TestCase
from karma.selector import Selection

SELECTION = Selection(
    tests=("tests/test_a.py", "tests/test_b.py"),
    total_tests=3,
    changed=("app/core.py",),
    reasons={
        "tests/test_a.py": ("tests/test_a.py", "app/core.py"),
        "tests/test_b.py": ("tests/test_b.py", "app/api.py", "app/core.py"),
    },
)


def run(n: int, **tests: str) -> Run:
    return Run(timestamp=float(n), tests={k: TestRecord(v) for k, v in tests.items()})


class TestRecords:
    def test_cases_are_folded_per_file(self) -> None:
        result = RunResult(
            exit_code=1,
            cases=(
                TestCase("tests/test_a.py::t1", Outcome.PASSED, 0.5, file="tests/test_a.py"),
                TestCase("tests/test_a.py::t2", Outcome.FAILED, 0.25, file="tests/test_a.py"),
                TestCase("tests/test_b.py::t3", Outcome.PASSED, 1.0, file="tests/test_b.py"),
                TestCase("tests/test_c.py::t4", Outcome.SKIPPED, 0.0),  # no file attribute
            ),
        )

        records = records_from(result, SELECTION)

        assert records == {
            "tests/test_a.py": TestRecord(FAILED, 0.75, 1),
            "tests/test_b.py": TestRecord(PASSED, 1.0, 2),
            "tests/test_c.py": TestRecord(SKIPPED, 0.0, None),
        }

    def test_errors_count_as_failures_and_changed_tests_have_distance_zero(self) -> None:
        selection = Selection(
            tests=("tests/test_a.py",),
            total_tests=1,
            reasons={"tests/test_a.py": ("tests/test_a.py",)},
        )
        result = RunResult(1, (TestCase("x", Outcome.ERROR, file="tests/test_a.py"),))
        assert records_from(result, selection)["tests/test_a.py"] == TestRecord(FAILED, 0.0, 0)

    def test_run_from(self) -> None:
        result = RunResult(0, (TestCase("t", Outcome.PASSED, file="tests/test_a.py"),))
        recorded = run_from(result, SELECTION, "abc123", now=42.0)
        assert recorded.timestamp == 42.0
        assert recorded.commit == "abc123"
        assert recorded.changed == ("app/core.py",)
        assert set(recorded.tests) == {"tests/test_a.py"}


class TestStorage:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = default_history_path(tmp_path)
        stored = History.load(path)
        original = Run(1.5, {"t.py": TestRecord(FAILED, 0.25, 1)}, ("a.py",), "sha")
        stored.append(original)

        assert History.load(path).runs == [original]
        assert (path.parent / ".gitignore").exists()  # the cache dir hides itself from git

    def test_missing_and_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "history.jsonl"
        assert History.load(path).runs == []
        good = json.dumps(run(1, **{"t.py": PASSED}).to_json())
        path.write_text(f"{good}\n{{not json\n\n{good[:20]}\n", encoding="utf-8")
        assert len(History.load(path).runs) == 1

    def test_unreadable_history_is_ignored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "history.jsonl"
        path.mkdir()
        assert History.load(path).runs == []
        assert "cannot read test history" in caplog.text

    def test_write_failure_is_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stored = History(tmp_path / "missing" / "deeper" / "history.jsonl")
        stored.append(run(1, **{"t.py": PASSED}))
        assert "could not record test history" in caplog.text
        assert len(stored.runs) == 1

    def test_only_recent_runs_are_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(history_module, "MAX_RUNS", 3)
        path = tmp_path / "history.jsonl"
        stored = History.load(path)
        for n in range(5):
            stored.append(run(n, **{"t.py": PASSED}))

        assert [r.timestamp for r in History.load(path).runs] == [2.0, 3.0, 4.0]
        assert len(path.read_text(encoding="utf-8").splitlines()) == 3

    def test_in_memory_history(self) -> None:
        stored = History.load(None)
        stored.append(run(1, **{"t.py": PASSED}))
        assert len(stored.runs) == 1

    def test_distance_must_be_numeric(self) -> None:
        data = {"t": 1, "tests": {"t.py": ["passed", 0, "far"]}}
        with pytest.raises(TypeError):
            Run.from_json(data)
