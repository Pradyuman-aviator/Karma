"""Measure how early Karma's risk ordering finds real failures (APFD).

Two steps, so the slow part runs once:

    # 1. break each module in turn, run the full suite, record what failed
    python scripts/prioritization_benchmark.py collect --package src/app --out data.json
    # 2. compare orderings of each selection
    python scripts/prioritization_benchmark.py evaluate data.json

APFD (Average Percentage of Faults Detected) is the standard measure in test
prioritisation research: 1 - TF/n + 1/(2n), where TF is the position of the first test
file that fails among the n selected. 1.0 means the very first test found the bug;
random ordering averages about 0.5. Evaluation is *prequential*: faults are replayed
in a random order and, for each one, Karma predicts using only the history of the
faults before it, then records the outcome, exactly as it would learn in CI.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from safety_check import Project, mutate, working_tree_is_clean

from karma.history import FAILED, PASSED, History, Run, TestRecord
from karma.risk import assess
from karma.selector import Selection


def collect(package: str, tests: str, out: Path) -> int:
    root = Path.cwd()
    if not working_tree_is_clean(root):
        print("refusing to run: commit or stash your changes first", file=sys.stderr)
        return 2
    project = Project(root, tests, sys.executable)
    baseline = project.failing_test_files()
    faults = []
    for module in sorted(p.relative_to(root).as_posix() for p in (root / package).rglob("*.py")):
        if module.rpartition("/")[2] in ("__init__.py", "__main__.py", "conftest.py"):
            continue
        path = root / module
        original = path.read_bytes()
        mutated = mutate(original.decode("utf-8"))
        if mutated is None:
            continue
        try:
            path.write_text(mutated, encoding="utf-8")
            failing = sorted(project.failing_test_files() - baseline)
        finally:
            path.write_bytes(original)
        selection = json.loads(project.karma_json(module))
        faults.append({"module": module, "failing": failing, "selection": selection})
        print(f"{module:<50} {len(failing):>3} failing of {len(selection['tests'])} selected")
    out.write_text(json.dumps(faults, indent=1), encoding="utf-8")
    print(f"wrote {len(faults)} faults to {out}")
    return 0


def first_failure(order: list[str], failing: set[str]) -> float:
    return next(i for i, test in enumerate(order, start=1) if test in failing)


def apfd(n: int, first: float) -> float:
    return 1 - first / n + 1 / (2 * n)


def expected_random_first(n: int, k: int) -> float:
    # With k failing among n in random order, the first failure is at (n+1)/(k+1) on average.
    return (n + 1) / (k + 1)


def evaluate(data: Path, seeds: int) -> int:
    faults = json.loads(data.read_text(encoding="utf-8"))
    # Order only matters when there is a choice, and only failures can be found early.
    usable = [f for f in faults if f["failing"] and len(f["selection"]["tests"]) > 1]
    names = ("alphabetical", "random", "prior", "learned")
    scores: dict[str, list[float]] = {name: [] for name in names}
    firsts: dict[str, list[float]] = {name: [] for name in names}
    for seed in range(seeds):
        replay = usable[:]
        random.Random(seed).shuffle(replay)
        history = History()
        for tick, fault in enumerate(replay):
            sel = fault["selection"]
            selection = Selection(
                tests=tuple(sel["tests"]),
                total_tests=sel["total"],
                changed=tuple(sel["changed"]),
                reasons={t: tuple(chain) for t, chain in sel["reasons"].items()},
            )
            failing = set(fault["failing"]) & set(selection.tests)
            if not failing:
                continue
            n = len(selection.tests)
            positions = {
                "alphabetical": first_failure(sorted(selection.tests), failing),
                "random": expected_random_first(n, len(failing)),
                "prior": first_failure([r.test for r in assess(selection, History())], failing),
                "learned": first_failure([r.test for r in assess(selection, history)], failing),
            }
            for name, position in positions.items():
                firsts[name].append(position)
                scores[name].append(apfd(n, position))
            history.append(
                Run(
                    timestamp=float(tick),
                    tests={
                        t: TestRecord(
                            FAILED if t in failing else PASSED,
                            0.0,
                            len(selection.reasons.get(t, (t,))) - 1,
                        )
                        for t in selection.tests
                    },
                    changed=selection.changed,
                )
            )
    print(f"{len(usable)} faults with a choice of order, replayed with {seeds} random orders\n")
    print(f"{'ordering':<14}{'mean APFD':>10}{'median':>9}{'first failure at':>19}")
    for name in names:
        values = scores[name]
        print(
            f"{name:<14}{statistics.mean(values):>10.3f}{statistics.median(values):>9.3f}"
            f"{statistics.mean(firsts[name]):>13.2f} files"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--package", required=True)
    collect_parser.add_argument("--tests", default="tests")
    collect_parser.add_argument("--out", type=Path, required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("data", type=Path)
    evaluate_parser.add_argument("--seeds", type=int, default=20)
    args = parser.parse_args()
    if args.command == "collect":
        return collect(args.package, args.tests, args.out)
    return evaluate(args.data, args.seeds)


if __name__ == "__main__":
    raise SystemExit(main())
