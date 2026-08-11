import argparse
from core.git_diff import get_changed_files
from languages.python_parser import build_dependency_map
from core.test_selector import get_affected_tests


def main():
    # Step 1: Arguments
    parser = argparse.ArgumentParser(description="Karma Test Selection Engine CLI")
    parser.add_argument("--base", default="main", help="Base git commit/branch (default: main)")
    parser.add_argument("--head", default="HEAD", help="Head git commit/branch (default: HEAD)")
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--ci", action="store_true", help="Enable CI mode (GitHub summary, no color)")
    args = parser.parse_args()

    # Step 2: Changed files
    changed_files = get_changed_files(base=args.base, head=head_arg if (head_arg := args.head) else "HEAD")

    # Step 3: Dependency graph
    dep_map = build_dependency_map(root_dir=args.repo)

    # Step 4: Tests select
    affected_tests = get_affected_tests(changed_files=changed_files, dep_map=dep_map)

    # Step 5: Run affected tests & report results
    from core.reporter import Reporter, TestResult
    import subprocess

    reporter = Reporter()

    for test in affected_tests:
        result = subprocess.run(
            ["python", "-m", "pytest", test, "-q"],
            capture_output=True,
            text=True
        )
        passed = result.returncode == 0
        reporter.add_result(TestResult(
            name=test,
            passed=passed,
            error_message=result.stdout if not passed else ""
        ))

    reporter.print_summary()

    if args.ci:
        reporter.write_github_summary()

    reporter.exit()

if __name__ == "__main__":
    main()