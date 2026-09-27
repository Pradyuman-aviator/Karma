"""The website (docs/index.html) must document exactly what the tool does."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "index.html"


def embedded_data() -> dict[str, object]:
    page = PAGE.read_text(encoding="utf-8")
    match = re.search(r'<script id="karma-data" type="application/json">(.*?)</script>', page, re.S)
    assert match, "the page has no embedded data block"
    return json.loads(match.group(1))  # type: ignore[no-any-return]


def test_the_site_is_generated_from_the_current_source() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_site.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + "\nFix: python scripts/build_site.py"


def test_every_command_and_action_input_is_documented() -> None:
    data = embedded_data()
    commands = {c["name"]: c for c in data["commands"]}  # type: ignore[index, union-attr]
    assert list(commands) == ["run", "select", "diagnose", "graph", "history", "flaky"]
    assert [a["name"] for a in commands["flaky"]["actions"]] == [
        "flaky quarantine",
        "flaky release",
        "flaky sync",
    ]
    run_flags = {flag for option in commands["run"]["options"] for flag in option["flags"]}
    assert {"--prioritize", "--retries", "--fail-on-flaky", "--explain", "--staged"} <= run_flags
    diagnose_flags = {
        flag for option in commands["diagnose"]["options"] for flag in option["flags"]
    }
    assert {"--ai", "--ai-model", "--show-prompt", "--report"} <= diagnose_flags
    inputs = {i["name"] for i in data["action"]["inputs"]}  # type: ignore[index]
    assert {"base-branch", "prioritize", "retries", "cache", "diagnose", "ai"} <= inputs


def test_the_page_only_loads_allowed_resources() -> None:
    page = PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', page)
    stylesheets = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', page)
    assert scripts == []  # everything is inline: works offline and in strict CSPs
    assert all(url.startswith("https://fonts.googleapis.com/") for url in stylesheets)
