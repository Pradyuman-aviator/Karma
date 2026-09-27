# Changelog

All notable changes to Karma are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [3.3.0] - 2026-09-27

Phase 5 of the roadmap: failure diagnosis. With it, every phase is shipped.

### Added
- **Failure diagnosis** (`karma run --diagnose`, config `diagnose`, action input
  `diagnose`). For each failure Karma lines up the evidence it already has: the
  *suspects* (the changed lines the traceback passes through, from where the error was
  raised outwards; otherwise the changed files the test imports, nearest first), why
  the test ran, and its history. Failures with the same error and suspects are
  grouped. It is computed locally. In CI it is added to the job summary, and each
  suspect line gets an annotation on the pull request's diff.
- `karma diagnose`: explain the last run's failures without running anything again,
  or those of any JUnit report (`--report`, including pytest's default xunit2 format,
  which records no file). `--format text|markdown|json`, `--max N`.
- **Optional AI explanations** (`--ai anthropic|ollama|openai`, `--ai-model`, `--ai-url`;
  config `ai`, `ai-model`, `ai-url`; action inputs `ai`, `ai-model`). A language model
  turns the evidence into a summary, a cause, a fix, a kind (regression, test needs an
  update, flaky, environment) and a confidence.
  - Anthropic's API (default model `claude-sonnet-5`, with your own `ANTHROPIC_API_KEY`),
    Ollama on your machine, or any OpenAI-compatible server (LM Studio, llama.cpp,
    vLLM, OpenAI).
  - Off unless asked for: without it Karma makes no network requests. `--no-ai`
    overrides the configuration.
  - `karma diagnose --show-prompt` prints exactly what would be sent, and sends
    nothing. Credentials (API keys, tokens, private keys, quoted passwords) are
    redacted from the prompt.
  - At most 3 distinct failures per run, about 6,000 tokens of evidence each, asked in
    parallel, with one retry on rate limits. An AI error never changes the result of
    `karma run`.
- Website, live at https://pradyuman-aviator.github.io/Karma/ (`docs/index.html`,
  published to the `gh-pages` branch by the new Website workflow whenever it changes on
  `main`, after checking it matches the source):
  - the pitch, with an animated dependency-graph trace and an example run
  - the story of why Karma exists
  - a playground on starlette's real dependency graph: click files to change them
    and see the selection, the chain behind every test and each module's blast
    radius; paste your own `karma graph --format json` to explore your project
  - a risk-model demo driven by the model's own features and prior weights
  - the pull request summary and the flaky-test registry as your team sees them
  - benchmark charts from the real-project experiments, each with a table view,
    and the starlette and rich experiments as case studies
  - a CI savings calculator
  - an interactive explorer that builds any `karma` command
  - setup for GitHub Actions, GitLab CI, Jenkins, CircleCI and pre-commit
  - the Action and configuration reference
  - pitch mode: a slide walkthrough for presenting (arrow keys, full screen)
  - a social preview card (`docs/og.png`) and icon, so shared links show the pitch

  Commands, Action inputs/outputs, configuration keys and the risk model's weights
  are generated from the source by `scripts/build_site.py`, which also embeds the
  benchmark results and the playground graphs (`docs/samples.json`); a test fails if
  the page drifts.

### Changed
- `--no-history` also skips saving the run's failures for `karma diagnose`.
- CI lints the workflows and `action.yml` with actionlint.

### Fixed
- `--format` of `graph`, `history` and `flaky` had no help text.

## [3.2.0] - 2026-09-27

Phase 4 of the roadmap: the flaky-test registry.

### Added
- `--retries N` (config `retries`, action input `retries`): re-runs only the failed tests.
  A test that fails and then passes is confirmed flaky and reported as a warning rather
  than a failure (`--fail-on-flaky` for a strict policy). The action gains a
  `flaky-tests` output.
- Quarantine registry `karma-quarantine.toml` (config `quarantine-file`): quarantined
  tests still run and are reported, but their failures do not fail the build.
- `karma flaky`: list, `quarantine`, `release`, and `sync`. `sync` quarantines tests
  confirmed flaky in at least N runs and releases ones that have passed their last M
  runs.
- The history records flaky tests and the results of quarantined tests; flaky failures
  are kept out of the risk model's training.
- Flaky and quarantined results appear in the console summary, the GitHub job summary,
  and as `::warning` annotations.

## [3.1.0] - 2026-09-27

Phase 3 of the roadmap: the prediction layer. Karma now learns which tests are likely
to fail and can run them first. It still never skips a test because of a prediction.

### Added
- Test history: every `karma run` records each test file's outcome, duration and
  distance from the change in `.karma_cache/history.jsonl`. Opt out with
  `--no-history`.
- `--prioritize` (or `prioritize = true` in `[tool.karma]`): orders the selected tests
  by predicted risk of failure, so failures surface first (fail fast with `-- -x`).
  - The model is a logistic regression over seven explainable features. It trains
    online on the local history with no look-ahead, and is regularised towards priors
    so it orders sensibly from the first run.
  - Pure Python; no new dependencies.
- Risk explanations in `--explain`, `select --format json`, and a Risk column in the
  GitHub job summary, e.g. "risk 64%: failed before when app/pay.py changed".
- `karma history`: most-failing tests, flaky candidates and slowest tests
  (`--format json` for tooling). `--import` seeds the history from JUnit XML reports.
- GitHub Action: `cache` input (default on) persists `.karma_cache/` between runs with
  `actions/cache`; `prioritize` input.
- `scripts/prioritization_benchmark.py` measures how early an ordering finds real
  failures (APFD).

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
- A second review round, also fixed with tests:
  - text-file doctests (`test*.txt` by default) and imports inside doctest examples
  - path-style `norecursedirs`
  - `testpaths` applied outside pytest's rootdir
  - chains through `exclude`d files (`exclude` now only means "never a test")
  - Poetry / setup.cfg / `PYTEST_PLUGINS` / comma-separated `pytest_plugins` plugins
  - deleted plugins
  - submodules with `ignore = all`
  - pytest paths on another drive
  - stale user JUnit reports
  - full runs that collect nothing now fail like plain pytest
  - non-ASCII action inputs
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
