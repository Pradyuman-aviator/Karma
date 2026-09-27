"""The flaky-test registry: detect, quarantine and release unreliable tests.

A test is *confirmed* flaky when it fails and then passes on retry against the same
code (``karma run --retries N``). That is far stronger evidence than a history of
flip-flopping outcomes, which a real bug that was later fixed also produces.

Quarantined tests are listed in a small TOML file committed to the repository
(``karma-quarantine.toml`` by default), so the whole team sees and reviews them.
They still run, and are still reported, but their failures do not fail the build:
skipping them would hide whether they have been fixed. ``karma flaky sync``
quarantines tests confirmed flaky repeatedly and releases those that have passed
reliably since.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from karma.errors import ConfigError
from karma.history import History
from karma.runner import Outcome, RunResult

log = logging.getLogger(__name__)

tomllib: ModuleType | None
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python < 3.11 only
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

REGISTRY_FILE = "karma-quarantine.toml"
HEADER = (
    "# Quarantined tests: they still run and are reported, but their failures do not\n"
    "# fail the build. Managed with `karma flaky`; remove an entry once the test is fixed.\n"
)


@dataclass(frozen=True)
class Entry:
    """A quarantined test: a pytest node id, a test file, or a test without its params."""

    id: str
    reason: str = ""
    since: str = ""
    issue: str = ""

    def matches(self, nodeid: str) -> bool:
        return nodeid == self.id or nodeid.startswith((f"{self.id}::", f"{self.id}["))


@dataclass
class Registry:
    path: Path
    entries: list[Entry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Registry:
        registry = cls(path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return registry
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from None
        if tomllib is None:  # pragma: no cover - Python < 3.11 without tomli
            log.warning("install 'tomli' to read %s; no tests are quarantined", path.name)
            return registry
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from None
        items = data.get("quarantine", [])
        if not isinstance(items, list):
            raise ConfigError(f"{path.name}: `quarantine` must be an array of tables")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ConfigError(f"{path.name}: every [[quarantine]] entry needs an `id`")
            unknown = set(item) - {"id", "reason", "since", "issue"}
            if unknown:
                raise ConfigError(f"{path.name}: unknown key(s) {', '.join(sorted(unknown))}")
            registry.entries.append(Entry(**{k: str(v) for k, v in item.items()}))
        return registry

    def save(self) -> None:
        lines = [HEADER]
        for entry in sorted(self.entries, key=lambda e: e.id):
            lines.append("\n[[quarantine]]\n")
            for key, value in dataclasses.asdict(entry).items():
                if value:
                    # JSON strings are valid TOML basic strings.
                    lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}\n")
        self.path.write_text("".join(lines), encoding="utf-8")

    def match(self, nodeid: str) -> Entry | None:
        return next((entry for entry in self.entries if entry.matches(nodeid)), None)

    def add(self, entry: Entry) -> bool:
        if any(existing.id == entry.id for existing in self.entries):
            return False
        self.entries.append(entry)
        return True

    def remove(self, test_id: str) -> bool:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.id != test_id]
        return len(self.entries) != before


def apply_quarantine(result: RunResult, registry: Registry) -> RunResult:
    """Mark failures of quarantined tests ``QUARANTINED`` (so they do not fail the build)."""
    if not registry.entries:
        return result
    cases = tuple(
        dataclasses.replace(case, outcome=Outcome.QUARANTINED)
        if case.outcome in (Outcome.FAILED, Outcome.ERROR) and registry.match(case.nodeid)
        else case
        for case in result.cases
    )
    return dataclasses.replace(result, cases=cases).settled()


# --------------------------------------------------------------------------- detection


@dataclass(frozen=True)
class Flake:
    """A test's record in the history: how often it was confirmed flaky."""

    id: str
    flaky_runs: int
    last_seen: float


def confirmed_flakes(history: History) -> list[Flake]:
    """Tests that failed and then passed on retry, most often first."""
    counts: dict[str, list[float]] = {}
    for run in history.runs:
        for nodeid in run.flaky:
            record = counts.setdefault(nodeid, [0, 0.0])
            record[0] += 1
            record[1] = run.timestamp
    return sorted(
        (Flake(nodeid, int(n), last) for nodeid, (n, last) in counts.items()),
        key=lambda flake: (-flake.flaky_runs, flake.id),
    )


def watched_outcomes(history: History, entry: Entry) -> Iterator[str]:
    """Outcomes of a quarantined test in each recorded run it ran in, oldest first."""
    for run in history.runs:
        for nodeid, outcome in run.watched.items():
            if entry.matches(nodeid):
                yield outcome
                break


def healed(history: History, entry: Entry, streak: int) -> bool:
    """Whether a quarantined test passed in each of its last ``streak`` runs."""
    outcomes = list(watched_outcomes(history, entry))
    return len(outcomes) >= streak and all(o == Outcome.PASSED.value for o in outcomes[-streak:])


@dataclass(frozen=True)
class SyncPlan:
    quarantine: tuple[Entry, ...]
    release: tuple[Entry, ...]


def plan_sync(
    registry: Registry, history: History, *, min_flakes: int = 2, heal_after: int = 10
) -> SyncPlan:
    """Quarantine tests confirmed flaky ``min_flakes`` times; release healed ones."""
    today = datetime.date.today().isoformat()
    quarantine = tuple(
        Entry(
            flake.id,
            reason=f"passed on retry after failing, in {flake.flaky_runs} recorded runs",
            since=today,
        )
        for flake in confirmed_flakes(history)
        if flake.flaky_runs >= min_flakes and not registry.match(flake.id)
    )
    release = tuple(e for e in registry.entries if healed(history, e, heal_after))
    return SyncPlan(quarantine, release)


def watched_cases(result: RunResult, registry: Registry) -> dict[str, str]:
    """Outcomes of quarantined tests in this run, to tell later when they have healed."""
    outcome_names: dict[Outcome, str] = {
        Outcome.QUARANTINED: Outcome.FAILED.value,
        Outcome.FLAKY: Outcome.FLAKY.value,
    }
    return {
        case.nodeid: outcome_names.get(case.outcome, case.outcome.value)
        for case in result.cases
        if registry.match(case.nodeid)
    }


def to_json(entries: Iterable[Entry]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(entry) for entry in entries]
