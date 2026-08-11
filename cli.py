import argparse
import os
import subprocess
import sys
from pathlib import Path

from core.cache import load_cache, save_cache
from core.git_diff import get_changed_files
from core.reporter import Reporter, TestResult
from core.test_selector import get_affected_tests
from languages.python_parser import build_dependency_map, get_all_python_files


def _write_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def _load_dependency_map(repo: str) -> dict[str, set[str]]:
    py_files = get_all_python_files(root_dir=repo)
    cached = load_cache(py_files, repo=repo)
    if cached is not None:
        return cached

    dep_map = build_dependency_map(root_dir=repo)
    save_cache(dep_map, py_files, repo=repo)
    return dep_map


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
            reporter.add_result(
                TestResult(name=test_file, passed=False, error_message=error)
            )
    return reporter


def cmd_run(args: argparse.Namespace) -> None:
    repo = str(Path(args.repo).resolve())
    head = args.head or "HEAD"

    changed_files = get_changed_files(base=args.base, head=head, repo=repo)
    dep_map = _load_dependency_map(repo)
    affected_tests = get_affected_tests(changed_files=changed_files, dep_map=dep_map)

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
    head = args.head or "HEAD"

    changed_files = get_changed_files(base=args.base, head=head, repo=repo)
    dep_map = _load_dependency_map(repo)
    affected_tests = get_affected_tests(changed_files=changed_files, dep_map=dep_map)

    if affected_tests:
        print(" ".join(affected_tests))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Karma Test Selection Engine CLI")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(flags: argparse.ArgumentParser) -> None:
        flags.add_argument("--base", default="main", help="Base git commit/branch (default: main)")
        flags.add_argument("--head", default="HEAD", help="Head git commit/branch (default: HEAD)")
        flags.add_argument("--repo", default=".", help="Repository root directory (default: .)")
        flags.add_argument("--ci", action="store_true", help="Enable CI mode (GitHub summary, outputs)")

    run_parser = subparsers.add_parser("run", help="Select and run affected tests")
    add_common(run_parser)
    run_parser.set_defaults(func=cmd_run)

    select_parser = subparsers.add_parser("select", help="Print affected test files only")
    add_common(select_parser)
    select_parser.set_defaults(func=cmd_select)

    # Backward-compatible top-level flags (default: select-only behavior)
    add_common(parser)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
        return

    # No subcommand: keep prior select-and-print behavior
    cmd_select(args)


if __name__ == "__main__":
    main()
