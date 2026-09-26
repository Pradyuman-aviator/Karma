from __future__ import annotations

import json
from pathlib import Path

import pytest

from karma import cache as cache_module
from karma.cache import ImportCache
from karma.languages.python import ImportRef


def test_parses_on_miss_and_reuses_on_hit(tmp_path: Path) -> None:
    cache = ImportCache(tmp_path / "c.json")

    first = cache.imports_for("a.py", b"import os\n")
    second = cache.imports_for("a.py", b"import os\n")

    assert first == second == (ImportRef("os"),)
    assert (cache.hits, cache.misses) == (1, 1)


def test_changed_content_is_reparsed(tmp_path: Path) -> None:
    cache = ImportCache(tmp_path / "c.json")
    cache.imports_for("a.py", b"import os\n")

    assert cache.imports_for("a.py", b"import sys\n") == (ImportRef("sys"),)
    assert cache.misses == 2


def test_persists_between_instances(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    writer = ImportCache(path)
    writer.imports_for("a.py", b"from .b import c\n")
    writer.save()

    reader = ImportCache(path)

    assert reader.imports_for("a.py", b"from .b import c\n") == (ImportRef("b", ("c",), 1),)
    assert (reader.hits, reader.misses) == (1, 0)


def test_save_is_a_no_op_when_nothing_changed(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    ImportCache(path).save()
    assert not path.exists()


def test_prune_forgets_deleted_files(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cache = ImportCache(path)
    cache.imports_for("keep.py", b"")
    cache.imports_for("gone.py", b"")
    cache.prune(["keep.py"])
    cache.save()

    assert set(json.loads(path.read_text(encoding="utf-8"))["files"]) == {"keep.py"}


def test_in_memory_cache_never_touches_disk(tmp_path: Path) -> None:
    cache = ImportCache(None)
    cache.imports_for("a.py", b"import os\n")
    cache.save()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"version": "0/old/py2.7", "files": {}}',
        '{"version": "@STAMP@", "files": {"a.py": {"digest": "x"}}}',
        '{"version": "@STAMP@", "files": {"a.py": {"digest": "x", "imports": [[1, 2]]}}}',
        '{"version": "@STAMP@", "files": []}',
    ],
)
def test_corrupt_or_foreign_cache_is_ignored(tmp_path: Path, content: str) -> None:
    path = tmp_path / "c.json"
    path.write_text(content.replace("@STAMP@", cache_module._stamp()), encoding="utf-8")

    cache = ImportCache(path)

    assert cache.imports_for("a.py", b"import os\n") == (ImportRef("os"),)
    assert cache.misses == 1


def test_unreadable_cache_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.mkdir()  # reading a directory raises OSError
    assert ImportCache(path).imports_for("a.py", b"") == ()


def test_write_failure_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = ImportCache(tmp_path / "no" / "such-dir" / "c.json")  # parent's parent missing
    cache.imports_for("a.py", b"")

    cache.save()

    assert "could not write cache" in caplog.text
    assert not (tmp_path / "no").exists()


def test_cache_directory_ignores_itself(tmp_path: Path) -> None:
    cache = ImportCache(tmp_path / ".karma_cache" / "imports.json")
    cache.imports_for("a.py", b"")

    cache.save()

    directory = tmp_path / ".karma_cache"
    assert (directory / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"
    assert (directory / "CACHEDIR.TAG").read_text(encoding="utf-8").startswith("Signature: ")
