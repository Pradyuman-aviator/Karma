import argparse
import os
import subprocess
import sys
from pathlib import Path

from karma.cache import CACHE_FILE, ImportCache
from karma.errors import KarmaError
from karma.git import get_changes
from karma.reporter import Reporter, TestResult
from karma.selector import get_affected_tests
from karma.graph import DependencyGraph, build_graph


def _write_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def _load_graph(repo: str, deleted: tuple[str, ...]) -> DependencyGraph:
    root = Path(repo)
    return build_graph(root, deleted=deleted, cache=ImportCache(root / CACHE_FILE))


def _run_pytest(test_files: list[str], repo: str) -> Reporter:
    reporter = Reporter()
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *test_files]
    result = subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
    )

    combined = (result.stdout or "") + (result.stderr or "")
    if combined.strip():
        print(combined)

    if result.returncode == 0:
        for test_file in test_files:
            reporter.add_result(TestResult(name=test_file, passed=True))
    else:
        # Mark all selected files failed when the suite exits non-zero;
        # pytest output is printed above for diagnosis.
        error = combined.strip().splitlines()[-1] if combined.strip() else "pytest failed"
        for test_file in test_files:
            reporter.add_result(TestResult(name=test_file, passed=False, error_message=error))
    return reporter


def cmd_run(args: argparse.Namespace) -> None:
    repo = str(Path(args.repo).resolve())

    changes = get_changes(args.base, args.head, cwd=Path(repo))
    graph = _load_graph(repo, changes.deleted)
    affected_tests = [t for t in get_affected_tests(list(changes.all), graph) if t in graph.files]

    test_files_value = " ".join(affected_tests)
    _write_github_output("test_files", test_files_value)

    if not affected_tests:
        print("[Karma] No affected tests found.")
        _write_github_output("tests-run", "0")
        if args.ci:
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("## Karma Test Results\n")
                    f.write("- No affected tests to run\n")
        sys.exit(0)

    print(f"[Karma] Affected tests: {test_files_value}")
    _write_github_output("tests-run", str(len(affected_tests)))

    reporter = _run_pytest(affected_tests, repo=repo)
    reporter.print_summary()
    if args.ci:
        reporter.write_github_summary()
    reporter.exit()


def cmd_select(args: argparse.Namespace) -> None:
    """Select affected tests and print them (no execution)."""
    repo = str(Path(args.repo).resolve())

    changes = get_changes(args.base, args.head, cwd=Path(repo))
    graph = _load_graph(repo, changes.deleted)
    affected_tests = [t for t in get_affected_tests(list(changes.all), graph) if t in graph.files]

    if affected_tests:
        print(" ".join(affected_tests))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Karma Test Selection Engine CLI")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(flags: argparse.ArgumentParser) -> None:
        flags.add_argument("--base", default="main", help="Base git commit/branch (default: main)")
        flags.add_argument("--head", default=None, help="Head commit (default: the working tree)")
        flags.add_argument("--repo", default=".", help="Repository root directory (default: .)")
        flags.add_argument(
            "--ci", action="store_true", help="Enable CI mode (GitHub summary, outputs)"
        )

    run_parser = subparsers.add_parser("run", help="Select and run affected tests")
    add_common(run_parser)
    run_parser.set_defaults(func=cmd_run)

    select_parser = subparsers.add_parser("select", help="Print affected test files only")
    add_common(select_parser)
    select_parser.set_defaults(func=cmd_select)

    # Backward-compatible top-level flags (default: select-only behavior)
    add_common(parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if hasattr(args, "func"):
            args.func(args)
        else:
            # No subcommand: keep prior select-and-print behavior
            cmd_select(args)
    except KarmaError as exc:
        print(f"[Karma] error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    main()
