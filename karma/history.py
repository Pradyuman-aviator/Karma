"""A local record of past test outcomes: the training data for risk prediction.

Every ``karma run`` appends one line to ``.karma_cache/history.jsonl`` with, for each
test file that ran, its outcome, duration, and how far it was from the change (in
import hops). Nothing leaves the machine. In CI, persist ``.karma_cache/`` between runs
(the Karma GitHub Action does this automatically) so the history accumulates.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from karma.cache import CACHE_DIR, prepare_directory
from karma.runner import Outcome, RunResult, TestCase
from karma.selector import Selection

log = logging.getLogger(__name__)

HISTORY_FILE = "history.jsonl"
MAX_RUNS = 500  # older runs are dropped; recent behaviour matters most

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"


@dataclass(frozen=True)
class TestRecord:
    """One test file's result in one run."""

    __test__ = False  # not a pytest test class, despite the name

    outcome: str
    duration: float = 0.0
    #: import hops from the nearest changed file: 0 = the test file itself changed;
    #: None = unknown (e.g. a full run, or an imported report)
    distance: int | None = None


@dataclass(frozen=True)
class Run:
    timestamp: float
    tests: Mapping[str, TestRecord]
    changed: tuple[str, ...] = ()
    commit: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "t": round(self.timestamp, 3),
            "commit": self.commit,
            "changed": list(self.changed),
            "tests": {
                path: [rec.outcome, round(rec.duration, 4), rec.distance]
                for path, rec in sorted(self.tests.items())
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Run:
        tests = {
            str(path): TestRecord(str(outcome), float(duration), _optional_int(distance))
            for path, (outcome, duration, distance) in data["tests"].items()
        }
        return cls(
            timestamp=float(data["t"]),
            tests=tests,
            changed=tuple(str(p) for p in data.get("changed", ())),
            commit=data.get("commit"),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raise TypeError(f"expected a number, got {value!r}")


@dataclass
class History:
    """Past runs, oldest first, backed by a JSON-lines file (``None``: in memory)."""

    path: Path | None = None
    runs: list[Run] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> History:
        history = cls(path)
        if path is None:
            return history
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return history
        except OSError as exc:
            log.warning("cannot read test history %s: %s", path, exc)
            return history
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                history.runs.append(Run.from_json(json.loads(line)))
            except (ValueError, KeyError, TypeError) as exc:
                # e.g. a line cut short by a crash or a concurrent writer
                log.debug("skipping malformed history line %d: %s", number, exc)
        history.runs = history.runs[-MAX_RUNS:]
        return history

    def append(self, run: Run) -> None:
        """Record ``run``; failure to write is logged, never fatal."""
        self.runs.append(run)
        overflow = len(self.runs) > MAX_RUNS
        self.runs = self.runs[-MAX_RUNS:]
        if self.path is None:
            return
        try:
            prepare_directory(self.path.parent)
            if overflow:  # rewrite, keeping only the most recent runs
                text = "".join(json.dumps(r.to_json()) + "\n" for r in self.runs)
                self.path.write_text(text, encoding="utf-8")
            else:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(run.to_json()) + "\n")
        except OSError as exc:
            log.warning("could not record test history in %s: %s", self.path, exc)


def default_history_path(root: Path) -> Path:
    return root / CACHE_DIR / HISTORY_FILE


def file_of(case: TestCase) -> str:
    """The test file a JUnit test case belongs to."""
    return case.file or case.nodeid.split("::", 1)[0]


def records_from(result: RunResult, selection: Selection) -> dict[str, TestRecord]:
    """Fold per-test results into one record per test file."""
    grouped: dict[str, list[TestCase]] = {}
    for case in result.cases:
        grouped.setdefault(file_of(case), []).append(case)
    return {path: _record(cases, _distance(path, selection)) for path, cases in grouped.items()}


def _record(cases: Iterable[TestCase], distance: int | None) -> TestRecord:
    outcomes = [case.outcome for case in cases]
    duration = sum(case.duration for case in cases)
    if any(o in (Outcome.FAILED, Outcome.ERROR) for o in outcomes):
        outcome = FAILED
    elif any(o is Outcome.PASSED for o in outcomes):
        outcome = PASSED
    else:
        outcome = SKIPPED
    return TestRecord(outcome, duration, distance)


def _distance(test: str, selection: Selection) -> int | None:
    chain = selection.reasons.get(test)
    return len(chain) - 1 if chain else None


def run_from(
    result: RunResult, selection: Selection, commit: str | None, now: float | None = None
) -> Run:
    return Run(
        timestamp=time.time() if now is None else now,
        tests=records_from(result, selection),
        changed=selection.changed,
        commit=commit,
    )
