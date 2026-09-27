"""Explain failing tests with the evidence Karma already has.

For each failure Karma knows why the test ran (the import chain from a changed file),
what changed (the diff) and how the test behaved before (the history). A diagnosis puts
these side by side and names the *suspects*: changed lines that appear in the
traceback, or, when none do, the changed files the test depends on. It is computed
locally and nothing leaves the machine; :mod:`karma.ai` can turn the same evidence into
a written explanation, only when asked to.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from karma.cache import CACHE_DIR, prepare_directory
from karma.git import ChangeSet, to_relative
from karma.graph import DependencyGraph
from karma.history import History
from karma.risk import Evidence
from karma.runner import Outcome, TestCase
from karma.selector import Selection

log = logging.getLogger(__name__)

MAX_DIAGNOSES = 3  # failures explained per run by default (each may be an AI request)
MAX_SUSPECTS = 3
MAX_DIFF_FILES = 20  # changed files whose diff is read for one run's diagnoses
NEAR = 3  # a traceback line this close to a changed line counts as "next to" it
FAILURES_FILE = "last-failures.json"

# pytest's long/short tracebacks: "billing/tax.py:6: ValueError", "app.py:5: in total".
# Error lines ("E   ...") and the failing source line (">   ...") are not frames.
_PYTEST_FRAME = re.compile(
    r"^(?!E\s|>)(?P<path>(?:[A-Za-z]:)?[^\s:][^:\n]*?\.py):(?P<line>\d+):"
    r"(?: in (?P<func>\S+))?"
)
# Python's own format (pytest --tb=native, or a traceback printed by the code under test).
_NATIVE_FRAME = re.compile(
    r'^\s*File "(?P<path>[^"]+\.py)", line (?P<line>\d+)(?:, in (?P<func>\S+))?'
)
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+(?P<start>\d+)(?:,(?P<new>\d+))? @@")
_NUMBER = re.compile(r"0x[0-9a-fA-F]+|\d+(?:\.\d+)?")


# --------------------------------------------------------------------------- tracebacks


@dataclass(frozen=True)
class Frame:
    """One position in a traceback; ``path`` is project-relative when ``inside``."""

    path: str
    line: int
    function: str = ""
    inside: bool = True


def traceback_frames(details: str, root: Path, known: Collection[str] = ()) -> list[Frame]:
    """The frames of a failure report, outermost first (the order pytest prints them).

    Paths are made relative to ``root``. pytest prints them relative to its rootdir,
    which ``known`` (project files) helps map back, as :func:`karma.runner.rebase_cases`
    does. Frames in installed packages are kept, marked as outside the project.
    """
    frames: list[Frame] = []
    for text in details.splitlines():
        match = _PYTEST_FRAME.match(text) or _NATIVE_FRAME.match(text)
        if not match:
            continue
        raw = match["path"].replace("\\", "/")
        path = _project_path(raw, root, known)
        frame = Frame(path or raw, int(match["line"]), match["func"] or "", path is not None)
        if not frames or frames[-1] != frame:
            frames.append(frame)
    return frames


def _project_path(path: str, root: Path, known: Collection[str]) -> str | None:
    if "/site-packages/" in path or "/dist-packages/" in path:
        return None
    if Path(path).is_absolute() or re.match(r"^[A-Za-z]:/", path):
        return to_relative(path, root)
    path = path.removeprefix("./")
    if (root / path).is_file():
        return path
    matches = [k for k in known if k.endswith("/" + path)]
    return matches[0] if len(matches) == 1 else path


# --------------------------------------------------------------------------- diffs


@dataclass(frozen=True)
class Hunk:
    """One ``@@`` block of a unified diff, located in the new version of the file."""

    path: str
    start: int
    length: int
    text: str

    @property
    def touched(self) -> tuple[int, ...]:
        """New-file lines that were added or changed; for a pure deletion, the line after it."""
        lines: list[int] = []
        current = self.start
        for text in self.text.splitlines()[1:]:
            if text.startswith(("+", "-")):
                if not lines or lines[-1] != current:
                    lines.append(current)
                if text.startswith("+"):
                    current += 1
            elif not text.startswith("\\"):  # "\ No newline at end of file"
                current += 1
        return tuple(lines)

    def distance(self, line: int) -> int:
        """How many lines ``line`` is from the nearest touched line (0: it was touched)."""
        return min((abs(line - t) for t in self.touched), default=abs(line - self.start))


def parse_diff(text: str) -> dict[str, list[Hunk]]:
    """Hunks per file from ``git diff`` output (deleted and binary files have none).

    Each hunk's header gives its line counts, which are followed exactly: a removed
    line reading ``-- note`` must not pass for a ``--- a/file`` header.
    """
    hunks: dict[str, list[Hunk]] = {}
    path: str | None = None
    block: list[str] = []
    start = length = old_left = new_left = 0
    for line in text.splitlines():
        if block:
            block.append(line)
            if line.startswith("+"):
                new_left -= 1
            elif line.startswith("-"):
                old_left -= 1
            elif not line.startswith("\\"):
                old_left -= 1
                new_left -= 1
            if old_left <= 0 and new_left <= 0:
                if path is not None:
                    hunks.setdefault(path, []).append(Hunk(path, start, length, "\n".join(block)))
                block = []
        elif line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            target = _unquote(line[4:].rstrip("\t"))
            path = None if target == "/dev/null" else target.removeprefix("b/")
        elif (header := _HUNK_HEADER.match(line)) and path is not None:
            start = int(header["start"])
            length = new_left = int(header["new"]) if header["new"] is not None else 1
            old_left = int(header["old"]) if header["old"] is not None else 1
            if old_left or new_left:
                block = [line]
    return hunks


def _unquote(path: str) -> str:
    """git quotes unusual paths as C strings: ``"b/caf\\303\\251.py"``."""
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1].encode("latin-1", errors="backslashreplace").decode("unicode_escape")
    return raw.encode("latin-1", errors="replace").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- diagnosis


@dataclass(frozen=True)
class Suspect:
    """A change that may have caused a failure, and why Karma thinks so."""

    path: str
    line: int | None
    reason: str
    hunk: Hunk | None = None


@dataclass(frozen=True)
class Explanation:
    """A language model's reading of a diagnosis (see :mod:`karma.ai`)."""

    summary: str
    cause: str = ""
    fix: str = ""
    #: regression, test-needs-update, flaky, environment or unclear
    kind: str = "unclear"
    confidence: str = "low"
    path: str | None = None
    line: int | None = None
    model: str = ""


