"""Human-facing output: console summaries and GitHub Actions integration."""

from __future__ import annotations

import html
import os
import secrets
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TextIO

from karma.risk import Risk
from karma.runner import Outcome, RunResult, TestCase
from karma.selector import Selection

MAX_SUMMARY_ROWS = 50
MAX_ANNOTATIONS = 50  # GitHub shows at most 10 per step and 50 per job anyway


def _can_encode(stream: TextIO, text: str) -> bool:
    try:
        text.encode(stream.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --------------------------------------------------------------------------- console


def describe_selection(selection: Selection) -> str:
    """One line, e.g. ``4 of 312 test files selected (98.7% skipped)``."""
    if selection.run_all:
        return (
            f"running all {plural(selection.total_tests, 'test file')}: {selection.run_all_reason}"
        )
    changed = plural(len(selection.changed), "changed file")
    return (
        f"{len(selection.tests)} of {plural(selection.total_tests, 'test file')} selected "
        f"({selection.skipped_fraction:.1%} skipped) for {changed}"
    )


def format_risk(risk: Risk) -> str:
    reasons = f": {'; '.join(risk.reasons)}" if risk.reasons else ""
    return f"risk {risk.probability:.0%}{reasons}"


def format_explanation(
    selection: Selection, stream: TextIO | None = None, risks: Sequence[Risk] | None = None
) -> str:
    """Why each test was selected, one indented dependency chain per test.

    ``stream`` is where the text will be written; it decides whether arrows can be
    drawn with Unicode. With ``risks``, tests are listed in risk order with their risk.
    """
    arrow = "←" if _can_encode(stream or sys.stdout, "←") else "<-"
    if selection.run_all:
        return f"All tests selected: {selection.run_all_reason}\n"
    by_test = {risk.test: risk for risk in risks or ()}
    order = [risk.test for risk in risks] if risks else list(selection.tests)
    lines: list[str] = []
    for test in order:
        chain = selection.reasons.get(test, (test,))
        risk = by_test.get(test)
        lines.append(f"{test}  ({format_risk(risk)})" if risk else test)
        if len(chain) == 1:
            lines.append("    (changed)")
        for i, step in enumerate(chain[1:], start=1):
            suffix = "  (changed)" if i == len(chain) - 1 else ""
            lines.append(f"    {arrow} {step}{suffix}")
    if selection.no_impact:
        lines.append(f"No test depends on: {', '.join(selection.no_impact)}")
    if not lines:
        lines.append("No tests selected.")
    return "\n".join(lines) + "\n"


def print_run_summary(result: RunResult, stream: TextIO | None = None) -> None:
    # Resolve the default at call time so redirected/captured streams are honoured.
    stream = stream or sys.stderr
    counts = result.counts
    rule = "─" if _can_encode(stream, "─") else "-"
    print(f"\n{rule * 18} karma summary {rule * 18}", file=stream)
    for outcome, label in (
        (Outcome.PASSED, "passed"),
        (Outcome.FAILED, "failed"),
        (Outcome.ERROR, "errors"),
        (Outcome.SKIPPED, "skipped"),
    ):
        print(f"  {label:<8} {counts[outcome]:>6}", file=stream)
    print(f"  {'time':<8} {result.duration:>5.1f}s", file=stream)
    for case in result.problems[:MAX_SUMMARY_ROWS]:
        detail = f" - {case.message}" if case.message else ""
        print(f"  {case.outcome.value.upper()} {case.nodeid}{detail}", file=stream)
    if result.crashed:
        print(
            f"  pytest exited with code {result.exit_code} without producing results "
            "(is pytest installed in this Python environment?)",
            file=stream,
        )


# --------------------------------------------------------------------------- GitHub Actions


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def write_github_outputs(outputs: dict[str, str]) -> None:
    """Append step outputs to ``$GITHUB_OUTPUT`` (multi-line safe)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for name, value in outputs.items():
            delimiter = f"karma_{secrets.token_hex(8)}"
            fh.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def append_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown)


def emit_annotations(cases: Iterable[TestCase], root: Path, stream: TextIO | None = None) -> None:
    """Emit ``::error`` workflow commands so failures show inline on the pull request."""
    stream = stream or sys.stdout
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or root)
    for case in list(cases)[:MAX_ANNOTATIONS]:
        props = [f"title={_escape_property(f'{case.nodeid} {case.outcome.value}')}"]
        if case.file:
            try:
                file = Path(os.path.relpath(root / case.file, workspace)).as_posix()
            except ValueError:
                file = case.file
            props.insert(0, f"file={_escape_property(file)}")
            if case.line:
                props.insert(1, f"line={case.line}")
        message = case.details or case.message or f"{case.nodeid} {case.outcome.value}"
        print(f"::error {','.join(props)}::{_escape_data(message)}", file=stream)


def _code(text: str) -> str:
    return f"<code>{html.escape(text)}</code>"


def selection_markdown(
    selection: Selection, base: str | None = None, risks: Sequence[Risk] | None = None
) -> str:
    lines = []
    if selection.run_all:
        reason = html.escape(str(selection.run_all_reason))
        lines.append(f"Running **all {selection.total_tests}** test files: {reason}")
    else:
        against = f" against {_code(base)}" if base else ""
        lines.append(
            f"**{len(selection.tests)} of {selection.total_tests}** test files selected "
            f"(**{selection.skipped_fraction:.1%}** skipped) for "
            f"{plural(len(selection.changed), 'changed file')}{against}."
        )
    if selection.reasons:
        by_test = {risk.test: risk for risk in risks or ()}
        order = [risk.test for risk in risks] if risks else list(selection.tests)
        risk_header = (" | Risk", " | ---") if risks else ("", "")
        lines += [
            "",
            "<details><summary>Why were these tests selected?</summary>",
            "",
        ]
        if risks:
            lines += ["Ordered by predicted risk: the likeliest failures ran first.", ""]
        lines += [
            f"| Test file | Selected because of{risk_header[0]} |",
            f"| --- | ---{risk_header[1]} |",
        ]
        for test in order[:MAX_SUMMARY_ROWS]:
            chain = selection.reasons.get(test, (test,))
            cause = " ← ".join(_code(p) for p in chain[1:]) or "changed directly"
            risk = by_test.get(test)
            risk_cell = f" | {html.escape(format_risk(risk))}" if risk else ""
            lines.append(f"| {_code(test)} | {cause}{risk_cell} |")
        if len(selection.tests) > MAX_SUMMARY_ROWS:
            lines.append(f"| … and {len(selection.tests) - MAX_SUMMARY_ROWS} more | |")
        lines += ["", "</details>"]
    if selection.no_impact:
        shown = ", ".join(_code(p) for p in selection.no_impact[:MAX_SUMMARY_ROWS])
        lines += ["", f"No test depends on: {shown}"]
    return "\n".join(lines) + "\n"


def run_markdown(
    selection: Selection,
    result: RunResult | None,
    base: str | None = None,
    risks: Sequence[Risk] | None = None,
) -> str:
    """The full GitHub step summary for a ``karma run``."""
    if result is None:
        heading = "⚡ Karma: no tests affected"
    elif result.ok:
        heading = f"⚡ Karma: ✅ {plural(result.counts[Outcome.PASSED], 'test')} passed"
    else:
        heading = f"⚡ Karma: ❌ {plural(len(result.problems), 'test')} failed"
    parts = [f"## {heading}", "", selection_markdown(selection, base, risks)]
    if result is not None:
        counts = result.counts
        parts += [
            "| Result | Tests |",
            "| --- | ---: |",
            f"| ✅ Passed | {counts[Outcome.PASSED]} |",
            f"| ❌ Failed | {counts[Outcome.FAILED]} |",
            f"| ⚠️ Errors | {counts[Outcome.ERROR]} |",
            f"| ⏭️ Skipped | {counts[Outcome.SKIPPED]} |",
            f"| ⏱️ Time | {result.duration:.1f}s |",
            "",
        ]
        if result.crashed:
            parts += [
                f"> pytest exited with code {result.exit_code} without producing results.",
                "",
            ]
        problems = result.problems
        if problems:
            parts += ["### Failures", ""]
            for case in problems[:MAX_SUMMARY_ROWS]:
                body = html.escape(case.details or case.message)
                parts += [
                    f"<details><summary>{_code(case.nodeid)}</summary>",
                    "",
                    f"<pre>{body}</pre>",
                    "</details>",
                    "",
                ]
            if len(problems) > MAX_SUMMARY_ROWS:
                parts += [f"… and {len(problems) - MAX_SUMMARY_ROWS} more.", ""]
    return "\n".join(parts) + "\n"
