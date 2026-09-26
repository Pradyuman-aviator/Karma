"""Discover which files changed, using the git CLI.

Every path Karma handles is a POSIX-style path relative to the directory being
analysed (``--repo``), which may be a sub-directory of the git work tree.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from karma.errors import GitError

log = logging.getLogger(__name__)

# `git hash-object -t tree /dev/null`: lets --staged work before the first commit.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_HISTORY_HINT = (
    "In CI, fetch enough history for the base ref and the merge base to exist "
    "(for actions/checkout use `fetch-depth: 0`)."
)

# Directories never worth scanning when we have to walk the file system ourselves.
_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "venv",
    }
)


@dataclass(frozen=True)
class ChangeSet:
    """Files that differ between two trees.

    ``modified`` covers added, modified and type-changed files (they exist on the
    compared side); ``deleted`` covers files that no longer exist.
    """

    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    merge_base: str | None = None

    @property
    def all(self) -> tuple[str, ...]:
        return tuple(sorted({*self.modified, *self.deleted}))

    def __bool__(self) -> bool:
        return bool(self.modified or self.deleted)

    @classmethod
    def from_paths(cls, paths: Iterable[str], root: Path) -> ChangeSet:
        """Build a change set from explicit paths (e.g. ``--files`` or an editor hook)."""
        modified: set[str] = set()
        deleted: set[str] = set()
        for raw in paths:
            rel = to_relative(raw, root)
            if rel is None:
                log.warning("ignoring %s: it is outside %s", raw, root)
                continue
            (modified if (root / rel).exists() else deleted).add(rel)
        return cls(modified=tuple(sorted(modified)), deleted=tuple(sorted(deleted)))


def to_relative(path: str, root: Path) -> str | None:
    """Normalise ``path`` to a POSIX path relative to ``root``, or ``None`` if outside it."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        # relpath (unlike Path.relative_to) compares case-insensitively on Windows.
        rel = Path(os.path.relpath(candidate, root)).as_posix()
    except ValueError:  # different drive on Windows
        return None
    if rel in (".", "..") or rel.startswith("../"):
        return None
    return rel


def run_git(args: Sequence[str], cwd: Path) -> str:
    """Run ``git <args>`` in ``cwd`` and return stdout, raising :class:`GitError` on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except FileNotFoundError:
        raise GitError("git executable not found on PATH") from None
    except NotADirectoryError:
        raise GitError(f"{cwd} is not a directory") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit status {proc.returncode}"
        message = f"`git {' '.join(args)}` failed: {detail}"
        if "dubious ownership" in detail:
            message += (
                f"\nMark the checkout as safe with: git config --global --add safe.directory {cwd}"
            )
        raise GitError(message)
    return proc.stdout


def is_git_repo(path: Path) -> bool:
    try:
        return run_git(["rev-parse", "--is-inside-work-tree"], path).strip() == "true"
    except GitError:
        return False


def verify_ref(ref: str, cwd: Path) -> str:
    """Resolve ``ref`` to a commit SHA, with a helpful error when it does not exist."""
    if not ref or ref.startswith("-"):
        raise GitError(f"invalid git ref {ref!r}")
    try:
        return run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd).strip()
    except GitError:
        raise GitError(f"unknown git ref {ref!r}. {_HISTORY_HINT}") from None


def default_base(cwd: Path) -> str:
    """Best guess for the branch this work will be merged into.

    ``$KARMA_BASE``, then the pull request's target on GitHub Actions, then the
    remote's default branch, then the first of main/master that exists.
    """
    if os.environ.get("KARMA_BASE"):
        return os.environ["KARMA_BASE"]
    if os.environ.get("GITHUB_BASE_REF"):
        return f"origin/{os.environ['GITHUB_BASE_REF']}"
    try:
        remote_head = run_git(
            ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd
        )
        if remote_head.strip():
            return remote_head.strip()
    except GitError:
        pass
    for candidate in ("origin/main", "origin/master", "main", "master"):
        try:
            verify_ref(candidate, cwd)
        except GitError:
            continue
        return candidate
    return "main"


def merge_base(base: str, head: str, cwd: Path) -> str:
    """Return the best common ancestor of ``base`` and ``head``."""
    base_sha = verify_ref(base, cwd)
    head_sha = verify_ref(head, cwd)
    try:
        return run_git(["merge-base", base_sha, head_sha], cwd).strip()
    except GitError:
        raise GitError(f"{base!r} and {head!r} have no common ancestor. {_HISTORY_HINT}") from None


def get_changes(
    base: str = "main",
    head: str | None = None,
    *,
    cwd: Path,
    staged: bool = False,
) -> ChangeSet:
    """Return the files changed on this branch.

    * ``head`` given: the commits in ``base...head`` (the merge base to ``head``), which
      is exactly what a pull request introduces.
    * ``head`` omitted: the merge base to the *working tree*, including uncommitted and
      untracked files, which is what you want when running Karma locally.
    * ``staged``: only what is staged in the index, for pre-commit hooks.
    """
    diff = ["diff", "--name-status", "-z", "--no-renames", "--no-color", "--relative"]
    if staged:
        try:
            against = verify_ref("HEAD", cwd)
        except GitError:
            against = EMPTY_TREE
        modified, deleted = _parse_name_status(run_git([*diff, "--cached", against], cwd))
        return ChangeSet(tuple(sorted(modified)), tuple(sorted(deleted)))

    base_sha = merge_base(base, head or "HEAD", cwd)
    if head is None:
        modified, deleted = _parse_name_status(run_git([*diff, base_sha], cwd))
        untracked = run_git(["ls-files", "-z", "--others", "--exclude-standard"], cwd)
        modified |= {p for p in untracked.split("\0") if p}
    else:
        head_sha = verify_ref(head, cwd)
        modified, deleted = _parse_name_status(run_git([*diff, base_sha, head_sha], cwd))
    return ChangeSet(tuple(sorted(modified)), tuple(sorted(deleted)), merge_base=base_sha)


def _parse_name_status(output: str) -> tuple[set[str], set[str]]:
    """Parse ``git diff --name-status -z --no-renames`` output: ``STATUS\\0PATH\\0...``."""
    tokens = output.split("\0")
    modified: set[str] = set()
    deleted: set[str] = set()
    for status, path in zip(tokens[0::2], tokens[1::2]):
        if not path:
            continue
        (deleted if status.startswith("D") else modified).add(path)
    return modified, deleted


def list_files(root: Path, suffix: str = ".py") -> list[str]:
    """List files under ``root`` ending in ``suffix``, respecting ``.gitignore``.

    Uses git when available (tracked files plus untracked, non-ignored ones) and falls
    back to walking the file system for directories that are not git repositories.
    """
    try:
        output = run_git(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", f"*{suffix}"],
            root,
        )
    except GitError as exc:
        log.debug("git ls-files unavailable (%s); walking the file system", exc)
        return _walk(root, suffix)
    # --cached still lists files deleted from the working tree but not yet from the index.
    return sorted({p for p in output.split("\0") if p and (root / p).is_file()})


def _walk(root: Path, suffix: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".") and not d.endswith(".egg-info")
        )
        rel_dir = Path(dirpath).relative_to(root)
        found.extend((rel_dir / name).as_posix() for name in filenames if name.endswith(suffix))
    return sorted(found)
