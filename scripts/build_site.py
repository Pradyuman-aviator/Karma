"""Build the data behind the website (docs/index.html) from the real sources.

Every command and flag comes from Karma's own argument parser, the GitHub Action's
inputs and outputs from action.yml, configuration keys from karma.config, and the
benchmark figures from docs/benchmarks.json. The result is embedded in the page, so
the site always documents exactly what the tool does.

    python scripts/build_site.py          # update docs/index.html
    python scripts/build_site.py --check  # exit 1 if the page is out of date (used in tests)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from karma import __version__  # noqa: E402
from karma.cli import build_parser  # noqa: E402
from karma.config import _KNOWN_KEYS  # noqa: E402
from karma.risk import FEATURES, PRIOR, RECENCY  # noqa: E402

PAGE = ROOT / "docs" / "index.html"
BENCHMARKS = ROOT / "docs" / "benchmarks.json"
SAMPLES = ROOT / "docs" / "samples.json"
START = '<script id="karma-data" type="application/json">'
END = "</script>"

COMMAND_ORDER = ["run", "select", "diagnose", "graph", "history", "flaky"]

EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "run": [
        ("karma run", "Run the tests affected by your branch and uncommitted work"),
        ("karma run --explain", "Show why each test was selected"),
        ("karma run --prioritize -- -x", "Likeliest failures first; stop at the first one"),
        ("karma run --retries 2", "Re-run failures; a pass on retry is reported as flaky"),
        ("karma run --diagnose", "Explain each failure: the changed lines behind it"),
        ("karma run --staged", "Only what is staged for commit (pre-commit hook)"),
        ("karma run --all", "The whole suite, still recorded in the history"),
    ],
    "diagnose": [
        ("karma diagnose", "Explain the last run's failures, all locally"),
        ("karma diagnose --ai ollama --ai-model qwen2.5-coder", "Ask a model on your machine"),
        ("karma diagnose --ai anthropic", "Ask Claude (uses your ANTHROPIC_API_KEY)"),
        ("karma diagnose --ai anthropic --show-prompt", "See exactly what would be sent"),
        ("karma diagnose --report junit.xml --format markdown", "A CI report, as Markdown"),
    ],
    "select": [
        ("pytest $(karma select)", "Hand the selection to your own pytest command"),
        ("karma select --format json", "Tests, reasons and risk for tooling"),
        ("karma select --files app/models.py", "What would a change to this file affect?"),
        ("karma select --base origin/develop --explain", "Compare against another branch"),
    ],
    "graph": [
        ("karma graph --format mermaid", "Paste into a GitHub comment or README"),
        ("karma graph --format dot | dot -Tsvg > deps.svg", "Render with Graphviz"),
    ],
    "history": [
        ("karma history", "Most-failing, flaky and slowest tests"),
        ("karma history --import reports/*.xml", "Seed the history from old CI reports"),
        ("karma history --format json", "Per-test statistics for tooling"),
    ],
    "flaky": [
        ("karma flaky", "Quarantined tests with their recent results"),
        (
            'karma flaky quarantine "tests/test_net.py::test_timeout" --issue https://…',
            "Quarantine a test",
        ),
        ("karma flaky release tests/test_net.py::test_timeout", "It is fixed: release it"),
        ("karma flaky sync --dry-run", "Preview automatic quarantine and release"),
    ],
}

CONFIG_DOCS: dict[str, tuple[str, str, str]] = {
    # key: (type, default, description)
    "test-patterns": ("list", "pytest's python_files", "Which files are tests"),
    "source-roots": ("list", '[".", "src"]', "Where top-level packages live"),
    "exclude": ("list", "[]", "Never selected as tests (still analysed)"),
    "run-all-on": (
        "list",
        "packaging, pytest and dependency files",
        "Changes that run the whole suite (replaces the defaults)",
    ),
    "extend-run-all-on": ("list", "[]", "Add to the full-suite triggers"),
    "mappings": (
        "table",
        "{}",
        "Dependencies imports cannot express, e.g. data files to tests",
    ),
    "pytest-args": ("list", "[]", "Always passed to pytest"),
    "prioritize": ("bool", "false", "Run the tests most likely to fail first"),
    "retries": ("int", "0", "Re-run failed tests; a pass on retry means flaky"),
    "fail-on-flaky": ("bool", "false", "Fail the run when a test is flaky"),
    "quarantine-file": ("string", '"karma-quarantine.toml"', "Where quarantined tests are listed"),
    "diagnose": ("bool", "false", "Explain failures after a run: suspect changes, history"),
    "ai": ("string", "off", "Also ask a language model: anthropic, ollama or openai"),
    "ai-model": ("string", '"claude-sonnet-5" for anthropic', "The model to ask"),
    "ai-url": ("string", "the provider's API", "API base URL, e.g. a server on this machine"),
}


def _option(action: argparse.Action, group: str) -> dict[str, Any]:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        kind = "flag"
    elif action.choices:
        kind = "choice"
    elif action.nargs in ("+", "*"):
        kind = "list"
    else:
        kind = "value"
    default = action.default
    if default in (None, False, argparse.SUPPRESS) or kind == "flag":
        default = None
    if action.dest == "python":
        default = "the Python running Karma"
    return {
        "flags": list(action.option_strings) or [action.dest],
        "positional": not action.option_strings,
        "metavar": action.metavar or (action.dest.upper() if kind != "flag" else None),
        "kind": kind,
        "choices": list(action.choices) if action.choices else None,
        "default": default,
        "help": (action.help or "").replace("%(prog)s", "karma"),
        "group": group,
    }


def _describe(parser: argparse.ArgumentParser, name: str) -> dict[str, Any]:
    groups = {
        id(action): group.title
        for group in parser._action_groups
        for action in group._group_actions
    }
    options, actions = [], []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.dest in ("verbose", "quiet"):
            continue  # global; documented once
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help for a in action._choices_actions}
            actions = [
                {**_describe(sub, f"{name} {sub_name}"), "help": helps.get(sub_name, "")}
                for sub_name, sub in action.choices.items()
            ]
            continue
        title = groups.get(id(action)) or "options"
        options.append(
            _option(action, "general" if title in ("options", "optional arguments") else title)
        )
    return {
        "name": name,
        "description": parser.description or "",
        "options": options,
        "actions": actions,
        "examples": [{"command": c, "what": w} for c, w in EXAMPLES.get(name, [])],
        "pytest_args": name == "run",
    }


def cli_reference() -> list[dict[str, Any]]:
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in sub._choices_actions}
    missing = set(sub.choices) - set(COMMAND_ORDER)
    if missing:
        raise SystemExit(f"new command(s) {sorted(missing)}: add them to COMMAND_ORDER/EXAMPLES")
    return [
        {**_describe(sub.choices[name], name), "help": helps.get(name, "")}
        for name in COMMAND_ORDER
    ]


def action_reference() -> dict[str, list[dict[str, str]]]:
    """Inputs and outputs of action.yml (a small, fixed-shape YAML file)."""
    result: dict[str, list[dict[str, str]]] = {"inputs": [], "outputs": []}
    section: str | None = None
    current: dict[str, str] | None = None
    field: str | None = None
    for line in (ROOT / "action.yml").read_text(encoding="utf-8").splitlines():
        top = re.match(r"^(\w[\w-]*):", line)
        if top:
            section = top.group(1) if top.group(1) in result else None
            current = None
            continue
        if section is None:
            continue
        key = re.match(r"^  ([\w-]+):\s*$", line)
        if key:
            current = {"name": key.group(1), "description": "", "default": ""}
            result[section].append(current)
            field = None
            continue
        prop = re.match(r"^    (description|default):\s*(.*)$", line)
        if prop and current is not None:
            field = prop.group(1)
            value = prop.group(2).strip()
            if value not in (">-", ">", "|"):
                current[field] = value.strip('"')
            continue
        continuation = re.match(r"^      (\S.*)$", line)
        if continuation and current is not None and field == "description":
            current["description"] = f"{current['description']} {continuation.group(1)}".strip()
    return result


def config_reference() -> list[dict[str, str]]:
    undocumented = set(_KNOWN_KEYS) - set(CONFIG_DOCS)
    if undocumented:
        raise SystemExit(f"document [tool.karma] key(s) {sorted(undocumented)} in CONFIG_DOCS")
    return [
        {"key": key, "type": kind, "default": default, "description": text}
        for key, (kind, default, text) in CONFIG_DOCS.items()
    ]


def render_data() -> str:
    data = {
        "version": __version__,
        "commands": cli_reference(),
        "action": action_reference(),
        "config": config_reference(),
        "benchmarks": json.loads(BENCHMARKS.read_text(encoding="utf-8")),
        # The risk model's prior weights, so the page's risk demo computes what Karma does.
        "risk": {"features": list(FEATURES), "prior": dict(PRIOR), "recency": RECENCY},
        # Dependency graphs (`karma graph --format json`) for the playground.
        "samples": json.loads(SAMPLES.read_text(encoding="utf-8")),
    }
    # `</` could end the <script> element early; JSON allows the escaped form.
    return json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")


def embedded(page: str) -> str:
    start = page.index(START) + len(START)
    return page[start : page.index(END, start)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if docs/index.html is stale")
    args = parser.parse_args()
    page = PAGE.read_text(encoding="utf-8")
    data = render_data()
    if args.check:
        if embedded(page) != data:
            print(
                "docs/index.html is out of date: run python scripts/build_site.py", file=sys.stderr
            )
            return 1
        return 0
    start = page.index(START) + len(START)
    page = page[:start] + data + page[page.index(END, start) :]
    PAGE.write_text(page, encoding="utf-8", newline="\n")
    print(f"updated {PAGE.relative_to(ROOT)} (karma {__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
