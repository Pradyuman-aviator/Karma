from __future__ import annotations

import pytest

from karma import risk as risk_module
from karma.history import FAILED, PASSED, SKIPPED, History, Run, TestRecord
from karma.risk import PRIOR, Evidence, RiskModel, assess
from karma.selector import Selection


def selection(changed: tuple[str, ...], **chains: tuple[str, ...]) -> Selection:
    """``chains``: test file (with ``/`` written as ``__``) -> its dependency chain."""
    paths = {name: name.replace("__", "/") + ".py" for name in chains}
    reasons = {paths[name]: (paths[name], *chain) for name, chain in chains.items()}
    return Selection(tests=tuple(sorted(reasons)), total_tests=10, changed=changed, reasons=reasons)


def history(*runs: tuple[tuple[str, ...], dict[str, str]]) -> History:
    """Each run: (changed files, {test: outcome}); all tests one hop from the change."""
    return History(
        runs=[
            Run(float(i), {t: TestRecord(o, 1.0, 1) for t, o in tests.items()}, changed)
            for i, (changed, tests) in enumerate(runs)
        ]
    )


class TestWithoutHistory:
    def test_changed_tests_then_direct_importers_then_the_rest(self) -> None:
        sel = selection(
            ("app/core.py", "tests/test_b.py"),
            tests__test_far=("app/api.py", "app/core.py"),
            tests__test_near=("app/core.py",),
            tests__test_b=(),
        )

        risks = assess(sel, History())

        assert [r.test for r in risks] == [
            "tests/test_b.py",
            "tests/test_near.py",
            "tests/test_far.py",
        ]
        assert risks[0].reasons == ("the test itself changed",)
        assert risks[1].reasons == ("imports app/core.py (changed)",)
        assert risks[2].reasons == ("2 imports away from app/core.py",)
        assert 0 < risks[2].probability < risks[1].probability < risks[0].probability < 1


class TestLearningFromHistory:
    def test_a_test_that_fails_often_ranks_first(self) -> None:
        runs = [
            (
                ("app/x.py",),
                {"tests/test_shaky.py": FAILED if i % 2 else PASSED, "tests/test_ok.py": PASSED},
            )
            for i in range(20)
        ]
        sel = selection(
            ("app/x.py",), tests__test_ok=("app/x.py",), tests__test_shaky=("app/x.py",)
        )

        risks = assess(sel, history(*runs))

        assert risks[0].test == "tests/test_shaky.py"
        assert any("failed in 10 of 20 recorded runs" in r for r in risks[0].reasons)
        assert risks[0].probability > 2 * risks[1].probability

    def test_it_learns_which_changes_break_which_tests(self) -> None:
        # test_pay fails whenever app/pay.py changes, and only then.
        runs = []
        for i in range(30):
            changed = ("app/pay.py",) if i % 3 == 0 else ("app/other.py",)
            outcome = FAILED if changed == ("app/pay.py",) else PASSED
            runs.append((changed, {"tests/test_pay.py": outcome, "tests/test_misc.py": PASSED}))
        sel = selection(
            ("app/pay.py",), tests__test_misc=("app/pay.py",), tests__test_pay=("app/pay.py",)
        )

        risks = assess(sel, history(*runs))

        assert risks[0].test == "tests/test_pay.py"
        assert "failed before when app/pay.py changed" in risks[0].reasons
        model, _ = RiskModel.train(history(*runs))
        assert model.examples == 60
        assert model.weights != dict(PRIOR)

    def test_new_tests_are_flagged(self) -> None:
        sel = selection(("app/x.py",), tests__test_new=("app/x.py",), tests__test_old=("app/x.py",))
        risks = assess(sel, history((("app/y.py",), {"tests/test_old.py": PASSED})))
        assert risks[0].test == "tests/test_new.py"
        assert "new test with no history" in risks[0].reasons

    def test_recent_failures_and_flakiness_are_explained(self) -> None:
        runs = [((), {"tests/test_a.py": o}) for o in (PASSED, FAILED, PASSED, FAILED, PASSED)]
        sel = selection(("app/x.py",), tests__test_a=("app/x.py",))
        (only,) = assess(sel, history(*runs))
        assert (
            "failed 2 runs ago" in only.reasons or "flaky: its outcome often flips" in only.reasons
        )

    def test_ties_run_the_quickest_test_first(self) -> None:
        runs = History(
            runs=[
                Run(
                    0,
                    {
                        "tests/test_slow.py": TestRecord(PASSED, 9.0, 1),
                        "tests/test_fast.py": TestRecord(PASSED, 0.1, 1),
                    },
                )
            ]
        )
        sel = selection(
            ("app/x.py",), tests__test_slow=("app/x.py",), tests__test_fast=("app/x.py",)
        )
        assert [r.test for r in assess(sel, runs)] == ["tests/test_fast.py", "tests/test_slow.py"]


class TestEvidence:
    def test_no_look_ahead(self) -> None:
        evidence = Evidence()
        run = Run(0, {"t.py": TestRecord(FAILED, 1.0, 1)}, ("a.py",))
        before = evidence.features("t.py", 1, ("a.py",))
        evidence.update(run)
        after = evidence.features("t.py", 1, ("a.py",))

        assert before["failure_rate"] == before["co_failure"] == before["recent_failure"] == 0
        assert after["failure_rate"] > 0
        assert after["co_failure"] > 0
        assert after["recent_failure"] == 1.0

    def test_skipped_results_are_ignored(self) -> None:
        evidence = Evidence()
        evidence.update(Run(0, {"t.py": TestRecord(SKIPPED)}))
        assert "t.py" not in evidence.tests

    def test_huge_change_sets_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(risk_module, "MAX_CHANGED", 2)
        evidence = Evidence()
        evidence.update(Run(0, {"t.py": TestRecord(FAILED)}, ("a.py", "b.py", "c.py")))
        assert {path for path, _ in evidence.pairs} == {"a.py", "b.py"}

    def test_unknown_distance(self) -> None:
        assert Evidence().features("t.py", None, ())["proximity"] == 0.0


@pytest.mark.parametrize("z", [-1000.0, -5.0, 0.0, 5.0, 1000.0])
def test_sigmoid_is_numerically_stable(z: float) -> None:
    assert 0.0 <= risk_module._sigmoid(z) <= 1.0


class TestSummaries:
    def test_statistics_and_flakiness(self) -> None:
        outcomes = [PASSED, FAILED, PASSED, FAILED, PASSED, FAILED]
        runs = [((), {"tests/test_flaky.py": o, "tests/test_ok.py": PASSED}) for o in outcomes]

        summaries = {s.test: s for s in risk_module.summarize(history(*runs))}

        flaky = summaries["tests/test_flaky.py"]
        assert (flaky.runs, flaky.failures, flaky.flips) == (6, 3, 5)
        assert flaky.failure_rate == 0.5
        assert flaky.flaky
        assert not summaries["tests/test_ok.py"].flaky
        assert summaries["tests/test_ok.py"].flip_rate == 0.0

    def test_a_single_run_is_never_flaky(self) -> None:
        (only,) = risk_module.summarize(history(((), {"t.py": FAILED})))
        assert only.flip_rate == 0.0
        assert not only.flaky
