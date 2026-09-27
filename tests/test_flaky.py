from __future__ import annotations

from pathlib import Path

import pytest

from karma.errors import ConfigError
from karma.flaky import (
    Entry,
    Registry,
    apply_quarantine,
    confirmed_flakes,
    healed,
    plan_sync,
    watched_cases,
)
from karma.history import FAILED, FLAKY, PASSED, History, Run, TestRecord, records_from
from karma.risk import Evidence
from karma.runner import (
    EXIT_OK,
    EXIT_TESTS_FAILED,
    EXIT_USAGE_ERROR,
    Outcome,
    RunResult,
    TestCase,
    rerun_failures,
)
from karma.selector import Selection


def case(nodeid: str, outcome: Outcome) -> TestCase:
    return TestCase(nodeid, outcome, file=nodeid.split("::")[0])


class TestEntry:
    @pytest.mark.parametrize(
        ("entry", "nodeid", "expected"),
        [
            ("tests/test_a.py::test_x", "tests/test_a.py::test_x", True),
            ("tests/test_a.py::test_x", "tests/test_a.py::test_x[1-2]", True),
            ("tests/test_a.py", "tests/test_a.py::TestC::test_y", True),
            ("tests/test_a.py::TestC", "tests/test_a.py::TestC::test_y", True),
            ("tests/test_a.py", "tests/test_ab.py::test_x", False),
            ("tests/test_a.py::test_x", "tests/test_a.py::test_xy", False),
        ],
    )
    def test_matches(self, entry: str, nodeid: str, expected: bool) -> None:
        assert Entry(entry).matches(nodeid) is expected


class TestRegistry:
    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert Registry.load(tmp_path / "q.toml").entries == []

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "q.toml"
        registry = Registry(path)
        assert registry.add(
            Entry(
                'tests/test_a.py::test_x["quoted"]',
                'times out "sometimes"',
                "2026-09-27",
                "https://x/1",
            )
        )
        assert registry.add(Entry("tests/test_ü.py", "unicode ✓"))
        assert not registry.add(Entry("tests/test_ü.py"))  # no duplicates
        registry.save()

        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Quarantined tests")
        loaded = Registry.load(path)
        assert loaded.entries == sorted(registry.entries, key=lambda e: e.id)
        assert loaded.match('tests/test_a.py::test_x["quoted"]') is not None

    def test_remove(self, tmp_path: Path) -> None:
        registry = Registry(tmp_path / "q.toml", [Entry("a"), Entry("b")])
        assert registry.remove("a")
        assert not registry.remove("a")
        assert [e.id for e in registry.entries] == ["b"]

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("[[quarantine\n", "invalid TOML"),
            ("quarantine = 1\n", "must be an array"),
            ("[[quarantine]]\nreason = 'x'\n", "needs an `id`"),
            ("[[quarantine]]\nid = 'a'\nowner = 'me'\n", "unknown key"),
        ],
    )
    def test_invalid_files(self, tmp_path: Path, content: str, message: str) -> None:
        path = tmp_path / "q.toml"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ConfigError, match=message):
            Registry.load(path)

    def test_unreadable_file(self, tmp_path: Path) -> None:
        (tmp_path / "q.toml").mkdir()
        with pytest.raises(ConfigError, match="cannot read"):
            Registry.load(tmp_path / "q.toml")


class TestRetries:
    def test_a_pass_on_retry_is_flaky_and_the_run_passes(self) -> None:
        result = RunResult(
            EXIT_TESTS_FAILED,
            (case("t.py::a", Outcome.FAILED), case("t.py::b", Outcome.PASSED)),
        )
        calls: list[list[str]] = []

        def rerun(nodeids: list[str]) -> RunResult:
            calls.append(nodeids)
            outcome = Outcome.PASSED if len(calls) == 2 else Outcome.FAILED
            return RunResult(EXIT_OK, tuple(case(n, outcome) for n in nodeids))

        retried = rerun_failures(result, 3, rerun)

        assert calls == [["t.py::a"], ["t.py::a"]]  # stops once nothing is failing
        flaky = {c.nodeid: c for c in retried.cases}["t.py::a"]
        assert flaky.outcome is Outcome.FLAKY
        assert flaky.message.startswith("passed on retry 2")
        assert retried.exit_code == EXIT_OK
        assert retried.warnings == (flaky,)

    def test_a_test_that_never_passes_stays_failed(self) -> None:
        result = RunResult(EXIT_TESTS_FAILED, (case("t.py::a", Outcome.FAILED),))
        retried = rerun_failures(
            result, 2, lambda ids: RunResult(1, tuple(case(n, Outcome.FAILED) for n in ids))
        )
        assert retried.problems
        assert retried.exit_code == EXIT_TESTS_FAILED

    def test_a_crashed_retry_keeps_the_failures(self) -> None:
        result = RunResult(EXIT_TESTS_FAILED, (case("t.py::a", Outcome.FAILED),))
        retried = rerun_failures(result, 2, lambda ids: RunResult(4, crashed=True))
        assert retried.exit_code == EXIT_TESTS_FAILED

    def test_other_exit_codes_are_never_turned_into_success(self) -> None:
        result = RunResult(EXIT_USAGE_ERROR, (case("t.py::a", Outcome.FLAKY),))
        assert result.settled().exit_code == EXIT_USAGE_ERROR


