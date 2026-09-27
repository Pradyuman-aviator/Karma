"""Human-facing output: console summaries and GitHub Actions integration."""

from __future__ import annotations

import html
import os
import re
import secrets
import sys
import textwrap
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TextIO

from karma.diagnose import Diagnosis, Hunk, Suspect
from karma.git import to_relative
from karma.risk import Risk
from karma.runner import Outcome, RunResult, TestCase
from karma.selector import Selection

MAX_SUMMARY_ROWS = 50
MAX_ANNOTATIONS = 50  # GitHub shows at most 10 per step and 50 per job anyway
MAX_HUNK_LINES = 10  # of a suspect's diff, in the console


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
        (Outcome.FLAKY, "flaky"),
        (Outcome.QUARANTINED, "quarantined"),
    ):
        if counts[outcome] or outcome not in (Outcome.FLAKY, Outcome.QUARANTINED):
            print(f"  {label:<11} {counts[outcome]:>6}", file=stream)
    print(f"  {'time':<11} {result.duration:>5.1f}s", file=stream)
    for case in (*result.problems, *result.warnings)[:MAX_SUMMARY_ROWS]:
        detail = f" - {case.message}" if case.message else ""
        print(f"  {case.outcome.value.upper()} {case.nodeid}{detail}", file=stream)
    if result.crashed:
        print(
            f"  pytest exited with code {result.exit_code} without producing results "
            "(is pytest installed in this Python environment?)",
            file=stream,
        )


NO_SUSPECT = "nothing in this change is linked to it: it may predate the change, or be flaky"


def format_diagnoses(diagnoses: Sequence[Diagnosis], stream: TextIO | None = None) -> str:
    """Each failure with its evidence: why it ran, its history, the suspect changes."""
    stream = stream or sys.stderr
    arrow = "←" if _can_encode(stream, "←") else "<-"
    rule = "─" if _can_encode(stream, "─") else "-"
    lines = [f"\n{rule * 17} karma diagnosis {rule * 17}"]
    for d in diagnoses:
        lines += ["", d.case.nodeid, _field("error", d.case.message or d.case.outcome.value)]
        if d.chain:
            lines.append(_field("ran for", f" {arrow} ".join(d.chain) + "  (changed)"))
        if d.history:
            lines.append(_field("history", d.history))
        for i, suspect in enumerate(d.suspects):
            lines.append(
                _field("suspect" if i == 0 else "", f"{_where(suspect)}  {suspect.reason}")
            )
            if i == 0 and suspect.hunk is not None:
                lines += [" " * 13 + text for text in _hunk_excerpt(suspect.hunk, suspect.line)]
        if not d.suspects:
            lines.append(_field("suspect", NO_SUSPECT))
        if d.same_failure:
            lines.append(_field("same", f"also fails this way: {_listing(d.same_failure, 3)}"))
        explanation = d.explanation
        if explanation is not None:
            about = f"{explanation.kind}, {explanation.confidence} confidence, {explanation.model}"
            lines += [_field("ai", explanation.summary), _field("", f"({about})")]
            for label, text in (("cause", explanation.cause), ("fix", explanation.fix)):
                if text:
                    lines += _wrapped(f"{label}: {text}")
            if explanation.path:
                at = f":{explanation.line}" if explanation.line else ""
                lines.append(_field("", f"look at {explanation.path}{at}"))
    return "\n".join(lines) + "\n"


def _field(label: str, text: str) -> str:
    return f"  {label:<10} {text}"


def _where(suspect: Suspect) -> str:
    return f"{suspect.path}:{suspect.line}" if suspect.line else suspect.path


def _listing(items: Sequence[str], shown: int) -> str:
    more = len(items) - shown
    return ", ".join(items[:shown]) + (f" and {more} more" if more > 0 else "")


def _wrapped(text: str) -> list[str]:
    indent = " " * 13
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        lines += textwrap.wrap(paragraph, 88, initial_indent=indent, subsequent_indent=indent)
    return lines


