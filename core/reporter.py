# CI Result Formatter for Karma

import sys
import os
import io
from dataclasses import dataclass, field
from typing import List


# How a single test result looks
@dataclass
class TestResult:
    name: str
    passed: bool
    skipped: bool = False
    error_message: str = ""




class Reporter:
    def __init__(self):
        self.results: List[TestResult] = []

    def add_result(self, result: TestResult):
        self.results.append(result)

    def print_summary(self):
        # Ensure emojis render on Windows terminals
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')

        passed  = sum(1 for r in self.results if r.passed)
        failed  = sum(1 for r in self.results if not r.passed and not r.skipped)
        skipped = sum(1 for r in self.results if r.skipped)

        print("\n========== KARMA TEST SUMMARY ==========")
        print(f"  ✅ Passed  : {passed}")
        print(f"  ❌ Failed  : {failed}")
        print(f"  ⏭️  Skipped : {skipped}")
        print("=========================================\n")

        for r in self.results:
            if not r.passed and not r.skipped:
                print(f"  FAIL: {r.name}")
                print(f"        {r.error_message}")

    def write_github_summary(self):
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not summary_path:
            return  # Not running in GitHub Actions, skip

        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed and not r.skipped)

        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## ⚡ Karma Test Results\n")
            f.write(f"- ✅ **Passed**: {passed}\n")
            f.write(f"- ❌ **Failed**: {failed}\n")

    def exit(self):
        failed = sum(1 for r in self.results if not r.passed and not r.skipped)
        sys.exit(1 if failed > 0 else 0)
