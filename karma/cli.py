"""Command-line interface: ``karma run``, ``karma select`` and ``karma graph``."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from karma import __version__
from karma.cache import ImportCache, default_cache_path
from karma.config import Config, load_config
from karma.errors import GitError, KarmaError
from karma.flaky import Entry as FlakyEntry
from karma.flaky import (
    Registry,
    apply_quarantine,
    confirmed_flakes,
    plan_sync,
    watched_cases,
    watched_outcomes,
)
from karma.flaky import to_json as flaky_to_json
from karma.git import ChangeSet, default_base, get_changes, verify_ref
from karma.graph import DependencyGraph, build_graph
from karma.history import History, default_history_path, run_from, run_from_report
from karma.reporter import (
    append_step_summary,
    describe_selection,
    emit_annotations,
    format_explanation,
    plural,
    print_run_summary,
    run_markdown,
    write_github_outputs,
)
from karma.risk import Risk, assess, summarize
from karma.runner import (
    EXIT_NO_TESTS_COLLECTED,
    EXIT_OK,
    EXIT_TESTS_FAILED,
    Outcome,
    RunResult,
    rerun_failures,
    run_pytest,
)
from karma.selector import Selection, is_test_file, select_tests

log = logging.getLogger("karma")

EXIT_KARMA_ERROR = 2
EXIT_INTERRUPTED = 130
COMMANDS = ("run", "select", "graph", "history", "flaky")


@dataclass(frozen=True)
class Plan:
    """Everything needed to report on or run a selection."""

    root: Path
    config: Config
    selection: Selection
    base: str | None


# --------------------------------------------------------------------------- selection


def _resolve_root(repo: str) -> Path:
    root = Path(repo).resolve()
    if not root.is_dir():
        raise KarmaError(f"--repo {repo!r} is not a directory")
    return root


def _build_graph(
    root: Path, config: Config, args: argparse.Namespace, deleted: Sequence[str] = ()
) -> DependencyGraph:
    return build_graph(
        root,
        source_roots=config.source_roots,
        deleted=deleted,
        cache=None if args.no_cache else ImportCache(default_cache_path(root)),
        jobs=args.jobs,
        doctest_modules=config.doctest_modules,
        doctest_globs=config.effective_doctest_globs,
    )


def _changes(args: argparse.Namespace, root: Path) -> tuple[ChangeSet, str | None, bool]:
    """Return ``(changes, base, run_everything)`` for the selection options."""
    if args.files is not None:
        return ChangeSet.from_paths(args.files, root), None, False
    if args.all:
        return ChangeSet(), None, True
    try:
        if args.staged:
            return get_changes(cwd=root, staged=True), None, False
        base: str = args.base or default_base(root)
        changes = get_changes(base, args.head, cwd=root)
    except GitError as exc:
        if args.on_git_error != "run-all":
            raise
        log.warning("%s", exc)
        log.warning("--on-git-error=run-all: running the full suite instead")
        return ChangeSet(), None, True
    log.info(
        "comparing %s against %s (merge base %s)",
        args.head or "the working tree",
        base,
        (changes.merge_base or "?")[:10],
    )
    return changes, base, False


def _plan(args: argparse.Namespace) -> Plan:
    root = _resolve_root(args.repo)
    # `karma run -- -p plugin --doctest-modules` changes what pytest will collect.
    config = load_config(root).with_pytest_args(args.pytest_args)
    changes, base, everything = _changes(args, root)
    graph = _build_graph(root, config, args, changes.deleted)
    if everything:
        tests = tuple(sorted(f for f in graph.files if is_test_file(f, config)))
        reason = "--all was requested" if args.all else "changes could not be determined"
        selection = Selection(tests=tests, total_tests=len(tests), run_all_reason=reason)
    else:
        selection = select_tests(changes, graph, config)
    if not selection.total_tests:
        log.warning(
            "found no test files (test patterns: %s); if pytest finds tests here, set "
            "[tool.karma] test-patterns",
            " ".join(config.test_patterns),
        )
    log.info("%s", describe_selection(selection))
    return Plan(root, config, selection, base)


def _github_outputs(selection: Selection, tests_run: int, result: RunResult | None = None) -> None:
    flaky = [c.nodeid for c in result.cases if c.outcome is Outcome.FLAKY] if result else []
    write_github_outputs(
        {
            "test_files": " ".join(selection.tests),
            "tests-run": str(tests_run),
            "selected-count": str(len(selection.tests)),
            "total-count": str(selection.total_tests),
            "run-all": str(selection.run_all).lower(),
            "flaky-tests": " ".join(flaky),
        }
    )


# --------------------------------------------------------------------------- commands


def _risks(args: argparse.Namespace, plan: Plan) -> list[Risk] | None:
    """Predicted risk per selected test, if prioritisation is on (else ``None``)."""
    if not (args.prioritize or plan.config.prioritize) or not plan.selection.tests:
        return None
    if plan.selection.run_all:
        log.info("full run: pytest decides the order (prioritisation applies to targeted runs)")
        return None
    history = History.load(default_history_path(plan.root))
    risks = assess(plan.selection, history)
    log.info(
        "prioritised by risk using %s",
        plural(len(history.runs), "recorded run") if history.runs else "built-in priors",
    )
    return risks


def cmd_select(args: argparse.Namespace) -> int:
    plan = _plan(args)
    selection = plan.selection
    risks = _risks(args, plan)
    ordered = [risk.test for risk in risks] if risks else list(selection.tests)
    if args.explain:
        sys.stdout.write(format_explanation(selection, sys.stdout, risks))
    elif args.format == "json":
        data = selection.to_dict()
        if risks:
            data["tests"] = ordered
            data["risk"] = {
                r.test: {"probability": round(r.probability, 4), "reasons": list(r.reasons)}
                for r in risks
            }
        print(json.dumps(data, indent=2))
    elif ordered:
        print(("\n" if args.format == "lines" else " ").join(ordered))
    if args.ci:
        _github_outputs(selection, tests_run=0)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    plan = _plan(args)
    selection = plan.selection
    risks = _risks(args, plan)
    if args.explain:
        sys.stderr.write(format_explanation(selection, sys.stderr, risks))

    # A full run always goes to pytest, even if Karma recognised no test files itself:
    # pytest's discovery is the authority, and an empty "full run" must never pass.
    if not selection.tests and not selection.run_all:
        log.info("no affected tests; nothing to run")
        if args.ci:
            _github_outputs(selection, tests_run=0)
            append_step_summary(run_markdown(selection, None, plan.base))
        return EXIT_OK

    # For a full run, let pytest discover tests itself so its testpaths setting applies.
    if selection.run_all:
        targets: list[str] = []
    else:  # the likeliest failures first, so they are found in the first seconds
        targets = [r.test for r in risks] if risks else list(selection.tests)
    pytest_args = [*plan.config.pytest_args, *args.pytest_args]
    result = run_pytest(
        targets,
        cwd=plan.root,
        python=args.python,
        args=pytest_args,
        known_tests=selection.tests,
        report=plan.config.junitxml,
    )

    # Flakiness: re-run only what failed; a pass on retry means flaky, not broken.
    retries = plan.config.retries if args.retries is None else args.retries
    if retries and result.problems:
        result = rerun_failures(
            result,
            retries,
            lambda nodeids: run_pytest(
                nodeids,
                cwd=plan.root,
                python=args.python,
                args=pytest_args,
                known_tests=selection.tests,
            ),
        )
    registry = Registry.load(plan.root / plan.config.quarantine_file)
    result = apply_quarantine(result, registry)

    print_run_summary(result)
    if not args.no_history and result.cases:
        _record_history(plan, result, watched_cases(result, registry))
    if args.ci:
        _github_outputs(selection, tests_run=len(selection.tests), result=result)
        emit_annotations((*result.problems, *result.warnings), plan.root)
        append_step_summary(run_markdown(selection, result, plan.base, risks))
    flaky = [c for c in result.cases if c.outcome is Outcome.FLAKY]
    if flaky and (args.fail_on_flaky or plan.config.fail_on_flaky) and result.exit_code == EXIT_OK:
        log.error("%s flaky: failing because of --fail-on-flaky", plural(len(flaky), "test"))
        return EXIT_TESTS_FAILED
    # "No tests collected" is fine for a targeted run (e.g. -m deselected them), but a
    # full run that collects nothing must fail, exactly as plain pytest does.
    if result.exit_code == EXIT_NO_TESTS_COLLECTED and not selection.run_all:
        return EXIT_OK
    return result.exit_code


def _record_history(plan: Plan, result: RunResult, watched: dict[str, str]) -> None:
    try:
        commit: str | None = verify_ref("HEAD", plan.root)
    except GitError:
        commit = None
    history = History.load(default_history_path(plan.root))
    history.append(run_from(result, plan.selection, commit, watched=watched))


def cmd_flaky(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo)
    config = load_config(root)
    registry = Registry.load(root / config.quarantine_file)
    history = History.load(default_history_path(root))
    action = args.action or "list"

    if action == "quarantine":
        for test_id in args.ids:
            entry = FlakyEntry(test_id, args.reason, datetime.date.today().isoformat(), args.issue)
            if registry.add(entry):
                print(f"quarantined {test_id}")
            else:
                log.warning("%s is already quarantined", test_id)
        registry.save()
        return EXIT_OK
    if action == "release":
        missing = [test_id for test_id in args.ids if not registry.remove(test_id)]
        for test_id in args.ids:
            if test_id not in missing:
                print(f"released {test_id}")
        registry.save()
        if missing:
            raise KarmaError(f"not quarantined: {', '.join(missing)}")
        return EXIT_OK
    if action == "sync":
        plan = plan_sync(registry, history, min_flakes=args.min_flakes, heal_after=args.heal_after)
        for entry in plan.quarantine:
            print(f"quarantine {entry.id}  ({entry.reason})")
        for entry in plan.release:
            print(f"release    {entry.id}  (passed its last {args.heal_after} runs)")
        if not (plan.quarantine or plan.release):
            print("quarantine is up to date")
        elif not args.dry_run:
            for entry in plan.quarantine:
                registry.add(entry)
            for entry in plan.release:
                registry.remove(entry.id)
            registry.save()
        return EXIT_OK

    # list
    flakes = [f for f in confirmed_flakes(history) if not registry.match(f.id)]
    if args.format == "json":
        print(
            json.dumps(
                {
                    "quarantined": flaky_to_json(registry.entries),
                    "confirmed_flaky": [{"id": f.id, "flaky_runs": f.flaky_runs} for f in flakes],
                },
                indent=2,
            )
        )
        return EXIT_OK
    print(f"Quarantined ({len(registry.entries)}) in {config.quarantine_file}:")
    for entry in registry.entries:
        outcomes = list(watched_outcomes(history, entry))[-10:]
        recent = " ".join("." if o == "passed" else "F" for o in outcomes) or "no runs yet"
        details = ", ".join(x for x in (entry.reason, entry.issue) if x)
        print(
            f"  {entry.id}" + (f"  ({details})" if details else "") + f"\n      last runs: {recent}"
        )
    if not registry.entries:
        print("  (none)")
    print(f"\nConfirmed flaky, not quarantined ({len(flakes)}):")
    for flake in flakes:
        print(f"  {flake.id}  (passed on retry in {plural(flake.flaky_runs, 'run')})")
    if not flakes:
        print("  (none; run tests with --retries to detect flaky tests)")
    return EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo)
    history = History.load(default_history_path(root))
    if args.import_reports:
        imported = []
        for report in args.import_reports:
            try:
                imported.append(run_from_report(Path(report), root))
            except (OSError, ValueError) as exc:
                raise KarmaError(f"cannot import {report}: {exc}") from None
        history.merge(imported)
        log.info("imported %s", plural(len(imported), "report"))

    summaries = summarize(history)
    failing = sorted(
        (s for s in summaries if s.failures),
        key=lambda s: (-s.failure_rate, -s.failures, s.test),
    )[: args.top]
    flaky = sorted((s for s in summaries if s.flaky), key=lambda s: (-s.flip_rate, s.test))
    slowest = sorted(
        (s for s in summaries if s.duration),
        key=lambda s: (-(s.duration or 0.0), s.test),
    )[: args.top]

    if args.format == "json":
        print(
            json.dumps(
                {
                    "runs": len(history.runs),
                    "tests": {
                        s.test: {
                            "runs": s.runs,
                            "failures": s.failures,
                            "flips": s.flips,
                            "duration": s.duration,
                            "flaky": s.flaky,
                        }
                        for s in summaries
                    },
                },
                indent=2,
            )
        )
        return EXIT_OK
    if not history.runs:
        print("No test history yet: `karma run` records it in .karma_cache/history.jsonl.")
        return EXIT_OK
    first, last = (
        time.strftime("%Y-%m-%d", time.localtime(run.timestamp))
        for run in (history.runs[0], history.runs[-1])
    )
    print(f"{plural(len(history.runs), 'recorded run')} ({first} to {last})")
    width = max((len(s.test) for s in summaries), default=0) + 2
    if failing:
        print("\nMost failures:")
        for s in failing:
            print(f"  {s.test:<{width}}{s.failures} of {s.runs} runs ({s.failure_rate:.0%})")
    if flaky:
        print("\nFlaky candidates (outcome flips back and forth):")
        for s in flaky:
            print(f"  {s.test:<{width}}flipped {s.flips} times in {s.runs} runs")
    if slowest:
        print("\nSlowest:")
        for s in slowest:
            print(f"  {s.test:<{width}}{s.duration:.2f}s")
    return EXIT_OK


def cmd_graph(args: argparse.Namespace) -> int:
    root = _resolve_root(args.repo)
    graph = _build_graph(root, load_config(root), args)
    render = {"json": graph.to_json, "dot": graph.to_dot, "mermaid": graph.to_mermaid}
    sys.stdout.write(render[args.format]())
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def _add_verbosity(parser: argparse.ArgumentParser, default: object) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-v", "--verbose", action="store_true", default=default, help="show debug logging"
    )
    group.add_argument(
        "-q", "--quiet", action="store_true", default=default, help="only show warnings"
    )


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS: `karma -v run` and `karma run -v` both work without overriding each other.
    _add_verbosity(parser, argparse.SUPPRESS)
    parser.add_argument(
        "--repo", default=".", metavar="DIR", help="project directory to analyse (default: .)"
    )
    parser.add_argument("--no-cache", action="store_true", help="do not read or write the cache")
    parser.add_argument(
        "--jobs", type=int, metavar="N", help="worker processes for parsing (default: CPU count)"
    )


def _add_selection_options(parser: argparse.ArgumentParser) -> None:
    _add_analysis_options(parser)
    source = parser.add_argument_group("what counts as changed")
    source.add_argument(
        "--base",
        metavar="REF",
        help="branch or commit the change will merge into (default: $KARMA_BASE, the pull "
        "request base on GitHub Actions, origin/HEAD, or main/master)",
    )
    source.add_argument(
        "--head", metavar="REF", help="compare this commit instead of the working tree"
    )
    exclusive = source.add_mutually_exclusive_group()
    exclusive.add_argument("--staged", action="store_true", help="only changes staged for commit")
    exclusive.add_argument(
        "--files", nargs="+", metavar="PATH", help="treat these files as changed; skip git"
    )
    exclusive.add_argument("--all", action="store_true", help="select every test")
    source.add_argument(
        "--on-git-error",
        choices=("fail", "run-all"),
        default="fail",
        help="if changes cannot be determined: fail (default) or run the full suite",
    )
    parser.add_argument("--explain", action="store_true", help="show why each test was selected")
    parser.add_argument(
        "--prioritize",
        action="store_true",
        help="order tests by predicted risk of failure, learned from local history",
    )
    parser.add_argument(
        "--ci", action="store_true", help="write GitHub Actions outputs, summary and annotations"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="karma",
        description="Run only the tests affected by your change.",
        epilog="Run 'karma <command> --help' for the options of each command.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_verbosity(parser, False)
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    run = commands.add_parser(
        "run",
        help="select affected tests and run them with pytest",
        description="Select the tests affected by your change and run them with pytest. "
        "Arguments after '--' go to pytest, e.g.: karma run -- -x -n auto",
    )
    _add_selection_options(run)
    run.add_argument(
        "--retries",
        type=int,
        metavar="N",
        help="re-run failed tests up to N times; a pass on retry marks a test flaky "
        "(default: [tool.karma] retries, else 0)",
    )
    run.add_argument(
        "--fail-on-flaky",
        action="store_true",
        help="fail the run if a test is flaky (by default flaky tests only warn)",
    )
    run.add_argument(
        "--no-history",
        action="store_true",
        help="do not record results in .karma_cache/history.jsonl (used for --prioritize)",
    )
    run.add_argument(
        "--python",
        default=sys.executable,
        metavar="EXE",
        help="interpreter used to run pytest (default: the one running Karma)",
    )
    run.set_defaults(func=cmd_run)

    select = commands.add_parser(
        "select",
        help="print the affected tests without running them",
        description="Print the tests affected by your change, e.g.: pytest $(karma select)",
    )
    _add_selection_options(select)
    select.add_argument(
        "--format",
        choices=("text", "lines", "json"),
        default="text",
        help="text: space-separated (default); lines: one per line; json: full details",
    )
    select.set_defaults(func=cmd_select)

    graph = commands.add_parser(
        "graph", help="print the dependency graph", description="Print the dependency graph."
    )
    _add_analysis_options(graph)
    graph.add_argument("--format", choices=("json", "dot", "mermaid"), default="json")
    graph.set_defaults(func=cmd_graph)

    history = commands.add_parser(
        "history",
        help="show the recorded test history; import JUnit reports into it",
        description="Summarise .karma_cache/history.jsonl, the data --prioritize learns "
        "from. --import seeds it from existing JUnit XML reports (e.g. CI artifacts).",
    )
    _add_verbosity(history, argparse.SUPPRESS)
    history.add_argument("--repo", default=".", metavar="DIR", help="project directory")
    history.add_argument(
        "--import", dest="import_reports", nargs="+", metavar="REPORT", help="JUnit XML reports"
    )
    history.add_argument("--format", choices=("text", "json"), default="text")
    history.add_argument("--top", type=int, default=10, metavar="N", help="rows per list")
    history.set_defaults(func=cmd_history)

    flaky = commands.add_parser(
        "flaky",
        help="list, quarantine and release flaky tests",
        description="Manage the quarantine registry (karma-quarantine.toml): quarantined "
        "tests still run, but their failures do not fail the build.",
    )
    _add_verbosity(flaky, argparse.SUPPRESS)
    flaky.add_argument("--repo", default=".", metavar="DIR", help="project directory")
    flaky.add_argument("--format", choices=("text", "json"), default="text")
    flaky.set_defaults(func=cmd_flaky, action=None)
    actions = flaky.add_subparsers(dest="action", metavar="<action>")

    def action_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text)
        # Accept the shared options after the action too (`karma flaky sync --repo x`).
        _add_verbosity(sub, argparse.SUPPRESS)
        sub.add_argument("--repo", default=argparse.SUPPRESS, metavar="DIR", help="project")
        return sub

    quarantine = action_parser("quarantine", "quarantine tests by pytest node id")
    quarantine.add_argument("ids", nargs="+", metavar="ID")
    quarantine.add_argument("--reason", default="", help="why it is quarantined")
    quarantine.add_argument("--issue", default="", help="link to the tracking issue")
    release = action_parser("release", "take tests out of quarantine")
    release.add_argument("ids", nargs="+", metavar="ID")
    sync = action_parser(
        "sync", "quarantine confirmed flaky tests and release healed ones, from the history"
    )
    sync.add_argument(
        "--min-flakes",
        type=int,
        default=2,
        metavar="N",
        help="confirmed flaky in at least N runs (default: 2)",
    )
    sync.add_argument(
        "--heal-after",
        type=int,
        default=10,
        metavar="N",
        help="release after passing N runs in a row (default: 10)",
    )
    sync.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    return parser


def _configure_logging(verbose: bool, quiet: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("karma: %(message)s"))
    logger = logging.getLogger("karma")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO)


def _safe_streams() -> None:
    """Never crash on output: a non-ASCII path piped on Windows uses cp1252 (strict)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    _safe_streams()
    arguments = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if "--" in arguments:
        split = arguments.index("--")
        arguments, pytest_args = arguments[:split], arguments[split + 1 :]
    if not set(COMMANDS) & set(arguments) and not {"-h", "--help", "--version"} & set(arguments):
        arguments.insert(0, "select")  # `karma --base main` keeps its historic meaning

    parser = build_parser()
    args = parser.parse_args(arguments)
    if pytest_args and args.command != "run":
        parser.error("arguments after '--' are only accepted by 'karma run'")
    args.pytest_args = pytest_args
    _configure_logging(args.verbose, args.quiet)

    try:
        exit_code: int = args.func(args)
    except KarmaError as exc:
        log.error("error: %s", exc)
        return EXIT_KARMA_ERROR
    except KeyboardInterrupt:
        log.error("interrupted")
        return EXIT_INTERRUPTED
    return exit_code
