from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from karma import git
from karma.errors import GitError
from karma.git import ChangeSet, get_changes, list_files, to_relative
from tests.helpers import GitRepo


class TestGetChanges:
    def test_commit_range_classifies_added_modified_deleted(self, repo: GitRepo) -> None:
        repo.write("keep.py", "a = 1\n")
        repo.write("gone.py", "b = 1\n")
        base = repo.commit("base")
        repo.write("keep.py", "a = 2\n")
        repo.write("new.py", "c = 1\n")
        repo.delete("gone.py")
        repo.commit("change")

        changes = get_changes(base, "HEAD", cwd=repo.path)

        assert changes.modified == ("keep.py", "new.py")
        assert changes.deleted == ("gone.py",)
        assert changes.all == ("gone.py", "keep.py", "new.py")
        assert changes.merge_base == base

    def test_uses_merge_base_so_base_branch_progress_is_ignored(self, repo: GitRepo) -> None:
        repo.write("app.py")
        repo.write("other.py")
        repo.commit("base")
        repo.branch("feature")
        repo.write("app.py", "x = 1\n")
        repo.commit("feature work")
        repo.checkout("main")
        repo.write("other.py", "y = 1\n")
        repo.commit("main moved on")
        repo.checkout("feature")

        changes = get_changes("main", "HEAD", cwd=repo.path)

        assert changes.all == ("app.py",)

    def test_working_tree_mode_includes_uncommitted_and_untracked(self, repo: GitRepo) -> None:
        repo.write(".gitignore", "ignored.py\n")
        repo.write("committed.py")
        repo.write("staged.py")
        repo.write("unstaged.py")
        repo.commit("base")
        repo.write("staged.py", "s = 1\n")
        repo.git("add", "staged.py")
        repo.write("unstaged.py", "u = 1\n")
        repo.write("untracked.py")
        repo.write("ignored.py")

        changes = get_changes("main", cwd=repo.path)

        assert changes.modified == ("staged.py", "unstaged.py", "untracked.py")
        assert changes.deleted == ()

    def test_staged_mode_only_reports_the_index(self, repo: GitRepo) -> None:
        repo.write("a.py")
        repo.write("b.py")
        repo.commit("base")
        repo.write("a.py", "a = 1\n")
        repo.git("add", "a.py")
        repo.write("b.py", "b = 1\n")

        assert get_changes(cwd=repo.path, staged=True).all == ("a.py",)

    def test_staged_mode_works_before_the_first_commit(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "first.py").write_text("", encoding="utf-8")
        subprocess.run(["git", "add", "first.py"], cwd=tmp_path, check=True)

        assert get_changes(cwd=tmp_path, staged=True).modified == ("first.py",)

    def test_paths_with_spaces_and_unicode_survive(self, repo: GitRepo) -> None:
        base = repo.commit("base")
        repo.write("my dir/tést file.py")
        repo.commit("add")

        assert get_changes(base, "HEAD", cwd=repo.path).modified == ("my dir/tést file.py",)

    def test_paths_are_relative_to_a_subdirectory_repo(self, repo: GitRepo) -> None:
        repo.write("service/app.py")
        repo.write("elsewhere.py")
        base = repo.commit("base")
        repo.write("service/app.py", "x = 1\n")
        repo.write("elsewhere.py", "y = 1\n")
        repo.commit("change")

        changes = get_changes(base, "HEAD", cwd=repo.path / "service")

        assert changes.all == ("app.py",)

    def test_unknown_base_ref_fails_loudly_with_a_hint(self, repo: GitRepo) -> None:
        with pytest.raises(GitError, match=r"unknown git ref 'origin/main'.*fetch-depth"):
            get_changes("origin/main", cwd=repo.path)

    @pytest.mark.parametrize("ref", ["", "--output=/tmp/pwned"])
    def test_option_like_refs_are_rejected(self, repo: GitRepo, ref: str) -> None:
        with pytest.raises(GitError, match="invalid git ref"):
            get_changes(ref, cwd=repo.path)

    def test_unrelated_histories_fail_loudly(self, repo: GitRepo) -> None:
        repo.git("checkout", "-q", "--orphan", "island")
        repo.commit("unrelated root")

        with pytest.raises(GitError, match="no common ancestor"):
            get_changes("main", "HEAD", cwd=repo.path)

    def test_outside_a_repository_fails_loudly(self, tmp_path: Path) -> None:
        with pytest.raises(GitError):
            get_changes("main", cwd=tmp_path)


class TestRunGit:
    def test_missing_git_binary(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(GitError, match="not found on PATH"):
            git.run_git(["status"], tmp_path)

    def test_dubious_ownership_error_explains_the_fix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def dubious(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                ["git"], 128, "", "fatal: detected dubious ownership in repository"
            )

        monkeypatch.setattr(subprocess, "run", dubious)
        with pytest.raises(GitError, match=r"safe\.directory"):
            git.run_git(["status"], tmp_path)

    def test_is_git_repo(self, repo: GitRepo, tmp_path_factory: pytest.TempPathFactory) -> None:
        assert git.is_git_repo(repo.path)
        assert not git.is_git_repo(tmp_path_factory.mktemp("plain"))


class TestListFiles:
    def test_lists_tracked_and_untracked_but_not_ignored_or_deleted(self, repo: GitRepo) -> None:
        repo.write(".gitignore", "build_out/\n")
        repo.write("pkg/mod.py")
        repo.write("pkg/data.txt")
        repo.write("removed.py")
        repo.commit("base")
        repo.write("fresh.py")
        repo.write("build_out/generated.py")
        repo.delete("removed.py")

        assert list_files(repo.path) == ["fresh.py", "pkg/mod.py"]

    def test_falls_back_to_walking_outside_git(self, tmp_path: Path) -> None:
        for rel in ("a.py", "pkg/b.py", "venv/lib.py", ".tox/x.py", "pkg/__pycache__/c.py"):
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")

        assert list_files(tmp_path) == ["a.py", "pkg/b.py"]


class TestChangeSet:
    def test_from_paths_normalises_and_classifies(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("", encoding="utf-8")

        changes = ChangeSet.from_paths(
            [str(tmp_path / "pkg" / "a.py"), "./pkg/gone.py", "../outside.py"], tmp_path
        )

        assert changes.modified == ("pkg/a.py",)
        assert changes.deleted == ("pkg/gone.py",)

    def test_truthiness(self) -> None:
        assert not ChangeSet()
        assert ChangeSet(deleted=("x.py",))

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("a/b.py", "a/b.py"),
            ("./a/../b.py", "b.py"),
            (".", None),
            ("..", None),
            ("../x.py", None),
        ],
    )
    def test_to_relative(self, tmp_path: Path, path: str, expected: str | None) -> None:
        assert to_relative(path, tmp_path) == expected

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
    def test_to_relative_accepts_windows_separators_and_case(self, tmp_path: Path) -> None:
        assert to_relative(r"a\b.py", tmp_path) == "a/b.py"
        assert to_relative(str(tmp_path).upper() + r"\c.py", tmp_path) == "c.py"
