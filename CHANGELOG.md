# Changelog

All notable changes to Karma are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [3.0.0] - 2026-09-27

A correctness and robustness overhaul. Several bugs caused Karma to skip tests that
should have run, or made CI pass without running any tests.

### Fixed
- **CI passed while running zero tests** whenever git failed (unknown base ref,
  shallow clone, "dubious ownership" inside the Docker action). Git errors now fail
  with exit code 2 and an actionable message.
- Changes were computed tip-to-tip (`git diff base head`), so commits added to the base
  branch after you branched were treated as your changes. Karma now diffs from the
  merge base.
- Uncommitted and untracked files were ignored when running locally.
- `from pkg import submodule` did not register a dependency on `pkg/submodule.py`.
- Deleted test files were passed to pytest, crashing the run.
- Every `.py` file under `tests/` (helpers, `conftest.py`, `__init__.py`) was treated as
  a test.
- A single failing test marked every selected file as failed; pytest's "no tests
  collected" exit code was treated as a failure.
- Log lines such as `[Karma] Cache saved…` were printed to stdout, breaking
  `pytest $(karma select)`.
- `karma --base X select` silently compared against `main`.
- The Docker-based action could not import a project's dependencies. It is now a
  composite action running in the workflow's own Python environment.
- Python 3.9 was not actually supported despite the documentation claiming 3.8+.
- Found by an adversarial review and fixed, each with a regression test. Each of these
  could skip a test that would fail:
  - a changed test-package `__init__.py`
  - helpers imported via a conftest directory on `sys.path`
  - `--doctest-modules`
  - pytest 9 `pytest.toml` / `[tool.pytest]` config and pytest's config precedence
  - Windows' case-insensitive test names
  - submodule bumps
  - pytest's `pythonpath`
  - `-p` plugins
  - lossy parsing of files `ast` cannot parse
  - `exclude` dropping edges
  - gitignored generated modules
  - `from pkg import *`
  - package-vs-module precedence
  - `--head` other than the checked-out commit
  - `testpaths` / `norecursedirs`
- `karma run --all` ran nothing when Karma recognised no test files; full runs now
  always go to pytest.
- Crashes on deeply nested generated code (including a hard interpreter crash on Python
  3.9/3.10), on an annotation-only `pytest_plugins`, and on non-ASCII output to a
  Windows pipe.
- A user's own `--junitxml` was silently replaced.
- Action: `base-branch: main` failed; `args`/`pytest-args` lost quoting and extra lines.

### Added
- `conftest.py` awareness, full-suite triggers for dependency and configuration files,
  and custom `mappings` for dependencies that imports cannot express.
- Dependency resolution for `src/` layouts, namespace packages, package `__init__`
  chains, pytest sibling imports, `importlib.import_module`, and `pytest_plugins`.
- `--explain` and `select --format json`: the reason each test was selected.
- Per-test results from pytest's JUnit report, GitHub job summaries, inline failure
  annotations, and new action outputs (`selected-count`, `total-count`, `run-all`).
- `karma graph --format json|dot|mermaid`.
- `--staged`, `--files`, `--all`, `--on-git-error run-all`, `--python`, `--jobs`,
  `--no-cache`, `-v`/`-q`, and pytest passthrough after `--`.
- Automatic base-branch detection, and automatic history fetching for shallow clones
  in the action.
- `[tool.karma]` configuration in `pyproject.toml`, validated strictly.
- An installable package with a `karma` command and `python -m karma`.

### Changed
- The import cache is incremental and per file, and cold parsing runs in parallel:
  on pandas (1,415 files) the graph builds in 1.2 s cold, 0.34 s warm (was 6.5 s,
  with any change forcing a full rebuild).
- The cache lives in `.karma_cache/`, which contains its own `.gitignore` (like
  `.pytest_cache/`), so it never shows up as an untracked or "changed" file. The old
  `.karma_cache.json` can be deleted.
- Code moved from the top-level `core`/`languages` packages into `karma`. `python
  cli.py …` still works.
- The Docker image is now a standalone `karma` CLI image.