@dataclass(frozen=True)
class Diagnosis:
    """The evidence about one failing test."""

    case: TestCase
    #: why the test ran: (test, file it imports, ..., changed file); () if unknown
    chain: tuple[str, ...] = ()
    history: str | None = None
    suspects: tuple[Suspect, ...] = ()
    #: other failures with the same error and the same suspects
    same_failure: tuple[str, ...] = ()
    #: the traceback's frames, outermost first
    frames: tuple[Frame, ...] = ()
    explanation: Explanation | None = None
    #: the diff of every changed file involved (for the AI prompt)
    diff: tuple[Hunk, ...] = field(default=(), repr=False)

    @property
    def test_file(self) -> str:
        return _test_file(self.case)


def _test_file(case: TestCase) -> str:
    return case.file or case.nodeid.partition("::")[0]


def infer_test_files(cases: Sequence[TestCase], known: Collection[str]) -> list[TestCase]:
    """Find the file of failures from reports that do not record it.

    pytest's default JUnit format (xunit2) gives only a dotted class name, so the node
    id reads ``tests.test_core.TestX::test_y``; with ``tests/test_core.py`` among the
    ``known`` files it becomes ``tests/test_core.py::TestX::test_y``.
    """
    files = set(known)
    result: list[TestCase] = []
    for case in cases:
        dotted, sep, name = case.nodeid.partition("::")
        if not case.file and "/" not in dotted:
            parts = dotted.split(".")
            for size in range(len(parts), 0, -1):
                candidate = "/".join(parts[:size]) + ".py"
                if candidate in files:
                    nodeid = "::".join([candidate, *parts[size:], *([name] if sep else [])])
                    case = replace(case, file=candidate, nodeid=nodeid)
                    break
        result.append(case)
    return result


