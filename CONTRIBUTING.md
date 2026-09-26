# 🤝 Contributing to Karma

First off — thanks for taking the time to contribute! Karma is an open-source project and every contribution, big or small, makes a difference.

---

## 📌 What is Karma?

Karma is a smart test selection engine that analyzes git diffs and AST dependency graphs to run **only the tests affected by your code changes** — saving CI time and developer feedback loops.

---

## 🚀 Getting Started

### 1. Fork & Clone
```bash
git clone https://github.com/<your-username>/Karma.git
cd Karma
```

### 2. Set Up Environment
```bash
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

pip install -e ".[dev]"
```

### 3. Run the Checks
```bash
python -m pytest          # tests, with a 90% coverage gate when run with --cov
ruff check .              # lint
ruff format .             # format
mypy                      # strict type checking
```

All four should pass before you start making changes. CI runs them on Python 3.9–3.13 across Linux, macOS and Windows.

Tests that touch git create throwaway repositories and never read your personal git config. Use the `repo` fixture and `tests/helpers.py` rather than mocking `subprocess`.

---

## 🧭 Project Layout

```
karma/
├── git.py               # what changed: merge-base diffs, working tree, --staged, file listing
├── languages/python.py  # parse imports (cacheable) and resolve them to repository files
├── cache.py             # per-file import cache keyed by content digest
├── graph.py             # dependency graph: build (parallel), query, export (json/dot/mermaid)
├── config.py            # [tool.karma] in pyproject.toml
├── selector.py          # which tests a change affects, and why
├── runner.py            # run pytest, parse JUnit into per-test results
├── reporter.py          # console output, GitHub summary/annotations/outputs
└── cli.py               # `karma run | select | graph`
action.yml, entrypoint.sh  # the composite GitHub Action
```

Two rules keep Karma trustworthy. Please preserve them:

1. **Never skip a test that could fail.** When unsure, select more (see the run-all triggers in `config.py`) and fail loudly rather than select nothing.
2. **stdout is for machines.** `karma select` output must stay pipeable; progress and diagnostics go to stderr through the `karma` logger.

---

## 🛠️ How to Contribute

### 🐛 Found a Bug?
- Check [existing issues](https://github.com/Pradyuman-aviator/Karma/issues) first
- If not reported, open a new issue with:
  - What happened
  - What you expected
  - Steps to reproduce

### ✨ Want to Add a Feature?
- Open an issue first describing your idea
- Wait for feedback before writing code (saves everyone time)
- Once approved, fork → branch → PR

### 📖 Want to Improve Docs?
Always welcome — no approval needed, just open a PR!

---

## 🌿 Branch Naming

```
feature/add-javascript-parser
fix/cache-corruption-on-windows
docs/update-contributing-guide
test/add-reporter-unit-tests
```

---

## 📋 Good First Issues

Look for issues tagged **`good first issue`** — these are small, well-defined tasks perfect for first-time contributors:

- Adding support for a new language parser (JS, Java, Go)
- Writing unit tests for existing modules
- Improving error messages
- Fixing typos or improving documentation

---

## ✅ PR Checklist

Before submitting a pull request, make sure:

- [ ] `python -m pytest`, `ruff check .`, `ruff format --check .` and `mypy` all pass
- [ ] New behaviour has tests, including the failure paths
- [ ] User-visible changes are noted in `CHANGELOG.md`
- [ ] PR description explains **what** and **why**

---

## 🗺️ Roadmap — Where You Can Help

| Phase | Area | Skills Needed |
| :---: | :--- | :--- |
| **Phase 3** | ML Prediction Layer | Python, scikit-learn, data analysis |
| **Phase 3** | Training data pipeline | Git history parsing, pandas |
| **Phase 4** | Flaky Test Registry | Python, SQLite/JSON storage |
| **Phase 5** | LLM Integration | OpenAI/Anthropic APIs, prompt engineering |
| **Any** | Language Parsers | AST knowledge (JS, Java, Go, Rust) |
| **Any** | Tests & Docs | pytest, markdown |

---

## 💬 Questions?

Open a [GitHub Discussion](https://github.com/Pradyuman-aviator/Karma/discussions) or drop a comment on any issue.

---

## 📜 License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