class TestQuarantine:
    def test_quarantined_failures_do_not_fail_the_run(self) -> None:
        registry = Registry(Path("q.toml"), [Entry("t.py::a")])
        result = RunResult(EXIT_TESTS_FAILED, (case("t.py::a", Outcome.FAILED),))

        applied = apply_quarantine(result, registry)

        assert applied.cases[0].outcome is Outcome.QUARANTINED
        assert applied.exit_code == EXIT_OK

    def test_other_failures_still_fail(self) -> None:
        registry = Registry(Path("q.toml"), [Entry("t.py::a")])
        result = RunResult(
            EXIT_TESTS_FAILED, (case("t.py::a", Outcome.ERROR), case("t.py::b", Outcome.FAILED))
        )
        assert apply_quarantine(result, registry).exit_code == EXIT_TESTS_FAILED

    def test_watched_outcomes(self) -> None:
        registry = Registry(Path("q.toml"), [Entry("t.py")])
        result = RunResult(
            0,
            (
                case("t.py::a", Outcome.PASSED),
                case("t.py::b", Outcome.QUARANTINED),
                case("t.py::c", Outcome.FLAKY),
                case("u.py::d", Outcome.FAILED),
            ),
        )
        assert watched_cases(result, registry) == {
            "t.py::a": "passed",
            "t.py::b": "failed",
            "t.py::c": "flaky",
        }


def runs(*items: tuple[tuple[str, ...], dict[str, str]]) -> History:
    """Each item: (flaky node ids, watched outcomes)."""
    return History(
        runs=[
            Run(float(i), {}, flaky=flaky, watched=watched)
            for i, (flaky, watched) in enumerate(items)
        ]
    )


class TestDetectionAndSync:
    def test_confirmed_flakes_are_counted(self) -> None:
        history = runs(((("a", "b")), {}), (("a",), {}), ((), {}))
        assert [(f.id, f.flaky_runs) for f in confirmed_flakes(history)] == [("a", 2), ("b", 1)]

    def test_healed_needs_a_full_streak_of_passes(self) -> None:
        entry = Entry("t.py::a")
        history = runs(*(((), {"t.py::a": o}) for o in ("failed", "passed", "passed")))
        assert healed(history, entry, 2)
        assert not healed(history, entry, 3)
        assert not healed(runs(), entry, 1)

    def test_plan_sync(self) -> None:
        registry = Registry(Path("q.toml"), [Entry("old::fixed"), Entry("old::still_bad")])
        history = runs(
            (("new::flaky",), {"old::fixed": "passed", "old::still_bad": "failed"}),
            (("new::flaky", "rare::once"), {"old::fixed": "passed", "old::still_bad": "passed"}),
        )

        plan = plan_sync(registry, history, min_flakes=2, heal_after=2)

        assert [e.id for e in plan.quarantine] == ["new::flaky"]
        assert "in 2 recorded runs" in plan.quarantine[0].reason
        assert [e.id for e in plan.release] == ["old::fixed"]


class TestHistoryIntegration:
    def test_flaky_files_are_not_learned_as_failures(self) -> None:
        selection = Selection(tests=("t.py",), total_tests=1)
        result = RunResult(0, (case("t.py::a", Outcome.FLAKY), case("t.py::b", Outcome.PASSED)))
        records = records_from(result, selection)
        assert records["t.py"].outcome == FLAKY

        evidence = Evidence()
        evidence.update(Run(0, records))
        assert "t.py" not in evidence.tests  # neither a pass nor a failure for the model

    def test_round_trip(self) -> None:
        run = Run(1, {"t.py": TestRecord(PASSED)}, flaky=("t.py::a",), watched={"t.py::b": FAILED})
        assert Run.from_json(run.to_json()) == run
