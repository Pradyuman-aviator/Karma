"""Project configuration, read from ``[tool.karma]`` in ``pyproject.toml``.

Example::

    [tool.karma]
    test-patterns = ["test_*.py", "*_test.py"]   # default: pytest's python_files
    source-roots = [".", "src"]                   # where top-level packages live
    exclude = ["docs/*", "migrations/*"]          # never analysed
    extend-run-all-on = ["Dockerfile"]            # also run everything when these change
    pytest-args = ["-p", "no:cacheprovider"]

    [tool.karma.mappings]                         # dependencies imports can't express
    "tests/fixtures/*.json" = ["tests/test_loader.py"]
    "app/templates/*" = ["app/render.py"]

Glob patterns without a ``/`` match file names anywhere; patterns with a ``/`` match
the whole path relative to the analysed directory, and ``*`` also matches ``/``.
"""

from __future__ import annotations

import configparser
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from karma.errors import ConfigError

log = logging.getLogger(__name__)

tomllib: ModuleType | None
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python < 3.11 only
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

DEFAULT_TEST_PATTERNS = ("test_*.py", "*_test.py")
DEFAULT_SOURCE_ROOTS = (".", "src")
# Changes to these can affect any test (dependencies, pytest configuration, ...),
# so they select the whole suite rather than risk skipping a failing test.
DEFAULT_RUN_ALL_ON = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "requirements*.txt",
    "constraints*.txt",
    "Pipfile.lock",
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
)


@dataclass(frozen=True)
class Config:
    test_patterns: tuple[str, ...] = DEFAULT_TEST_PATTERNS
    source_roots: tuple[str, ...] = DEFAULT_SOURCE_ROOTS
    run_all_on: tuple[str, ...] = DEFAULT_RUN_ALL_ON
    exclude: tuple[str, ...] = ()
    mappings: tuple[tuple[str, tuple[str, ...]], ...] = ()
    pytest_args: tuple[str, ...] = ()


_LIST_KEYS = {
    "test-patterns": "test_patterns",
    "source-roots": "source_roots",
    "run-all-on": "run_all_on",
    "exclude": "exclude",
    "pytest-args": "pytest_args",
}
_KNOWN_KEYS = {*_LIST_KEYS, "extend-run-all-on", "mappings"}


def load_config(root: Path) -> Config:
    """Load configuration for the project at ``root``; defaults if none is present."""
    pyproject = _read_pyproject(root / "pyproject.toml")
    tool = pyproject.get("tool", {})
    table = tool.get("karma", {}) if isinstance(tool, dict) else {}
    if not isinstance(table, dict):
        raise ConfigError("[tool.karma] must be a table")

    unknown = sorted(set(table) - _KNOWN_KEYS)
    if unknown:
        raise ConfigError(
            f"unknown [tool.karma] option(s): {', '.join(unknown)} "
            f"(valid options: {', '.join(sorted(_KNOWN_KEYS))})"
        )

    values: dict[str, Any] = {
        field: _string_list(table[key], key) for key, field in _LIST_KEYS.items() if key in table
    }
    if "test_patterns" not in values:
        pytest_patterns = _pytest_python_files(root, pyproject)
        if pytest_patterns:
            values["test_patterns"] = pytest_patterns
    if "extend-run-all-on" in table:
        base = values.get("run_all_on", DEFAULT_RUN_ALL_ON)
        values["run_all_on"] = (
            *base,
            *_string_list(table["extend-run-all-on"], "extend-run-all-on"),
        )
    if "mappings" in table:
        values["mappings"] = _mappings(table["mappings"])
    return Config(**values)


def _read_pyproject(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    if tomllib is None:  # pragma: no cover - Python < 3.11 without tomli
        log.warning("install 'tomli' to read [tool.karma] on Python < 3.11; using defaults")
        return {}
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None
    return data


def _string_list(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"[tool.karma] {key} must be a list of strings")
    return tuple(value)


def _mappings(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, dict):
        raise ConfigError("[tool.karma.mappings] must be a table of pattern = [targets]")
    return tuple(
        (pattern, _string_list(targets, f"mappings.{pattern!r}"))
        for pattern, targets in value.items()
    )


def _pytest_python_files(root: Path, pyproject: dict[str, Any]) -> tuple[str, ...]:
    """Honour pytest's own ``python_files`` setting when Karma's is not set."""
    ini_options = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    value = ini_options.get("python_files") if isinstance(ini_options, dict) else None
    if value is None:
        for filename, section in (
            ("pytest.ini", "pytest"),
            ("tox.ini", "pytest"),
            ("setup.cfg", "tool:pytest"),
        ):
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(root / filename, encoding="utf-8")
            except (configparser.Error, UnicodeDecodeError):
                continue
            if parser.has_option(section, "python_files"):
                value = parser.get(section, "python_files")
                break
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return ()
