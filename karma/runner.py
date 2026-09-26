"""Run pytest on the selected tests and collect per-test results."""

from __future__ import annotations

import dataclasses
import enum
import logging
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# pytest's documented exit codes.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_INTERRUPTED = 2
EXIT_INTERNAL_ERROR = 3
EXIT_USAGE_ERROR = 4
EXIT_NO_TESTS_COLLECTED = 5

# Windows rejects command lines longer than 32,767 characters.
MAX_COMMAND_LENGTH = 30_000 if sys.platform == "win32" else 1_000_000


class Outcome(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TestCase:
    __test__ = False  # not a pytest test class, despite the name

    nodeid: str
    outcome: Outcome
    duration: float = 0.0
    message: str = ""
    details: str = ""
    file: str | None = None
    line: int | None = None  # 1-based


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    cases: tuple[TestCase, ...] = ()
    duration: float = 0.0
    #: pytest exited abnormally without writing a report (not installed, crashed, ...)
    crashed: bool = False

    @property
    def counts(self) -> Counter[Outcome]:
        return Counter(case.outcome for case in self.cases)

    @property
    def problems(self) -> tuple[TestCase, ...]:
        return tuple(c for c in self.cases if c.outcome in (Outcome.FAILED, Outcome.ERROR))

    @property
    def ok(self) -> bool:
        return self.exit_code in (EXIT_OK, EXIT_NO_TESTS_COLLECTED)


def run_pytest(
    tests: Sequence[str],
    *,
    cwd: Path,
    python: str = sys.executable,
    args: Sequence[str] = (),
    known_tests: Sequence[str] = (),
) -> RunResult:
    """Run ``python -m pytest`` on ``tests`` (all tests if empty), streaming its output.

    Test paths are split across several pytest invocations only if they would exceed
    the operating system's command-line length limit. ``known_tests`` (paths relative to
    ``cwd``) help map pytest's paths back when its rootdir is not ``cwd``.
    """
    started = time.monotonic()
    batches = _batches(list(tests), fixed_length=len(python) + sum(len(a) + 3 for a in args) + 200)
    results = [_run_once(batch, cwd=cwd, python=python, args=args) for batch in batches]
    known = [*tests, *known_tests]
    return RunResult(
        exit_code=_combine_exit_codes([r.exit_code for r in results]),
        cases=tuple(_rebase(case, cwd, known) for r in results for case in r.cases),
        duration=time.monotonic() - started,
        crashed=any(r.crashed for r in results),
    )


def _rebase(case: TestCase, cwd: Path, known: Sequence[str]) -> TestCase:
    """Make ``case.file`` relative to ``cwd``.

    pytest reports paths relative to its *rootdir*, which is wherever the nearest ini
    file lives. With e.g. ``tests/pytest.ini`` it says ``test_x.py`` for
    ``tests/test_x.py``, which would put GitHub annotations on a non-existent file.
    """
    file = case.file
    if not file or (cwd / file).is_file():
        return case
    matches = {k for k in known if k.endswith("/" + file)}
    if len(matches) != 1:
        return case
    (rebased,) = matches
    nodeid = rebased + case.nodeid[len(file) :] if case.nodeid.startswith(file) else case.nodeid
    return dataclasses.replace(case, file=rebased, nodeid=nodeid)


def _batches(tests: list[str], fixed_length: int) -> list[list[str]]:
    if not tests:
        return [[]]
    batches: list[list[str]] = [[]]
    length = fixed_length
    for test in tests:
        if batches[-1] and length + len(test) + 3 > MAX_COMMAND_LENGTH:
            batches.append([])
            length = fixed_length
        batches[-1].append(test)
        length += len(test) + 3
    if len(batches) > 1:
        log.info("command line too long for one pytest run; using %d batches", len(batches))
    return batches


def _run_once(tests: list[str], *, cwd: Path, python: str, args: Sequence[str]) -> RunResult:
    with tempfile.TemporaryDirectory(prefix="karma-") as tmp:
        report = Path(tmp) / "junit.xml"
        command = [
            python,
            "-m",
            "pytest",
            *args,
            f"--junitxml={report}",
            # xunit1 records each test's file and line, used for annotations.
            "-o",
            "junit_family=xunit1",
            *tests,
        ]
        log.debug("running %s", " ".join(command))
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            exit_code = subprocess.call(command, cwd=cwd)
        except OSError as exc:
            log.error("could not start pytest with %s: %s", python, exc)
            return RunResult(exit_code=EXIT_INTERNAL_ERROR, crashed=True)
        if not report.exists():
            crashed = exit_code not in (EXIT_OK, EXIT_NO_TESTS_COLLECTED)
            return RunResult(exit_code=exit_code, crashed=crashed)
        return RunResult(exit_code=exit_code, cases=tuple(parse_junit(report)))


def _combine_exit_codes(codes: Sequence[int]) -> int:
    for severe in (EXIT_INTERNAL_ERROR, EXIT_USAGE_ERROR, EXIT_INTERRUPTED):
        if severe in codes:
            return severe
    if any(code not in (EXIT_OK, EXIT_NO_TESTS_COLLECTED) for code in codes):
        return EXIT_TESTS_FAILED if EXIT_TESTS_FAILED in codes else max(codes)
    return EXIT_NO_TESTS_COLLECTED if all(c == EXIT_NO_TESTS_COLLECTED for c in codes) else EXIT_OK


def parse_junit(path: Path) -> list[TestCase]:
    """Parse a pytest JUnit XML report (either ``junit_family``)."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        log.warning("could not read pytest report %s: %s", path, exc)
        return []
    return [_test_case(element) for element in root.iter("testcase")]


def _test_case(element: ET.Element) -> TestCase:
    classname = element.get("classname", "")
    name = element.get("name", "")
    raw_file = element.get("file")
    file = raw_file.replace("\\", "/") if raw_file else None  # pytest uses os.sep
    line_attr = element.get("line")
    line = int(line_attr) + 1 if line_attr and line_attr.isdigit() else None

    outcome = Outcome.PASSED
    message = details = ""
    for child in element:
        tag = child.tag
        if tag in ("failure", "error", "skipped"):
            outcome = {"failure": Outcome.FAILED, "error": Outcome.ERROR}.get(tag, Outcome.SKIPPED)
            message = child.get("message") or ""
            details = (child.text or "").strip()
            if outcome is not Outcome.SKIPPED:
                break

    try:
        duration = float(element.get("time", "0"))
    except ValueError:
        duration = 0.0
    return TestCase(
        nodeid=_nodeid(classname, name, file),
        outcome=outcome,
        duration=duration,
        message=_summary(message, details),
        details=details,
        file=file,
        line=line,
    )


def _nodeid(classname: str, name: str, file: str | None) -> str:
    """Rebuild pytest's node id: ``tests.test_x.TestA`` + ``test_b`` -> ``...py::TestA::test_b``."""
    if file:
        module = file.removesuffix(".py").replace("/", ".")
        if name in (module, file):
            return file  # a collection error for the whole module
        if classname == module:
            return f"{file}::{name}"
        if classname.startswith(module + "."):
            return f"{file}::{classname[len(module) + 1 :].replace('.', '::')}::{name}"
        return f"{file}::{name}"
    return f"{classname}::{name}" if classname else name


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _summary(message: str, details: str) -> str:
    """A one-line reason; for pytest's generic messages, dig the real error out."""
    if message and message not in ("collection failure", "failed on setup with"):
        return _first_line(message)
    # pytest marks the lines of the actual error with a leading "E".
    for line in details.splitlines():
        if line.startswith("E "):
            return line[1:].strip()
    return _first_line(message) or _first_line(details)