def _hunk_excerpt(hunk: Hunk, line: int | None) -> list[str]:
    """At most :data:`MAX_HUNK_LINES` lines of a diff hunk, centred on ``line``."""
    body = hunk.text.splitlines()[1:]
    if not body:
        return []
    numbers: list[int] = []  # the new-file line each body line sits at
    current = hunk.start
    for text in body:
        numbers.append(current)
        if not text.startswith(("-", "\\")):
            current += 1
    target = line if line is not None else (hunk.touched or (hunk.start,))[0]
    centre = min(
        range(len(body)),
        key=lambda i: (abs(numbers[i] - target), not body[i].startswith(("+", "-"))),
    )
    start = max(0, min(centre - MAX_HUNK_LINES // 2, len(body) - MAX_HUNK_LINES))
    return body[start : start + MAX_HUNK_LINES]


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
    """Emit workflow commands so results show inline on the pull request.

    Real failures are ``::error``; flaky and quarantined failures are ``::warning``.
    """
    stream = stream or sys.stdout
    for case in list(cases)[:MAX_ANNOTATIONS]:
        props = [f"title={_escape_property(f'{case.nodeid} {case.outcome.value}')}"]
        if case.file:
            props.insert(0, f"file={_escape_property(_workspace_file(root, case.file))}")
            if case.line:
                props.insert(1, f"line={case.line}")
        message = case.details or case.message or f"{case.nodeid} {case.outcome.value}"
        if case.outcome is Outcome.FLAKY:
            message = f"{case.message} (flaky: does not fail the build)"
        level = "warning" if case.outcome in (Outcome.FLAKY, Outcome.QUARANTINED) else "error"
        print(f"::{level} {','.join(props)}::{_escape_data(message)}", file=stream)


def emit_diagnosis_annotations(
    diagnoses: Sequence[Diagnosis], root: Path, stream: TextIO | None = None
) -> None:
    """Mark each failure's prime suspect, and the line an AI points at, on the diff."""
    stream = stream or sys.stdout
    for d in diagnoses:
        spots: list[tuple[str, int, str]] = []
        suspect = next((s for s in d.suspects if s.line), None)
        if suspect is not None and suspect.line is not None:
            spots.append((suspect.path, suspect.line, f"{suspect.reason}. {d.case.message}"))
        explanation = d.explanation
        if explanation is not None and explanation.path and explanation.line:
            path = to_relative(explanation.path, root)  # models can name files that don't exist
            if path is not None and (root / path).is_file():
                advice = f"{explanation.summary} Fix: {explanation.fix}".strip().removesuffix(
                    "Fix:"
                )
                if spots and spots[0][:2] == (path, explanation.line):
                    spots[0] = (path, explanation.line, f"{spots[0][2]}\n{advice.strip()}")
                else:
                    spots.append((path, explanation.line, advice.strip()))
        title = _escape_property(f"Karma: may have broken {d.case.nodeid}")
        for file, line, message in spots:
            props = (
                f"file={_escape_property(_workspace_file(root, file))},line={line},title={title}"
            )
            print(f"::notice {props}::{_escape_data(message)}", file=stream)


def _workspace_file(root: Path, file: str) -> str:
    """``file`` (relative to ``root``) relative to the workspace, as annotations need."""
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or root)
    try:
        return Path(os.path.relpath(root / file, workspace)).as_posix()
    except ValueError:
        return file


def _code(text: str) -> str:
    return f"<code>{html.escape(text)}</code>"


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks inside ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _text(text: str) -> str:
    """Plain text for Markdown: no HTML gets through, and quotes stay readable."""
    return html.escape(text, quote=False)


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in _text(text).splitlines())


def diagnosis_markdown(diagnoses: Sequence[Diagnosis]) -> str:
    """The "Diagnosis" section of the step summary."""
    if not diagnoses:
        return ""
    parts = ["### 🔎 Diagnosis", ""]
    for d in diagnoses:
        parts += [f"**{_code(d.case.nodeid)}**: {_text(d.case.message)}", ""]
        if d.chain:
            parts.append(f"- **Why it ran:** {' ← '.join(_code(p) for p in d.chain)} (changed)")
        if d.history:
            parts.append(f"- **History:** {_text(d.history)}")
        for suspect in d.suspects:
            parts.append(f"- **Suspect:** {_code(_where(suspect))}: {_text(suspect.reason)}")
        if not d.suspects:
            parts.append(f"- **Suspect:** {NO_SUSPECT}")
        if d.same_failure:
            same = _listing([_code(n) for n in d.same_failure], 10)
            parts.append(f"- **Fails the same way:** {same}")
        parts.append("")
        hunk = next((s.hunk for s in d.suspects if s.hunk is not None), None)
        if hunk is not None:
            fence = _fence(hunk.text)
            parts += [
                f"<details><summary>The suspect change in {_code(hunk.path)}</summary>",
                "",
                f"{fence}diff",
                hunk.text,
                fence,
                "",
                "</details>",
                "",
            ]
        explanation = d.explanation
        if explanation is not None:
            about = f"{explanation.kind}, {explanation.confidence} confidence"
            parts += [f"> **🤖 {_text(explanation.model)}** ({about})", ">"]
            parts.append(_quote(explanation.summary))
            if explanation.cause:
                parts += [">", _quote(explanation.cause)]
            if explanation.fix:
                parts += [">", _quote(f"Fix: {explanation.fix}")]
            parts.append("")
    return "\n".join(parts) + "\n"


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
    diagnoses: Sequence[Diagnosis] = (),
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
            *(
                [f"| 🔁 Flaky (passed on retry) | {counts[Outcome.FLAKY]} |"]
                if counts[Outcome.FLAKY]
                else []
            ),
            *(
                [f"| 🧪 Quarantined failures | {counts[Outcome.QUARANTINED]} |"]
                if counts[Outcome.QUARANTINED]
                else []
            ),
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
        if diagnoses:
            parts.append(diagnosis_markdown(diagnoses))
        warnings = result.warnings
        if warnings:
            parts += [
                "### ⚠️ Flaky and quarantined tests",
                "",
                "These failed but did not fail the build. Fix them, then remove any entries "
                "from the quarantine file (`karma flaky`).",
                "",
            ]
            for case in warnings[:MAX_SUMMARY_ROWS]:
                label = "flaky" if case.outcome is Outcome.FLAKY else "quarantined"
                parts.append(f"- {_code(case.nodeid)}: {label}. {html.escape(case.message)}")
            parts.append("")
    return "\n".join(parts) + "\n"