def diagnose(
    failures: Sequence[TestCase],
    *,
    selection: Selection,
    changes: ChangeSet,
    root: Path,
    diff: str = "",
    graph: DependencyGraph | None = None,
    history: History | None = None,
    limit: int = MAX_DIAGNOSES,
) -> list[Diagnosis]:
    """Diagnose ``failures``: identical ones are grouped, the first ``limit`` returned.

    ``diff`` is ``git diff`` output for the files :func:`relevant_files` names; without
    it, suspects are whole files rather than lines.
    """
    known = _known(selection, graph)
    hunks = parse_diff(diff)
    changed = set(changes.all) | set(selection.changed)
    deleted = set(changes.deleted)
    evidence = _evidence(history)
    diagnoses: list[Diagnosis] = []
    groups: dict[tuple[object, ...], int] = {}
    for case in failures:
        frames = tuple(traceback_frames(case.details, root, known))
        test = _test_file(case)
        chain = tuple(selection.reasons.get(test, ()))
        involved = _involved(test, chain, frames, changed, graph)
        suspects = tuple(_suspects(test, frames, involved, hunks, deleted, case.line))
        key = (_signature(case.message), tuple((s.path, s.line) for s in suspects))
        if key in groups:
            first = diagnoses[groups[key]]
            diagnoses[groups[key]] = replace(first, same_failure=(*first.same_failure, case.nodeid))
            continue
        groups[key] = len(diagnoses)
        diagnoses.append(
            Diagnosis(
                case=case,
                chain=chain,
                history=_history_note(test, case.nodeid, evidence, history, sorted(changed)),
                suspects=suspects,
                frames=frames,
                diff=tuple(h for path in involved for h in hunks.get(path, ())),
            )
        )
    if len(diagnoses) > limit:
        log.info("diagnosing the first %d of %d distinct failures", limit, len(diagnoses))
    return diagnoses[:limit]


def relevant_files(
    failures: Sequence[TestCase],
    selection: Selection,
    changes: ChangeSet,
    root: Path,
    graph: DependencyGraph | None = None,
) -> list[str]:
    """The changed files whose diff a diagnosis needs, most relevant first: those in the
    failing tests' tracebacks, then those the tests depend on, nearest first."""
    changed = set(changes.all) | set(selection.changed)
    known = _known(selection, graph)
    ranked: dict[str, int] = {}
    for case in failures:
        test = _test_file(case)
        frames = traceback_frames(case.details, root, known)
        chain = tuple(selection.reasons.get(test, ()))
        for path, distance in _involved(test, chain, frames, changed, graph).items():
            ranked[path] = min(distance, ranked.get(path, distance))
    readable = [p for p in ranked if p not in changes.deleted and p not in changes.submodules]
    return sorted(readable, key=lambda p: (ranked[p], p))[:MAX_DIFF_FILES]


def _known(selection: Selection, graph: DependencyGraph | None) -> list[str]:
    return sorted({*(graph.files if graph is not None else ()), *selection.tests})


def _involved(
    test: str,
    chain: Sequence[str],
    frames: Sequence[Frame],
    changed: Collection[str],
    graph: DependencyGraph | None,
) -> dict[str, int]:
    """Changed files involved in a failure, with their distance in imports from the test.

    A changed file in the traceback is involved whatever the import graph says (-1:
    the traceback is the strongest evidence there is); the test itself is 0.
    """
    found: dict[str, int] = {}

    def add(path: str, distance: int) -> None:
        found[path] = min(distance, found.get(path, distance))

    for frame in frames:
        if frame.inside and frame.path in changed:
            add(frame.path, -1)
    if test in changed:
        add(test, 0)
    if chain and chain[-1] in changed:
        add(chain[-1], len(chain) - 1)
    if graph is not None and test in graph.files:
        seen = {test}
        queue = deque([(test, 0)])
        while queue:
            path, distance = queue.popleft()
            for dependency in sorted(graph.dependencies(path)):
                if dependency not in seen:
                    seen.add(dependency)
                    if dependency in changed:
                        add(dependency, distance + 1)
                    queue.append((dependency, distance + 1))
    return dict(sorted(found.items(), key=lambda item: (item[1], item[0])))


