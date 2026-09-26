"""Read the parts of pytest's own configuration that decide what a test run does.

Karma must agree with pytest about which files are tests, where imports resolve from,
and which plugins load, or it will select the wrong tests. This mirrors pytest's
configuration-file lookup: from the project directory upwards, the first of
``pytest.toml``, ``.pytest.toml``, ``pytest.ini``, ``.pytest.ini``, ``pyproject.toml``
(with ``[tool.pytest]`` or ``[tool.pytest.ini_options]``), ``tox.ini`` (``[pytest]``)
and ``setup.cfg`` (``[tool:pytest]``) wins.
"""

from __future__ import annotations

import configparser
import contextlib
import logging
import os
import shlex
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)

tomllib: ModuleType | None
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python < 3.11 only
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# pytest's default `norecursedirs`.
DEFAULT_NORECURSEDIRS = (
    "*.egg",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
)


# In pytest's order of precedence.
_CONFIG_FILES = (
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
)


@dataclass(frozen=True)
class PytestSettings:
    """pytest ini options, with paths made relative to the analysed directory."""

    inifile: Path | None = None
    python_files: tuple[str, ...] = ()
    testpaths: tuple[str, ...] = ()
    norecursedirs: tuple[str, ...] = DEFAULT_NORECURSEDIRS
    pythonpath: tuple[str, ...] = ()
    addopts: tuple[str, ...] = ()


@dataclass(frozen=True)
class PytestOptions:
    """What a pytest command line (``addopts`` included) means for test selection."""

    plugins: tuple[str, ...] = ()
    doctest_modules: bool = False
    doctest_globs: tuple[str, ...] = ()
    junitxml: str | None = None


def find_settings(root: Path) -> PytestSettings:
    """Locate and read the configuration file pytest would use when run in ``root``."""
    directory = root.resolve()
    while True:
        for name in _CONFIG_FILES:
            path = directory / name
            if path.is_file():
                values = _read(path)
                if values is not None:
                    return _settings(path, values, root)
        if directory.parent == directory:
            return PytestSettings()
        directory = directory.parent


def _read(path: Path) -> dict[str, Any] | None:
    """The pytest options in ``path``, or ``None`` if pytest would skip this file."""
    name = path.name
    try:
        if name.endswith(".toml"):
            if tomllib is None:  # pragma: no cover - Python < 3.11 without tomli
                return None
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if name != "pyproject.toml":
                table = data.get("pytest", {})  # pytest.toml counts even when empty
                return table if isinstance(table, dict) else {}
            pytest_table = data.get("tool", {}).get("pytest")
            if not isinstance(pytest_table, dict):
                return None
            legacy = pytest_table.get("ini_options")
            return legacy if isinstance(legacy, dict) else pytest_table
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError, configparser.Error) as exc:
        log.debug("ignoring unreadable pytest config %s: %s", path, exc)
        return None
    section = "tool:pytest" if name == "setup.cfg" else "pytest"
    if parser.has_section(section):
        return dict(parser.items(section))
    return {} if name in ("pytest.ini", ".pytest.ini") else None  # these count when empty


def _words(value: object, *, shell: bool = False) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            return tuple(shlex.split(value) if shell else value.split())
        except ValueError:
            return tuple(value.split())
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


def _settings(inifile: Path, values: dict[str, Any], root: Path) -> PytestSettings:
    base = inifile.parent

    def relative(entries: tuple[str, ...]) -> tuple[str, ...]:
        # ini paths are relative to the ini file; Karma's are relative to `root`.
        out = []
        for entry in entries:
            try:
                rel = Path(os.path.relpath(base / entry, root)).as_posix()
            except ValueError:  # on another drive (Windows): cannot be inside root
                continue
            if rel != ".." and not rel.startswith("../"):
                out.append("" if rel == "." else rel)
        return tuple(out)

    # pytest only uses testpaths when it runs from its rootdir (the ini file's
    # directory); run from a sub-directory, it collects everything below it.
    at_rootdir = base.resolve() == root.resolve()
    norecursedirs = values.get("norecursedirs")
    return PytestSettings(
        inifile=inifile,
        python_files=_words(values.get("python_files")),
        testpaths=relative(_words(values.get("testpaths"))) if at_rootdir else (),
        norecursedirs=_words(norecursedirs) if norecursedirs is not None else DEFAULT_NORECURSEDIRS,
        pythonpath=relative(_words(values.get("pythonpath"))),
        addopts=_words(values.get("addopts"), shell=True),
    )


_VALUE_FLAGS = ("-p", "--doctest-glob", "--junitxml", "--junit-xml")


def analyse_args(args: Sequence[str]) -> PytestOptions:
    """Extract plugins, doctest options and ``--junitxml`` from pytest arguments."""
    plugins: list[str] = []
    globs: list[str] = []
    doctest_modules = False
    junitxml: str | None = None
    rest = iter(args)
    for arg in rest:
        if arg == "--doctest-modules":
            doctest_modules = True
            continue
        flag, value = _flag_value(arg, rest)
        if flag == "-p" and value and not value.startswith("no:"):
            plugins.append(value)
        elif flag == "--doctest-glob" and value:
            globs.append(value)
        elif flag in ("--junitxml", "--junit-xml") and value:
            junitxml = value
    return PytestOptions(tuple(plugins), doctest_modules, tuple(globs), junitxml)


def _flag_value(arg: str, rest: Iterator[str]) -> tuple[str | None, str]:
    """``--flag value``, ``--flag=value`` or ``-pvalue`` -> ``(flag, value)``."""
    for flag in _VALUE_FLAGS:
        if arg == flag:
            return flag, next(rest, "")
        if arg.startswith(flag + "="):
            return flag, arg[len(flag) + 1 :]
    if arg.startswith("-p") and not arg.startswith("--") and len(arg) > 2:
        return "-p", arg[2:]
    return None, ""


def addopts_from_environment() -> tuple[str, ...]:
    return _words(os.environ.get("PYTEST_ADDOPTS", ""), shell=True)


def plugins_from_environment() -> tuple[str, ...]:
    """``PYTEST_PLUGINS``: comma-separated modules pytest loads as plugins."""
    value = os.environ.get("PYTEST_PLUGINS", "")
    return tuple(name.strip() for name in value.split(",") if name.strip())


def entry_point_plugins(pyproject: dict[str, Any], root: Path | None = None) -> tuple[str, ...]:
    """Modules the project registers as pytest plugins (``pytest11`` entry points).

    Read from PEP 621 ``[project.entry-points.pytest11]``, Poetry's
    ``[tool.poetry.plugins.pytest11]``, and setup.cfg's ``[options.entry_points]``.
    """
    specs: list[str] = []
    for keys in (
        ("project", "entry-points", "pytest11"),
        ("tool", "poetry", "plugins", "pytest11"),
    ):
        table: object = pyproject
        for key in keys:
            table = table.get(key) if isinstance(table, dict) else None
        if isinstance(table, dict):
            specs.extend(v for v in table.values() if isinstance(v, str))
    if root is not None and (root / "setup.cfg").is_file():
        parser = configparser.ConfigParser(interpolation=None)
        with contextlib.suppress(OSError, UnicodeDecodeError, configparser.Error):
            parser.read(root / "setup.cfg", encoding="utf-8")
            if parser.has_option("options.entry_points", "pytest11"):
                for line in parser.get("options.entry_points", "pytest11").splitlines():
                    if "=" in line:  # name = module:attr
                        specs.append(line.split("=", 1)[1])
    return tuple(dict.fromkeys(s.split(":")[0].strip() for s in specs if s.strip()))
