"""Predict which selected tests are most likely to fail, so they can run first.

Risk never removes a test from the selection: it only decides the *order*, so that a
failure shows up in the first seconds of a run instead of the last. Karma's guarantee
("every affected test runs") is untouched.

The model is a logistic regression over a handful of explainable features, trained
online on the local history (:mod:`karma.history`) in one chronological pass. Features
for each past run are computed only from the runs *before* it, so the model never
learns from information it could not have had at the time. It is regularised towards
sensible prior weights, so with little or no history it still orders tests well:
changed test files first, then tests that import a changed file directly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from karma.history import FAILED, PASSED, History, Run
from karma.selector import Selection

FEATURES = (
    "test_changed",  # the test file itself changed
    "proximity",  # 1 / import hops from the nearest changed file
    "failure_rate",  # share of recent runs that failed (recency-weighted)
    "recent_failure",  # decays with every run since the last failure
    "co_failure",  # failed before when one of these same files changed
    "flakiness",  # how often its outcome flipped between runs
    "new_test",  # never ran before (history exists, this test is not in it)
)

# Log-odds weights used before any history is seen; learning moves away from them only
# as far as the evidence justifies (see PRIOR_STRENGTH).
PRIOR: Mapping[str, float] = {
    "bias": -3.0,
    "test_changed": 2.0,
    "proximity": 1.5,
    "failure_rate": 3.0,
    "recent_failure": 2.0,
    "co_failure": 3.0,
    "flakiness": 1.0,
    "new_test": 1.0,
}
PRIOR_STRENGTH = 0.01  # L2 pull towards PRIOR, per training example
LEARNING_RATE = 0.5
DECAY = 0.9  # weight of a test's older runs in its failure rate
RECENCY = 0.7  # recent_failure = RECENCY ** runs since the last failure
MAX_CHANGED = 25  # a huge change set says little about any single file


@dataclass
class _TestStats:
    weighted_runs: float = 0.0
    weighted_failures: float = 0.0
    runs: int = 0
    failures: int = 0
    since_failure: int | None = None
    last_outcome: str | None = None
    flips: int = 0
    duration: float | None = None


@dataclass
class Evidence:
    """What past runs say about each test, updated one run at a time."""

    runs: int = 0
    tests: dict[str, _TestStats] = field(default_factory=dict)
    # (changed file, test) -> [runs where both happened, of which the test failed]
    pairs: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    def features(self, test: str, distance: int | None, changed: Sequence[str]) -> dict[str, float]:
        stats = self.tests.get(test)
        co_failure = 0.0
        for path in changed[:MAX_CHANGED]:
            counts = self.pairs.get((path, test))
            if counts:
                co_failure = max(co_failure, counts[1] / (counts[0] + 1))
        return {
            "test_changed": 1.0 if distance == 0 else 0.0,
            "proximity": 0.0 if distance is None else 1.0 / max(distance, 1),
            "failure_rate": (stats.weighted_failures / (stats.weighted_runs + 1) if stats else 0.0),
            "recent_failure": (
                RECENCY**stats.since_failure if stats and stats.since_failure is not None else 0.0
            ),
            "co_failure": co_failure,
            "flakiness": stats.flips / max(stats.runs - 1, 1) if stats else 0.0,
            "new_test": 1.0 if self.runs and stats is None else 0.0,
        }

    def update(self, run: Run) -> None:
        self.runs += 1
        changed = run.changed[:MAX_CHANGED]
        for test, record in run.tests.items():
            if record.outcome not in (PASSED, FAILED):
                continue
            failed = record.outcome == FAILED
            stats = self.tests.setdefault(test, _TestStats())
            stats.weighted_runs = stats.weighted_runs * DECAY + 1
            stats.weighted_failures = stats.weighted_failures * DECAY + failed
            stats.runs += 1
            stats.failures += failed
            if stats.last_outcome is not None and stats.last_outcome != record.outcome:
                stats.flips += 1
            stats.last_outcome = record.outcome
            if failed:
                stats.since_failure = 0
            elif stats.since_failure is not None:
                stats.since_failure += 1
            if record.duration > 0:
                previous = stats.duration
                stats.duration = (
                    record.duration if previous is None else 0.7 * previous + 0.3 * record.duration
                )
            for path in changed:
                counts = self.pairs.setdefault((path, test), [0, 0])
                counts[0] += 1
                counts[1] += failed

    def duration(self, test: str) -> float | None:
        stats = self.tests.get(test)
        return stats.duration if stats else None


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class RiskModel:
    weights: dict[str, float] = field(default_factory=lambda: dict(PRIOR))
    examples: int = 0

    def probability(self, features: Mapping[str, float]) -> float:
        z = self.weights["bias"] + sum(self.weights[f] * features[f] for f in FEATURES)
        return _sigmoid(z)

    def contributions(self, features: Mapping[str, float]) -> dict[str, float]:
        return {f: self.weights[f] * features[f] for f in FEATURES}

    def learn(self, features: Mapping[str, float], failed: bool) -> None:
        """One step of stochastic gradient descent on the log loss."""
        self.examples += 1
        error = self.probability(features) - (1.0 if failed else 0.0)
        rate = LEARNING_RATE / math.sqrt(self.examples + 10)
        for name in ("bias", *FEATURES):
            value = 1.0 if name == "bias" else features[name]
            pull = PRIOR_STRENGTH * (self.weights[name] - PRIOR[name])
            self.weights[name] -= rate * (error * value + pull)

    @classmethod
    def train(cls, history: History) -> tuple[RiskModel, Evidence]:
        """Replay ``history`` in order: predict each run from the runs before it, then learn."""
        model = cls()
        evidence = Evidence()
        for run in history.runs:
            for test, record in run.tests.items():
                if record.outcome in (PASSED, FAILED):
                    features = evidence.features(test, record.distance, run.changed)
                    model.learn(features, record.outcome == FAILED)
            evidence.update(run)
        return model, evidence


@dataclass(frozen=True)
class TestSummary:
    __test__ = False  # not a pytest test class, despite the name

    test: str
    runs: int
    failures: int
    flips: int
    duration: float | None

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def flip_rate(self) -> float:
        return self.flips / (self.runs - 1) if self.runs > 1 else 0.0

    @property
    def flaky(self) -> bool:
        """Flips back and forth often: the signature of a flaky test (or a hot spot)."""
        return self.runs >= 5 and self.failures >= 2 and self.flip_rate >= 0.3


def summarize(history: History) -> list[TestSummary]:
    """Per-test statistics over the whole history."""
    evidence = Evidence()
    for run in history.runs:
        evidence.update(run)
    return [
        TestSummary(test, s.runs, s.failures, s.flips, s.duration)
        for test, s in sorted(evidence.tests.items())
    ]


@dataclass(frozen=True)
class Risk:
    test: str
    probability: float
    reasons: tuple[str, ...] = ()


def assess(selection: Selection, history: History) -> list[Risk]:
    """Score every selected test, highest risk first (then quickest, then by path)."""
    model, evidence = RiskModel.train(history)
    scored: list[tuple[Risk, float]] = []
    for test in selection.tests:
        chain = selection.reasons.get(test)
        distance = len(chain) - 1 if chain else None
        features = evidence.features(test, distance, selection.changed)
        risk = Risk(
            test,
            model.probability(features),
            _reasons(test, chain, features, model, evidence, selection.changed),
        )
        scored.append((risk, evidence.duration(test) or math.inf))
    scored.sort(key=lambda item: (-item[0].probability, item[1], item[0].test))
    return [risk for risk, _ in scored]


def _reasons(
    test: str,
    chain: Sequence[str] | None,
    features: Mapping[str, float],
    model: RiskModel,
    evidence: Evidence,
    changed: Sequence[str],
) -> tuple[str, ...]:
    """The strongest reasons, in plain words, for a test's risk."""
    stats = evidence.tests.get(test)
    texts: list[tuple[float, str]] = []
    for name, weight in model.contributions(features).items():
        if weight <= 0.05:
            continue
        if name == "test_changed":
            text = "the test itself changed"
        elif name == "proximity" and chain and len(chain) > 1:
            hops = len(chain) - 1
            text = (
                f"imports {chain[1]} (changed)"
                if hops == 1
                else f"{hops} imports away from {chain[-1]}"
            )
        elif name == "failure_rate" and stats:
            text = f"failed in {stats.failures} of {stats.runs} recorded runs"
        elif name == "recent_failure" and stats and stats.since_failure is not None:
            text = (
                "failed the last time it ran"
                if stats.since_failure == 0
                else f"failed {stats.since_failure + 1} runs ago"
            )
        elif name == "co_failure":
            culprit = max(
                (p for p in changed[:MAX_CHANGED] if (p, test) in evidence.pairs),
                key=lambda p: evidence.pairs[(p, test)][1],
                default=None,
            )
            text = (
                f"failed before when {culprit} changed"
                if culprit
                else "failed with similar changes"
            )
        elif name == "flakiness":
            text = "flaky: its outcome often flips"
        elif name == "new_test":
            text = "new test with no history"
        else:
            continue
        texts.append((weight, text))
    texts.sort(key=lambda item: -item[0])
    return tuple(text for _, text in texts[:3])