def _suspects(
    test: str,
    frames: Sequence[Frame],
    involved: Mapping[str, int],
    hunks: Mapping[str, Sequence[Hunk]],
    deleted: Collection[str],
    test_line: int | None,
) -> list[Suspect]:
    suspects: list[Suspect] = []

    def add(suspect: Suspect) -> None:
        if len(suspects) < MAX_SUSPECTS and all(
            (s.path, s.line) != (suspect.path, suspect.line) for s in suspects
        ):
            suspects.append(suspect)

    # 1. Changed files on the traceback, from where the error was raised outwards.
    innermost = next((f for f in reversed(frames) if f.inside), None)
    for frame in reversed(frames):
        if not frame.inside or frame.path not in involved:
            continue
        where = "where the error was raised" if frame is innermost else "in the traceback"
        nearest = _nearest(hunks.get(frame.path, ()), frame.line)
        if nearest is None:  # no line-level diff (e.g. --files): the file changed
            add(Suspect(frame.path, frame.line, f"{where}; this file changed"))
        elif nearest.distance(frame.line) == 0:
            add(Suspect(frame.path, frame.line, f"changed, and {where}", nearest))
        elif nearest.distance(frame.line) <= NEAR:
            add(Suspect(frame.path, frame.line, f"{where}, next to a change", nearest))
        else:
            span = _span(nearest)
            add(Suspect(frame.path, frame.line, f"{where}; the file changed at {span}", nearest))
    # 2. The other changed files the test depends on, nearest first.
    on_traceback = {s.path for s in suspects}
    for path, distance in involved.items():
        if path in on_traceback:
            continue
        file_hunks = hunks.get(path, ())
        if path in deleted:
            add(Suspect(path, None, "deleted; " + _imported(distance).removeprefix("changed; ")))
        elif path == test:
            line = test_line if test_line is not None else 1
            hunk = _nearest(file_hunks, line)
            if hunk is not None:
                add(Suspect(path, _first_touched(hunk), "the test itself changed", hunk))
            else:
                add(Suspect(path, None, "the test itself changed"))
        elif file_hunks:
            for hunk in file_hunks[:2]:
                add(Suspect(path, _first_touched(hunk), _imported(distance), hunk))
        else:
            add(Suspect(path, None, _imported(distance)))
    return suspects


def _nearest(hunks: Sequence[Hunk], line: int) -> Hunk | None:
    best: Hunk | None = None
    for hunk in hunks:
        if best is None or hunk.distance(line) < best.distance(line):
            best = hunk
    return best


def _imported(distance: int) -> str:
    if distance <= 1:
        return "changed; the test imports it"
    return f"changed; the test imports it indirectly ({distance} imports away)"


def _first_touched(hunk: Hunk) -> int:
    touched = hunk.touched
    return touched[0] if touched else hunk.start


def _span(hunk: Hunk) -> str:
    touched = hunk.touched or (hunk.start,)
    first, last = min(touched), max(touched)
    return f"line {first}" if first == last else f"lines {first}-{last}"


def _signature(message: str) -> str:
    """The error with numbers blanked, so ``assert 119.0 == 121.0`` matches ``assert 9 == 1``."""
    return _NUMBER.sub("#", message.strip())


# --------------------------------------------------------------------------- history


def _evidence(history: History | None) -> Evidence | None:
    if history is None or not history.runs:
        return None
    evidence = Evidence()
    for run in history.runs:
        evidence.update(run)
    return evidence


