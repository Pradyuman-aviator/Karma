"""Command-line interface: ``karma run``, ``karma select`` and ``karma graph``."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from karma import __version__
from karma.cache import ImportCache, default_cache_path
from karma.config import Config, load_config
from karma.errors import GitError, KarmaError
from karma.git import ChangeSet, default_base, get_changes, verify_ref
from karma.graph import DependencyGraph, build_graph
from karma.history import History, default_history_path, run_from
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
from karma.risk import Risk, assess
from karma.runner import EXIT_NO_TESTS_COLLECTED, EXIT_OK, RunResult, run_pytest
from karma.selector import Selection, is_test_file, select_tests

log = logging.getLogger("karma")

EXIT_KARMA_ERROR = 2
EXIT_INTERRUPTED = 130
COMMANDS = ("run", "select", "graph")


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


def _github_outputs(selection: Selection, tests_run: int) -> None:
    write_github_outputs(
        {
            "test_files": " ".join(selection.tests),
            "tests-run": str(tests_run),
            "selected-count": str(len(selection.tests)),
            "total-count": str(selection.total_tests),
            "run-all": str(selection.run_all).lower(),
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

    print_run_summary(result)
    if not args.no_history and result.cases:
        _record_history(plan, result)
    if args.ci:
        _github_outputs(selection, tests_run=len(selection.tests))
        emit_annotations(result.problems, plan.root)
        append_step_summary(run_markdown(selection, result, plan.base, risks))
    # "No tests collected" is fine for a targeted run (e.g. -m deselected them), but a
    # full run that collects nothing must fail, exactly as plain pytest does.
    if result.exit_code == EXIT_NO_TESTS_COLLECTED and not selection.run_all:
        return EXIT_OK
    return result.exit_code


def _record_history(plan: Plan, result: RunResult) -> None:
    try:
        commit: str | None = verify_ref("HEAD", plan.root)
    except GitError:
        commit = None
    history = History.load(default_history_path(plan.root))
    history.append(run_from(result, plan.selection, commit))


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
