from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.helpers import GitRepo


@pytest.fixture(autouse=True)
def _hermetic_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolate every test from the developer's global/system git configuration."""
    global_config = tmp_path_factory.mktemp("gitconfig") / "config"
    global_config.write_text(
        "[core]\n\tautocrlf = false\n[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Never discover a repository *above* the temp dir (e.g. a home directory under git).
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "Karma Tests")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "tests@karma.invalid")
    for var in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    """An initialised repository on branch ``main`` with one empty commit."""
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    git_repo = GitRepo(path)
    git_repo.commit("initial")
    return git_repo
