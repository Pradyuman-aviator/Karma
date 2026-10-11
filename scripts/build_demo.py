"""Build the website's function-level demo (docs/demo.json) by running Karma for real.

A small billing project is written to a temporary git repository. For every function,
method and constant in it, the script changes that one symbol and records what Karma
selects at file and at function granularity, with the reasons. The website replays
these results, so the demo shows exactly what the tool does.

    python scripts/build_demo.py          # update docs/demo.json
    python scripts/build_demo.py --check  # exit 1 if docs/demo.json is out of date
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karma.config import Config  # noqa: E402
from karma.git import get_changes  # noqa: E402
from karma.graph import build_graph  # noqa: E402
from karma.selector import select_tests  # noqa: E402

OUTPUT = ROOT / "docs" / "demo.json"

PROJECT: dict[str, str] = {
    "billing/__init__.py": "",
    "billing/money.py": """\
from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount):
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def format_eur(cents):
    return f"€{cents / 100:.2f}"
""",
    "billing/tax.py": """\
RATES = {"eu": 0.20, "uk": 0.20, "us": 0.07}


def rate(region):
    return RATES.get(region, 0.0)


def tax_for(amount, region):
    return amount * rate(region)
""",
    "billing/discounts.py": """\
def loyalty(amount, years):
    return amount * min(years, 10) * 0.01


def coupon(code):
    return 5.0 if code == "WELCOME5" else 0.0
""",
    "billing/invoice.py": """\
from billing.discounts import coupon, loyalty
from billing.money import format_eur, to_cents
from billing.tax import tax_for


class Invoice:
    def __init__(self, amount, region, years=0, code=None):
        self.amount = amount
        self.region = region
        self.years = years
        self.code = code

    def subtotal(self):
        return self.amount - loyalty(self.amount, self.years) - coupon(self.code)

    def total(self):
        return self.subtotal() + tax_for(self.subtotal(), self.region)

    def render(self):
        return format_eur(to_cents(self.total()))


def quick_total(amount, region):
    return amount + tax_for(amount, region)
""",
    "billing/reports.py": """\
from billing.money import format_eur


def summary(invoices):
    return format_eur(sum(invoice.amount for invoice in invoices) * 100)
""",
    "tests/conftest.py": """\
import pytest

from billing.invoice import Invoice


@pytest.fixture
def eu_invoice():
    return Invoice(100.0, "eu")
""",
    "tests/test_tax.py": """\
from billing.tax import rate, tax_for


def test_known_region():
    assert rate("eu") == 0.20


def test_unknown_region():
    assert rate("mars") == 0.0


def test_tax_for():
    assert tax_for(50, "us") == 3.5
""",
    "tests/test_discounts.py": """\
from billing.discounts import coupon, loyalty


def test_loyalty_caps_at_ten_years():
    assert loyalty(100, 25) == 10


def test_coupon():
    assert coupon("WELCOME5") == 5.0
""",
    "tests/test_money.py": """\
from billing.money import format_eur, to_cents


def test_to_cents_rounds():
    assert to_cents(0.125) == 13


def test_format_eur():
    assert format_eur(1999) == "€19.99"
""",
    "tests/test_invoice.py": """\
from billing.invoice import quick_total


def test_total(eu_invoice):
    assert eu_invoice.total() == 115.0


def test_render(eu_invoice):
    assert eu_invoice.render() == "€115.00"


def test_quick_total():
    assert quick_total(10, "uk") == 12.0
""",
    "tests/test_reports.py": """\
from billing.reports import summary


class Amount:
    def __init__(self, amount):
        self.amount = amount


def test_summary():
    assert summary([Amount(1), Amount(2)]) == "€3.00"
""",
}


def symbols(source: str) -> list[dict[str, Any]]:
    """Each changeable symbol: top-level functions and constants, and methods."""
    found: list[dict[str, Any]] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            found.append(_symbol(node.name, node, "function"))
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            found.append(_symbol(node.targets[0].id, node, "constant"))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name != "__init__":
                    found.append(_symbol(f"{node.name}.{member.name}", member, "method"))
    return found


def _symbol(name: str, node: ast.stmt, kind: str) -> dict[str, Any]:
    return {"name": name, "kind": kind, "start": node.lineno, "end": node.end_lineno}


def mutate(source: str, symbol: dict[str, Any]) -> str:
    """A behaviour change inside one symbol (the demo only analyses it)."""
    lines = source.splitlines(keepends=True)
    if symbol["kind"] == "constant":
        index = symbol["start"] - 1
        lines[index] = lines[index].replace("0.07", "0.08", 1)
    else:  # change the last line of the body
        index = symbol["end"] - 1
        lines[index] = lines[index].rstrip("\n") + "  # changed\n"
        lines[index] = lines[index].replace("return ", "return 1 + ", 1)
    return "".join(lines)


def git(cwd: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "demo",
        "GIT_AUTHOR_EMAIL": "demo@karma.invalid",
        "GIT_COMMITTER_NAME": "demo",
        "GIT_COMMITTER_EMAIL": "demo@karma.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        check=True,
        env=env,
        capture_output=True,
    )


def selection(root: Path, config: Config) -> dict[str, Any]:
    changes = get_changes("main", cwd=root)
    graph = build_graph(root, source_roots=config.source_roots)
    chosen = select_tests(changes, graph, config, root)
    return {
        "files": list(chosen.tests),
        "targets": chosen.targets(),
        "file_reasons": {t: list(c) for t, c in chosen.reasons.items()},
        "item_reasons": {t: list(c) for t, c in chosen.item_reasons.items()},
        "narrowed_out": list(chosen.narrowed_out),
    }


def tests_in(source: str) -> list[str]:
    return [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name.startswith("test")
    ]


def build() -> dict[str, Any]:
    function_level = replace(Config(), granularity="function")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q", "-b", "main")
        for rel, content in PROJECT.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "demo")
        for rel, content in PROJECT.items():
            if not rel.startswith("billing/") or not content:
                continue
            for symbol in symbols(content):
                path = root / rel
                changed = mutate(content, symbol)
                span = slice(symbol["start"] - 1, symbol["end"])
                path.write_text(changed, encoding="utf-8", newline="\n")
                try:
                    results.append(
                        {
                            "file": rel,
                            "symbol": symbol["name"],
                            "kind": symbol["kind"],
                            "start": symbol["start"],
                            "end": symbol["end"],
                            # The symbol's lines before and after the change, for the diff.
                            "before": content.splitlines()[span],
                            "after": changed.splitlines()[span],
                            "file_level": selection(root, Config()),
                            "function_level": selection(root, function_level),
                        }
                    )
                finally:
                    path.write_text(content, encoding="utf-8", newline="\n")
    tests = {rel: tests_in(src) for rel, src in PROJECT.items() if rel.startswith("tests/test_")}
    sources = {rel: src for rel, src in PROJECT.items() if rel.startswith("billing/") and src}
    return {
        "files": PROJECT,
        "symbols": {rel: symbols(src) for rel, src in sources.items()},
        "tests": tests,
        "total_tests": sum(len(names) for names in tests.values()),
        "changes": results,
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if docs/demo.json is stale")
    args = parser.parse_args()
    text = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if args.check:
        if current != text:
            print("docs/demo.json is out of date: run python scripts/build_demo.py")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"updated {OUTPUT.relative_to(ROOT)} ({len(json.loads(text)['changes'])} changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
