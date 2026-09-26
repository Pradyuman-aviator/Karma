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
- Code moved from the top-level `core`/`languages` packages into `karma`. `python
  cli.py …` still works.
- The Docker image is now a standalone `karma` CLI image.
