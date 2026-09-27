"""Run the GitHub Action's entrypoint.sh the way a workflow would."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import GitRepo

ROOT = Path(__file__).resolve().parent.parent


def _find_bash() -> str | None:
    if sys.platform == "win32":
        # Prefer Git for Windows' bash; C:\Windows\System32\bash.exe is WSL.
        exec_path = subprocess.run(
            ["git", "--exec-path"], capture_output=True, text=True, check=False
        ).stdout.strip()
        candidate = Path(exec_path).parents[2] / "bin" / "bash.exe"
        return str(candidate) if candidate.exists() else None
    return shutil.which("bash")


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not available")


@pytest.fixture
def upstream(repo: GitRepo) -> GitRepo:
    """An 'origin' with a feature branch that only touches app/other.py."""
    repo.write("app/__init__.py")
    repo.write("app/core.py", "X = 1\n")
    repo.write("app/other.py", "Y = 1\n")
    repo.write("tests/test_core.py", "from app.core import X\n\ndef test_x():\n    assert X\n")
    repo.write("tests/test_other.py", "from app.other import Y\n\ndef test_y():\n    assert Y\n")
    repo.write(
        "pyproject.toml",
        '[tool.pytest.ini_options]\npythonpath = ["."]\naddopts = "-p no:cacheprovider"\n',
    )
    for i in range(3):  # some history, so a depth-1 clone really is missing commits
        repo.write("app/core.py", f"X = {i + 1}\n")
        repo.commit(f"main {i}")
    repo.branch("feature")
    repo.write("app/other.py", "Y = 2\n")
    repo.commit("feature")
    repo.checkout("main")
    return repo


def shallow_clone(upstream: GitRepo, tmp_path: Path, branch: str = "feature") -> Path:
    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--branch",
            branch,
            upstream.path.as_uri(),
            str(clone),
        ],
        check=True,
    )
    return clone


def run_action(
    workdir: Path, tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    output = tmp_path / "github_output"
    output.touch()
    full_env = {
        **os.environ,
        "KARMA_ACTION_PATH": str(ROOT),
        "INPUT_PYTHON": sys.executable,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        **env,
    }
    assert BASH is not None
    proc = subprocess.run(
        [BASH, str(ROOT / "entrypoint.sh")],
        cwd=workdir,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc, output.read_text(encoding="utf-8")


def output_value(outputs: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}<<(\S+)\n(.*?)\n\1$", outputs, re.M | re.S)
    assert match, f"{name} not in outputs:\n{outputs}"
    return match.group(2)


def test_pull_request_on_a_shallow_clone(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)

    proc, outputs = run_action(clone, tmp_path, GITHUB_BASE_REF="main")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert output_value(outputs, "test_files") == "tests/test_other.py"
    assert output_value(outputs, "tests-run") == "1"
    assert "Shallow clone" in proc.stdout
    assert "1 passed" in proc.stdout


def test_push_event_uses_the_previous_commit(upstream: GitRepo, tmp_path: Path) -> None:
    before = upstream.git("rev-parse", "main")
    upstream.write("app/core.py", "X = 99\n")
    upstream.commit("push to main")
    clone = shallow_clone(upstream, tmp_path, branch="main")

    proc, outputs = run_action(clone, tmp_path, KARMA_EVENT_BEFORE=before)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert output_value(outputs, "test_files") == "tests/test_core.py"


def test_inputs_are_passed_through(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)

    proc, outputs = run_action(
        clone,
        tmp_path,
        INPUT_BASE_BRANCH="origin/main",
        INPUT_COMMAND="select",
        INPUT_ARGS="--explain",
        KARMA_EVENT_BEFORE="0000000000000000000000000000000000000000",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "app/other.py  (changed)" in proc.stdout
    assert output_value(outputs, "tests-run") == "0"


def test_failures_fail_the_step(upstream: GitRepo, tmp_path: Path) -> None:
    upstream.checkout("feature")
    upstream.write("app/other.py", "Y = 0\n")
    upstream.commit("break it")
    clone = shallow_clone(upstream, tmp_path)

    proc, _ = run_action(clone, tmp_path, GITHUB_BASE_REF="main", INPUT_PYTEST_ARGS="-q -x")

    assert proc.returncode == 1
    assert "::error file=tests/test_other.py" in proc.stdout


def test_unknown_base_fails_by_default_or_runs_everything(
    upstream: GitRepo, tmp_path: Path
) -> None:
    clone = shallow_clone(upstream, tmp_path)

    failed, _ = run_action(clone, tmp_path, INPUT_BASE_BRANCH="origin/nope")
    assert failed.returncode == 2
    assert "unknown git ref 'origin/nope'" in failed.stderr

    ran_all, outputs = run_action(
        clone, tmp_path, INPUT_BASE_BRANCH="origin/nope", INPUT_ON_GIT_ERROR="run-all"
    )
    assert ran_all.returncode == 0, ran_all.stdout + ran_all.stderr
    assert output_value(outputs, "run-all") == "true"


def test_option_like_base_is_rejected_before_reaching_git(
    upstream: GitRepo, tmp_path: Path
) -> None:
    clone = shallow_clone(upstream, tmp_path)
    marker = tmp_path / "pwned"

    proc, _ = run_action(
        clone, tmp_path, INPUT_BASE_BRANCH=f"--upload-pack=touch {marker.as_posix()}"
    )

    assert proc.returncode == 2
    assert "invalid base-branch" in proc.stdout
    assert not marker.exists()


def test_bare_branch_name_as_base(upstream: GitRepo, tmp_path: Path) -> None:
    # Regression: `base-branch: main` was fetched into origin/main, then `main` was used.
    clone = shallow_clone(upstream, tmp_path)

    proc, outputs = run_action(clone, tmp_path, INPUT_BASE_BRANCH="main", INPUT_COMMAND="select")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert output_value(outputs, "test_files") == "tests/test_other.py"


def test_inputs_are_split_like_a_shell(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)

    proc, _ = run_action(
        clone,
        tmp_path,
        GITHUB_BASE_REF="main",
        INPUT_ARGS="--explain\n--jobs 2",
        INPUT_PYTEST_ARGS='-k "test_y or nothing"\n-v',
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "app/other.py  (changed)" in proc.stderr  # --explain on the first line survived
    assert "test_y PASSED" in proc.stdout  # -k kept its quoted value, -v applied


def test_unbalanced_quotes_fail_loudly(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)
    proc, _ = run_action(clone, tmp_path, GITHUB_BASE_REF="main", INPUT_PYTEST_ARGS='-k "oops')
    assert proc.returncode == 2
    assert "cannot parse the pytest-args input" in proc.stdout


def test_non_ascii_inputs_survive(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)

    proc, _ = run_action(
        clone, tmp_path, GITHUB_BASE_REF="main", INPUT_PYTEST_ARGS='-k "not 慢" -v'
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "test_y PASSED" in proc.stdout  # the pytest arguments were not dropped


def test_prioritize_input(upstream: GitRepo, tmp_path: Path) -> None:
    clone = shallow_clone(upstream, tmp_path)

    proc, _ = run_action(
        clone, tmp_path, GITHUB_BASE_REF="main", INPUT_COMMAND="select", INPUT_PRIORITIZE="true"
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--prioritize" in proc.stdout  # the echoed command
    assert "prioritised by risk using built-in priors" in proc.stderr