def _history_note(
    test: str,
    nodeid: str,
    evidence: Evidence | None,
    history: History | None,
    changed: Sequence[str],
) -> str | None:
    """What earlier runs say about a test file, in a sentence (``None`` without history)."""
    if evidence is None or history is None:
        return None
    stats = evidence.tests.get(test)
    if stats is None:
        return f"no earlier results for this test file in {_runs(evidence.runs)}"
    notes: list[str] = []
    if stats.failures == 0:
        notes.append(f"first failure in {_runs(stats.runs)}")
    else:
        note = f"failed in {stats.failures} of {_runs(stats.runs)}"
        if stats.since_failure == 0:
            note += ", including the last one"
        elif stats.since_failure is not None:
            note += f", most recently {stats.since_failure + 1} runs ago"
        notes.append(note)
    flaky_runs = sum(1 for run in history.runs if nodeid in run.flaky)
    if flaky_runs:
        notes.append(f"passed on retry in {_runs(flaky_runs, 'earlier run')}, so it is flaky")
    elif stats.runs >= 5 and stats.flips / (stats.runs - 1) >= 0.3:
        notes.append(f"its outcome flipped {stats.flips} times, so it may be flaky")
    for path in changed:
        counts = evidence.pairs.get((path, test))
        if counts and counts[1]:
            notes.append(f"it failed {counts[1]} of {counts[0]} times before when {path} changed")
            break
    return "; ".join(notes)


def _runs(count: int, noun: str = "recorded run") -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --------------------------------------------------------------------------- persistence


def save_failures(
    root: Path, failures: Sequence[TestCase], recorded_at: float | None = None
) -> None:
    """Keep the last run's failures, so ``karma diagnose`` can explain them later.

    ``recorded_at`` is the run's timestamp in the history, if it was recorded there.
    """
    path = root / CACHE_DIR / FAILURES_FILE
    try:
        if not failures:
            path.unlink(missing_ok=True)
            return
        prepare_directory(path.parent)
        data = [
            {
                "nodeid": case.nodeid,
                "outcome": case.outcome.value,
                "message": case.message,
                "details": case.details[-20_000:],
                "file": case.file,
                "line": case.line,
            }
            for case in failures
        ]
        recorded = None if recorded_at is None else round(recorded_at, 3)
        payload = {"version": 1, "recorded_at": recorded, "failures": data}
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    except OSError as exc:
        log.debug("could not save failures to %s: %s", path, exc)


def load_failures(root: Path) -> tuple[list[TestCase], float | None]:
    """The failures saved by the last ``karma run`` (none if it passed), and the
    timestamp under which that run is in the history (``None`` if it is not)."""
    path = root / CACHE_DIR / FAILURES_FILE
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        cases = [
            TestCase(
                nodeid=str(item["nodeid"]),
                outcome=Outcome(item["outcome"]),
                message=str(item.get("message") or ""),
                details=str(item.get("details") or ""),
                file=item["file"] if isinstance(item.get("file"), str) else None,
                line=item["line"] if isinstance(item.get("line"), int) else None,
            )
            for item in data["failures"]
        ]
        recorded = data.get("recorded_at")
        return cases, float(recorded) if isinstance(recorded, (int, float)) else None
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return [], None


# --------------------------------------------------------------------------- output


def to_json(diagnoses: Sequence[Diagnosis]) -> list[dict[str, Any]]:
    """Diagnoses as plain data, for ``karma diagnose --format json``."""
    result = []
    for d in diagnoses:
        explanation = d.explanation
        result.append(
            {
                "test": d.case.nodeid,
                "outcome": d.case.outcome.value,
                "error": d.case.message,
                "chain": list(d.chain),
                "history": d.history,
                "suspects": [
                    {
                        "file": s.path,
                        "line": s.line,
                        "reason": s.reason,
                        "diff": s.hunk.text if s.hunk else None,
                    }
                    for s in d.suspects
                ],
                "same_failure": list(d.same_failure),
                "explanation": None
                if explanation is None
                else {
                    "summary": explanation.summary,
                    "cause": explanation.cause,
                    "fix": explanation.fix,
                    "kind": explanation.kind,
                    "confidence": explanation.confidence,
                    "file": explanation.path,
                    "line": explanation.line,
                    "model": explanation.model,
                },
            }
        )
    return result
