# Karma ⚡

> **Intelligent, Impact-Driven Test Selection for Accelerated CI/CD Pipelines**

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()
[![Development Phase](https://img.shields.io/badge/Phase-2--In%20Development-orange.svg)]()

---

## 📌 Overview

**Karma** is a smart test selection engine designed to drastically reduce CI/CD build times. Instead of executing an entire test suite on every code commit, Karma analyzes code diffs, builds dependency graphs, and intelligently runs **only the tests affected by your changes**.

By cutting out redundant test execution, Karma accelerates developer feedback loops, lowers cloud infrastructure costs, and optimizes software delivery.

---

## ✨ Key Features

- 🎯 **Git Diff Analysis**: Parses modified files and changed line ranges directly from git diffs.
- 🕸️ **AST & Dependency Mapping**: Constructs static dependency graphs connecting codebase modules to test suites.
- 🚀 **Selective Test Execution**: Filters out unaffected tests and executes only the required subset.
- 🧩 **Multi-Language Architecture**: Built with modular language parsers starting with Python support.
- ⚙️ **GitHub Actions Integration**: Native CI plugin with smart caching, PR summaries, and automatic pass/fail reporting.

---

## 🗺️ Project Roadmap & Phases

Karma is being developed across five distinct phases:

| Phase | Module / Target | Description | Status |
| :---: | :--- | :--- | :---: |
| **Phase 1** | **Git Diff & AST Analysis** | Analyze git diffs & build static code dependency graphs to select affected tests. | ✅ *Completed* |
| **Phase 2** | **GitHub Actions Integration** | Seamless CI runner plugins & GitHub Workflow integration. | 🚧 *In Progress* |
| **Phase 3** | **ML Prediction Layer** | AI/ML models to score test failure probabilities and prioritize high-risk tests. | 📅 *Planned* |
| **Phase 4** | **Flaky Test Registry** | Quarantine flaky tests, track failure reliability, and avoid false-positive pipeline breaks. | 📅 *Planned* |
| **Phase 5** | **LLM AI Copilot & Auto-Fix** | LLM-driven semantic test mapping, automated test generation, and failure diagnosis. | 📅 *Planned* |

---

## 🛠️ How It Works

```mermaid
graph TD
    A[Git Commit / Diff] --> B[Git Diff Parser]
    B --> C[Language AST Parser]
    C --> D[Dependency Graph Builder]
    D --> E[Test Selector Engine]
    E --> F[Run Affected Tests Only]
```

1. **Diff Extraction**: Karma extracts modified files and line-level changes from the active git branch.
2. **Dependency Graph Construction**: The AST parser inspects imported modules and call chains to build a graph of affected code paths.
3. **Smart Selection**: Karma maps the changed dependencies against test files and generates an optimal subset of tests to execute.

---

## 📂 Repository Structure

```
Karma/
├── core/
│   ├── git_diff.py          # Git diff parser & change detection
│   ├── dep_graph.py         # Code & test dependency graph builder
│   ├── test_selector.py     # Test selection & filtering engine
│   ├── reporter.py          # CI result formatter & GitHub summary writer  [Phase 2]
│   └── cache.py             # AST dependency graph caching layer           [Phase 2]
├── languages/
│   └── python_parser.py     # Python AST code analyzer
├── tests/
│   ├── test_git_diff_parser.py
│   └── test_selector.py
├── action.yml               # GitHub Action manifest                       [Phase 2]
├── Dockerfile               # Container image for CI runner                [Phase 2]
├── cli.py                   # Command-line interface entrypoint
├── requirements.txt         # Package dependencies
└── Readme.MD                # Project documentation
```

---

## 🚀 Quick Start

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Pradyuman-aviator/Karma.git
   cd Karma
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

### Usage

Run Karma to analyze your branch changes and execute impacted tests:

```bash
karma run
```

Or invoke via Python CLI:

```bash
python cli.py run
```

---

## 🤝 Contributing

Contributions are welcome! Feel free to open issues or submit pull requests to help advance Karma through its roadmap.

---

## 📜 License

Distributed under the [MIT License](LICENSE).

