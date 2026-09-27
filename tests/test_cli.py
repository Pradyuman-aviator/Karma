"""End-to-end tests: real git repositories, real pytest runs."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import karma
from karma import cli
from tests.helpers import FakeAPI, GitRepo, anthropic_reply

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(repo: GitRepo) -> GitRepo:
    """A small project on `main`, with a `feature` branch checked out."""
    repo.write("app/__init__.py")
    repo.write("app/util.py", "def double(x):\n    return x * 2\n")
    repo.write(
        "app/core.py", "from app.util import double\n\ndef quad(x):\n    return double(double(x))\n"
    )
    repo.write("app/other.py", "VALUE = 1\n")
    repo.write(
        "tests/test_core.py",
        "from app.core import quad\n\ndef test_quad():\n    assert quad(1) == 4\n",
    )
    repo.write(
        "tests/test_other.py",
        "from app.other import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
    )
    repo.write(
        "pyproject.toml",
        '[tool.karma]\npytest-args = ["-p", "no:cacheprovider"]\n'
        '[tool.pytest.ini_options]\npythonpath = ["."]\n',
    )
    repo.commit("project")
    repo.branch("feature")
    return repo


def karma_main(project: GitRepo, *args: str) -> int:
    """Run the CLI in-process against ``project`` (``--repo`` goes before any ``--``)."""
    argv = list(args)
    split = argv.index("--") if "--" in argv else len(argv)
    return cli.main([*argv[:split], "--repo", str(project.path), *argv[split:]])


class TestSelect:
    def test_prints_only_test_paths_on_stdout(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/util.py", "def double(x):\n    return x + x\n")

        assert karma_main(project, "select", "--base", "main") == 0

        out, err = capsys.readouterr()
        assert out == "tests/test_core.py\n"  # nothing else: safe for pytest $(karma select)
        assert "1 of 2 test files selected (50.0% skipped)" in err

    def test_formats(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("app/util.py", "# edit\n")
        project.write("app/other.py", "VALUE = 1  # edit\n")

        karma_main(project, "select", "--base", "main", "--format", "lines")
        assert capsys.readouterr().out == "tests/test_core.py\ntests/test_other.py\n"

        karma_main(project, "select", "--base", "main", "--format", "json")
        data = json.loads(capsys.readouterr().out)
        assert data["tests"] == ["tests/test_core.py", "tests/test_other.py"]
        assert data["reasons"]["tests/test_core.py"] == [
            "tests/test_core.py",
            "app/core.py",
            "app/util.py",
        ]

    def test_explain(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("app/util.py", "# edit\n")
        karma_main(project, "select", "--base", "main", "--explain")
        out = capsys.readouterr().out
        assert re.search(r"tests/test_core.py\n\s+(<-|←) app/core.py\n\s+(<-|←) app/util.py", out)

    def test_nothing_changed(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        assert karma_main(project, "select", "--base", "main") == 0
        out, err = capsys.readouterr()
        assert out == ""
        assert "0 of 2 test files selected" in err

    def test_legacy_invocation_without_subcommand(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/other.py", "# edit\n")
        assert karma_main(project, "--base", "main") == 0
        assert capsys.readouterr().out == "tests/test_other.py\n"

    def test_base_defaults_to_the_default_branch(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/other.py", "# edit\n")
        assert karma_main(project, "select") == 0
        _, err = capsys.readouterr()
        assert "against main" in err

    def test_base_from_environment(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KARMA_BASE", "does-not-exist")
        assert karma_main(project, "select") == cli.EXIT_KARMA_ERROR
        assert "unknown git ref 'does-not-exist'" in capsys.readouterr().err

    def test_commit_range_mode(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("app/other.py", "# committed\n")
        project.commit("edit other")
        project.write("app/util.py", "# uncommitted: ignored with --head\n")

        karma_main(project, "select", "--base", "main", "--head", "HEAD")

        assert capsys.readouterr().out == "tests/test_other.py\n"

    def test_staged_mode(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("app/other.py", "# staged\n")
        project.git("add", "app/other.py")
        project.write("app/util.py", "# not staged\n")

        karma_main(project, "select", "--staged")

        assert capsys.readouterr().out == "tests/test_other.py\n"

    def test_explicit_files(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        karma_main(project, "select", "--files", str(project.path / "app" / "util.py"))
        assert capsys.readouterr().out == "tests/test_core.py\n"

    def test_all(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        karma_main(project, "select", "--all")
        out, err = capsys.readouterr()
        assert out == "tests/test_core.py tests/test_other.py\n"
        assert "--all was requested" in err

    def test_ci_outputs(
        self, project: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        project.write("app/other.py", "# edit\n")

        karma_main(project, "select", "--base", "main", "--ci")

        text = output.read_text(encoding="utf-8")
        assert re.search(r"test_files<<(\S+)\ntests/test_other.py\n\1\n", text)
        assert re.search(r"total-count<<(\S+)\n2\n\1\n", text)


class TestRun:
    def test_runs_only_affected_tests(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/other.py", "VALUE = 1  # edit\n")

        assert karma_main(project, "run", "--base", "main") == 0

        out, err = capfd.readouterr()
        assert "test_other.py ." in out
        assert "test_core.py" not in out
        assert re.search(r"passed\s+1", err)

    def test_failing_test_fails_the_run(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/util.py", "def double(x):\n    return x * 3\n")

        assert karma_main(project, "run", "--base", "main") == 1

        assert "FAILED tests/test_core.py::test_quad" in capfd.readouterr().err

    def test_nothing_to_run(self, project: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        project.write("README.md", "docs only\n")
        assert karma_main(project, "run", "--base", "main") == 0
        assert "nothing to run" in capfd.readouterr().err

    def test_pytest_arguments_are_passed_through(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/util.py", "def double(x):\n    return x * 2  # edit\n")
        project.write("app/other.py", "VALUE = 1  # edit\n")

        assert karma_main(project, "run", "--base", "main", "--", "-k", "value", "-v") == 0

        out = capfd.readouterr().out
        assert "test_value PASSED" in out
        assert "test_quad" not in out

    def test_run_all_trigger_runs_the_whole_suite(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("requirements.txt", "requests\n")

        assert karma_main(project, "run", "--base", "main") == 0

        out, err = capfd.readouterr()
        assert "requirements.txt changed" in err
        assert "2 passed" in out

    def test_ci_mode_writes_summary_outputs_and_annotations(
        self,
        project: GitRepo,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        summary, output = tmp_path / "summary.md", tmp_path / "output"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        monkeypatch.setenv("GITHUB_WORKSPACE", str(project.path))
        project.write("app/util.py", "def double(x):\n    return 0\n")

        assert karma_main(project, "run", "--base", "main", "--ci") == 1

        out = capfd.readouterr().out
        assert (
            "::error file=tests/test_core.py,line=3,title=tests/test_core.py%3A%3Atest_quad" in out
        )
        text = summary.read_text(encoding="utf-8")
        assert "❌ 1 test failed" in text
        assert "**1 of 2** test files selected" in text
        assert re.search(r"tests-run<<(\S+)\n1\n\1\n", output.read_text(encoding="utf-8"))

    def test_ci_mode_with_nothing_to_run(
        self, project: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        assert karma_main(project, "run", "--base", "main", "--ci") == 0
        assert "no tests affected" in summary.read_text(encoding="utf-8")

    def test_explain_goes_to_stderr(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/other.py", "# edit\n")
        karma_main(project, "run", "--base", "main", "--explain")
        assert "app/other.py  (changed)" in capfd.readouterr().err


class TestErrors:
    def test_unknown_base_exits_2(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert karma_main(project, "run", "--base", "origin/nope") == cli.EXIT_KARMA_ERROR
        assert "karma: error: unknown git ref 'origin/nope'" in capsys.readouterr().err

    def test_on_git_error_run_all(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        code = karma_main(project, "run", "--base", "origin/nope", "--on-git-error", "run-all")
        assert code == 0
        out, err = capfd.readouterr()
        assert "changes could not be determined" in err
        assert "2 passed" in out

    def test_repo_must_be_a_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["select", "--repo", str(tmp_path / "missing")]) == cli.EXIT_KARMA_ERROR
        assert "is not a directory" in capsys.readouterr().err

    def test_invalid_config(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("pyproject.toml", "[tool.karma]\nbogus = 1\n")
        assert karma_main(project, "select") == cli.EXIT_KARMA_ERROR
        assert "unknown [tool.karma] option(s): bogus" in capsys.readouterr().err

    def test_pytest_args_only_for_run(self, project: GitRepo) -> None:
        with pytest.raises(SystemExit) as excinfo:
            karma_main(project, "select", "--", "-x")
        assert excinfo.value.code == 2

    def test_interrupt(self, project: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
        def interrupted(_args: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "cmd_select", interrupted)
        assert karma_main(project, "select") == cli.EXIT_INTERRUPTED

    def test_mutually_exclusive_sources(self, project: GitRepo) -> None:
        with pytest.raises(SystemExit):
            karma_main(project, "select", "--staged", "--all")


class TestGraph:
    @pytest.mark.parametrize(
        ("fmt", "needle"),
        [
            ("json", '"app/core.py": [\n    "app/__init__.py",\n    "app/util.py"\n  ]'),
            ("dot", '"app/core.py" -> "app/util.py";'),
            ("mermaid", "graph LR"),
        ],
    )
    def test_formats(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str], fmt: str, needle: str
    ) -> None:
        assert karma_main(project, "graph", "--format", fmt, "--no-cache") == 0
        assert needle in capsys.readouterr().out


class TestEntryPoints:
    def test_verbosity_flags(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        karma_main(project, "-q", "select")
        assert capsys.readouterr().err == ""
        karma_main(project, "select", "-v")
        assert "parsed" in capsys.readouterr().err

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--version"])
        assert capsys.readouterr().out.strip() == f"karma {karma.__version__}"

    @pytest.mark.parametrize(
        "command",
        [[sys.executable, "-m", "karma"], [sys.executable, str(ROOT / "cli.py")]],
        ids=["python -m karma", "cli.py shim"],
    )
    def test_process_entry_points(self, command: list[str]) -> None:
        proc = subprocess.run(
            [*command, "--version"], capture_output=True, text=True, cwd=ROOT, check=False
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == f"karma {karma.__version__}"


class TestReviewRegressions:
    def test_full_run_asks_pytest_even_if_karma_sees_no_tests(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        # Karma's patterns don't match, but pytest's own discovery does: never pass empty.
        project.write("pyproject.toml", '[tool.karma]\ntest-patterns = ["nothing_*.py"]\n')
        project.write("tests/test_boom.py", "def test_boom():\n    assert False\n")

        assert karma_main(project, "run", "--all", "--", "-p", "no:cacheprovider") == 1

        _, err = capfd.readouterr()
        assert "found no test files" in err

    def test_user_junitxml_is_kept(self, project: GitRepo) -> None:
        project.write("app/other.py", "VALUE = 1  # edit\n")

        assert karma_main(project, "run", "--base", "main", "--", "--junitxml=report.xml") == 0

        assert "test_value" in (project.path / "report.xml").read_text(encoding="utf-8")

    def test_doctest_modules_select_the_changed_module(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write(
            "app/other.py",
            'VALUE = 1\n\ndef shout():\n    """\n    >>> shout()\n    \'HI\'\n    """\n'
            '    return "hi"\n',
        )

        assert karma_main(project, "run", "--base", "main", "--", "--doctest-modules") == 1

        assert "app/other.py::app.other.shout" in capfd.readouterr().out

    def test_output_never_crashes_on_unencodable_paths(
        self, project: GitRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project.write("tests/test_日本.py", "from app.util import double\n")
        project.write("app/util.py", "def double(x):\n    return x + x\n")
        buffer = io.BytesIO()
        ascii_stdout = io.TextIOWrapper(buffer, encoding="ascii", errors="strict")
        monkeypatch.setattr(sys, "stdout", ascii_stdout)

        assert karma_main(project, "select", "--base", "main") == 0

        ascii_stdout.flush()
        assert rb"tests/test_\u65e5\u672c.py" in buffer.getvalue()


def test_a_full_run_that_collects_nothing_fails(
    project: GitRepo, capfd: pytest.CaptureFixture[str]
) -> None:
    # Everything deselected: plain pytest exits 5 and so must a full Karma run.
    assert karma_main(project, "run", "--all", "--", "-m", "no_such_marker") == 5
    project.write("app/other.py", "VALUE = 1  # edit\n")
    # ...while a targeted run that deselects everything is still fine.
    assert karma_main(project, "run", "--base", "main", "--", "-m", "no_such_marker") == 0


class TestHistoryRecording:
    def test_run_records_history(self, project: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        project.write("app/util.py", "def double(x):\n    return x * 3\n")  # breaks test_quad

        karma_main(project, "run", "--base", "main")

        path = project.path / ".karma_cache" / "history.jsonl"
        (line,) = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(line)
        assert record["changed"] == ["app/util.py"]
        assert record["tests"]["tests/test_core.py"][0] == "failed"
        assert record["tests"]["tests/test_core.py"][2] == 2  # test -> core -> util
        assert record["commit"] == project.git("rev-parse", "HEAD")

    def test_no_history_flag(self, project: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        project.write("app/other.py", "VALUE = 1  # edit\n")
        karma_main(project, "run", "--base", "main", "--no-history")
        assert not (project.path / ".karma_cache" / "history.jsonl").exists()


class TestPrioritize:
    @pytest.fixture
    def three(self, project: GitRepo) -> GitRepo:
        """Change app/util.py: test_core imports it via app/core.py, test_util directly."""
        project.checkout("main")  # an existing test, not part of this change
        project.write(
            "tests/test_util.py",
            "from app.util import double\n\ndef test_d():\n    assert double(2) == 4\n",
        )
        project.commit("add test_util")
        project.checkout("feature")
        project.git("merge", "-q", "main")
        project.write("app/util.py", "def double(x):\n    return x + x\n")
        return project

    def test_select_orders_by_risk_and_explains(
        self, three: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        karma_main(three, "select", "--base", "main", "--prioritize", "--format", "json")
        data = json.loads(capsys.readouterr().out)

        assert data["tests"] == ["tests/test_util.py", "tests/test_core.py"]  # nearest first
        assert data["risk"]["tests/test_util.py"]["reasons"] == ["imports app/util.py (changed)"]
        assert (
            data["risk"]["tests/test_util.py"]["probability"]
            > data["risk"]["tests/test_core.py"]["probability"]
        )

        karma_main(three, "select", "--base", "main", "--prioritize", "--explain")
        assert re.search(
            r"tests/test_util.py  \(risk \d+%: imports app/util.py", capsys.readouterr().out
        )

    def test_it_learns_from_history(
        self, three: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        # test_core fails in a recorded run...
        three.write("app/core.py", "from app.util import double\n\ndef quad(x):\n    return 0\n")
        assert karma_main(three, "run", "--base", "main") == 1
        three.write(
            "app/core.py",
            "from app.util import double\n\ndef quad(x):\n    return double(double(x))\n",
        )
        capfd.readouterr()

        # ...so next time it runs first, although test_util is nearer the change.
        assert karma_main(three, "run", "--base", "main", "--prioritize", "--", "-v") == 0

        out, err = capfd.readouterr()
        assert out.index("test_core.py::test_quad") < out.index("test_util.py::test_d")
        assert "prioritised by risk using 1 recorded run" in err

    def test_config_switch_and_full_runs(
        self, three: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        three.write(
            "pyproject.toml",
            '[tool.karma]\nprioritize = true\n[tool.pytest.ini_options]\npythonpath = ["."]\n',
        )
        karma_main(three, "select", "--files", "app/util.py")
        assert capsys.readouterr().out.split() == ["tests/test_util.py", "tests/test_core.py"]

        karma_main(three, "select", "--all")  # full runs keep pytest's own order
        assert "pytest decides the order" in capsys.readouterr().err

    def test_invalid_config(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project.write("pyproject.toml", '[tool.karma]\nprioritize = "yes"\n')
        assert karma_main(project, "select") == cli.EXIT_KARMA_ERROR
        assert "prioritize must be true or false" in capsys.readouterr().err


class TestHistoryCommand:
    def test_empty_history(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        assert karma_main(project, "history") == 0
        assert "No test history yet" in capsys.readouterr().out

    def test_summary_after_runs(self, project: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        project.write("app/util.py", "def double(x):\n    return 0\n")
        karma_main(project, "run", "--base", "main")
        project.write("app/util.py", "def double(x):\n    return x + x  # fixed\n")
        karma_main(project, "run", "--base", "main")
        capfd.readouterr()

        assert karma_main(project, "history") == 0
        out = capfd.readouterr().out
        assert "2 recorded runs" in out
        assert re.search(r"tests/test_core.py\s+1 of 2 runs \(50%\)", out)
        # (No timing assertions here: a trivial test can report 0.000s on a fast runner.)

        karma_main(project, "history", "--format", "json")
        data = json.loads(capfd.readouterr().out)
        assert data["runs"] == 2
        assert data["tests"]["tests/test_core.py"]["failures"] == 1

    def test_import_reports(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        report = project.path / "old.xml"
        report.write_text(
            '<testsuites><testsuite><testcase classname="tests.test_other" name="test_value" '
            'file="tests/test_other.py"><failure message="x"/></testcase></testsuite></testsuites>',
            encoding="utf-8",
        )

        assert karma_main(project, "history", "--import", str(report)) == 0

        out = capsys.readouterr().out
        assert "1 recorded run" in out
        assert re.search(r"tests/test_other.py\s+1 of 1 runs", out)

    def test_import_errors(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        assert karma_main(project, "history", "--import", "missing.xml") == cli.EXIT_KARMA_ERROR
        assert "cannot import missing.xml" in capsys.readouterr().err


def test_history_lists_flaky_and_slowest_tests(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    from karma.history import FAILED, PASSED, History, Run, TestRecord, default_history_path

    stored = History.load(default_history_path(project.path))
    for i, outcome in enumerate([PASSED, FAILED, PASSED, FAILED, PASSED, FAILED]):
        stored.append(
            Run(
                float(i),
                {
                    "tests/test_flaky.py": TestRecord(outcome, 0.5),
                    "tests/test_slow.py": TestRecord(PASSED, 7.25),
                },
            )
        )

    assert karma_main(project, "history") == 0

    out = capsys.readouterr().out
    assert re.search(r"Flaky candidates.*\n\s+tests/test_flaky.py\s+flipped 5 times in 6 runs", out)
    assert re.search(r"Slowest:\n\s+tests/test_slow.py\s+7.25s", out)


class TestFlaky:
    @pytest.fixture
    def flaky_project(
        self, project: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> GitRepo:
        """A test that fails the first time it runs and passes after (a real flake)."""
        monkeypatch.setenv("KARMA_TEST_MARKER", str(tmp_path / "ran-once"))
        project.write(
            "tests/test_flaky.py",
            "import os, pathlib\n\n"
            "def test_sometimes():\n"
            "    marker = pathlib.Path(os.environ['KARMA_TEST_MARKER'])\n"
            "    if not marker.exists():\n"
            "        marker.write_text('x')\n"
            "        raise AssertionError('first attempt fails')\n",
        )
        project.write("tests/test_bad.py", "def test_bad():\n    assert False, 'always'\n")
        return project

    def test_retries_detect_flaky_tests(
        self, flaky_project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            karma_main(flaky_project, "run", "--files", "tests/test_flaky.py", "--retries", "1")
            == 0
        )

        err = capfd.readouterr().err
        assert re.search(r"flaky\s+1", err)
        assert "FLAKY tests/test_flaky.py::test_sometimes - passed on retry 1" in err
        history = (flaky_project.path / ".karma_cache" / "history.jsonl").read_text(
            encoding="utf-8"
        )
        assert json.loads(history)["flaky"] == ["tests/test_flaky.py::test_sometimes"]

    def test_without_retries_a_flake_fails(
        self, flaky_project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        assert karma_main(flaky_project, "run", "--files", "tests/test_flaky.py") == 1

    def test_fail_on_flaky(self, flaky_project: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        code = karma_main(
            flaky_project,
            "run",
            "--files",
            "tests/test_flaky.py",
            "--retries",
            "1",
            "--fail-on-flaky",
        )
        assert code == 1
        assert "failing because of --fail-on-flaky" in capfd.readouterr().err

    def test_real_failures_are_retried_and_still_fail(
        self, flaky_project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            karma_main(flaky_project, "run", "--files", "tests/test_bad.py", "--retries", "2") == 1
        )
        assert "retrying 1 failed test(s), attempt 2 of 2" in capfd.readouterr().err

    def test_quarantine_workflow(
        self,
        flaky_project: GitRepo,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        assert (
            karma_main(
                flaky_project,
                "flaky",
                "quarantine",
                "tests/test_bad.py::test_bad",
                "--reason",
                "known",
            )
            == 0
        )
        registry = (flaky_project.path / "karma-quarantine.toml").read_text(encoding="utf-8")
        assert 'id = "tests/test_bad.py::test_bad"' in registry

        monkeypatch.setenv("GITHUB_WORKSPACE", str(flaky_project.path))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
        assert karma_main(flaky_project, "run", "--files", "tests/test_bad.py", "--ci") == 0

        out, err = capfd.readouterr()
        assert "QUARANTINED tests/test_bad.py::test_bad" in err
        assert "::warning file=tests/test_bad.py" in out
        summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
        assert "Quarantined failures | 1" in summary
        assert "Flaky and quarantined tests" in summary

        karma_main(flaky_project, "flaky")
        listing = capfd.readouterr().out
        assert "tests/test_bad.py::test_bad  (known)" in listing
        assert "last runs: F" in listing

        assert karma_main(flaky_project, "flaky", "release", "tests/test_bad.py::test_bad") == 0
        assert karma_main(flaky_project, "run", "--files", "tests/test_bad.py") == 1

    def test_sync_quarantines_confirmed_flakes(
        self, flaky_project: GitRepo, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        karma_main(flaky_project, "run", "--files", "tests/test_flaky.py", "--retries", "1")
        capfd.readouterr()

        karma_main(flaky_project, "flaky", "--format", "json")
        listing = json.loads(capfd.readouterr().out)
        assert listing["confirmed_flaky"] == [
            {"id": "tests/test_flaky.py::test_sometimes", "flaky_runs": 1}
        ]

        assert karma_main(flaky_project, "flaky", "sync", "--min-flakes", "1", "--dry-run") == 0
        assert "quarantine tests/test_flaky.py::test_sometimes" in capfd.readouterr().out
        assert not (flaky_project.path / "karma-quarantine.toml").exists()

        karma_main(flaky_project, "flaky", "sync", "--min-flakes", "1")
        assert "test_sometimes" in (flaky_project.path / "karma-quarantine.toml").read_text(
            encoding="utf-8"
        )
        karma_main(flaky_project, "flaky", "sync", "--min-flakes", "1")
        assert "quarantine is up to date" in capfd.readouterr().out

    def test_release_of_an_unknown_test(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert karma_main(project, "flaky", "release", "nope") == cli.EXIT_KARMA_ERROR
        assert "not quarantined: nope" in capsys.readouterr().err

    def test_invalid_retries_config(
        self, project: GitRepo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project.write("pyproject.toml", "[tool.karma]\nretries = -1\n")
        assert karma_main(project, "select") == cli.EXIT_KARMA_ERROR
        assert "retries must be a whole number" in capsys.readouterr().err


class TestDiagnose:
    """`karma run --diagnose` and `karma diagnose`, on a change that breaks a test."""

    SUSPECT = "app/util.py:2  changed; the test imports it indirectly (2 imports away)"

    @pytest.fixture
    def broken(self, project: GitRepo) -> GitRepo:
        project.write("app/util.py", "def double(x):\n    return x * 3\n")
        project.commit("triple it")
        return project

    def test_run_diagnose(self, broken: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        assert karma_main(broken, "run", "--diagnose") == 1
        err = capfd.readouterr().err
        assert "karma diagnosis" in err
        assert "tests/test_core.py::test_quad" in err
        assert "error      assert 9 == 4" in err
        assert self.SUSPECT in err
        assert "+    return x * 3" in err

    def test_only_when_asked(self, broken: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        assert karma_main(broken, "run") == 1
        assert "karma diagnosis" not in capfd.readouterr().err

    def test_config_switch(self, broken: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        broken.write(
            "pyproject.toml",
            '[tool.karma]\npytest-args = ["-p", "no:cacheprovider"]\ndiagnose = true\n'
            '[tool.pytest.ini_options]\npythonpath = ["."]\n',
        )
        assert karma_main(broken, "run") == 1
        assert self.SUSPECT in capfd.readouterr().err

    def test_the_error_raised_by_the_change(
        self, project: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        project.write("app/util.py", "def double(x):\n    raise RuntimeError('nope')\n")
        project.commit("raise")
        assert karma_main(project, "run", "--diagnose") == 1
        assert "app/util.py:2  changed, and where the error was raised" in capfd.readouterr().err

    def test_diagnose_the_last_run(
        self, broken: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        karma_main(broken, "run")
        capfd.readouterr()

        assert karma_main(broken, "diagnose") == 0
        out = capfd.readouterr().out  # the report goes to stdout
        assert "karma diagnosis" in out.splitlines()[0]
        assert "\ntests/test_core.py::test_quad\n" in out
        assert self.SUSPECT in out

        assert karma_main(broken, "diagnose", "--format", "json") == 0
        (data,) = json.loads(capfd.readouterr().out)
        assert data["test"] == "tests/test_core.py::test_quad"
        assert (data["suspects"][0]["file"], data["suspects"][0]["line"]) == ("app/util.py", 2)
        # The run being diagnosed is not part of its own history.
        assert data["history"] is None

        assert karma_main(broken, "diagnose", "--format", "markdown") == 0
        markdown = capfd.readouterr().out
        assert "Diagnosis" in markdown
        assert "<code>app/util.py:2</code>" in markdown

    def test_fixed_failures_are_forgotten(
        self, broken: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        karma_main(broken, "run")
        broken.write("app/util.py", "def double(x):\n    return x * 2\n")  # fixed, uncommitted
        assert karma_main(broken, "run") == 0  # nothing left to run
        capfd.readouterr()
        assert karma_main(broken, "diagnose", "--format", "json") == 0
        assert json.loads(capfd.readouterr().out) == []

    def test_no_history_saves_nothing(self, broken: GitRepo) -> None:
        karma_main(broken, "run", "--no-history")
        assert not (broken.path / ".karma_cache" / "last-failures.json").exists()

    def test_a_junit_report_from_ci(
        self, broken: GitRepo, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        # pytest's default JUnit format (xunit2) records no file: Karma finds it anyway.
        report = tmp_path / "report.xml"
        karma_main(broken, "run", "--no-history", "--", f"--junitxml={report}")
        capfd.readouterr()
        assert karma_main(broken, "diagnose", "--report", str(report)) == 0
        out = capfd.readouterr().out
        assert "\ntests/test_core.py::test_quad\n" in out
        assert self.SUSPECT in out

    def test_invalid_options(self, project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        assert karma_main(project, "diagnose", "--report", "missing.xml") == cli.EXIT_KARMA_ERROR
        assert "'missing.xml' is not a file" in capsys.readouterr().err
        assert karma_main(project, "diagnose", "--max", "0") == cli.EXIT_KARMA_ERROR
        assert "--max must be at least 1" in capsys.readouterr().err

    def test_show_prompt_sends_nothing(
        self, broken: GitRepo, capfd: pytest.CaptureFixture[str]
    ) -> None:
        karma_main(broken, "run")
        capfd.readouterr()
        # No API key and no server: --show-prompt needs neither.
        assert karma_main(broken, "diagnose", "--ai", "anthropic", "--show-prompt") == 0
        out = capfd.readouterr().out
        assert "[system]" in out
        assert "Failing test: tests/test_core.py::test_quad (failed)" in out
        assert "+    return x * 3" in out

    def test_a_missing_key(self, broken: GitRepo, capfd: pytest.CaptureFixture[str]) -> None:
        # karma run: a warning; the tests decide the exit code, and the evidence is shown.
        assert karma_main(broken, "run", "--ai", "anthropic") == 1
        err = capfd.readouterr().err
        assert "no AI explanation: --ai anthropic needs an API key" in err
        assert self.SUSPECT in err
        # karma diagnose --ai: asked for explicitly, so an error.
        assert karma_main(broken, "diagnose", "--ai", "anthropic") == cli.EXIT_KARMA_ERROR
        assert "set ANTHROPIC_API_KEY" in capfd.readouterr().err
        # --no-ai wins over the command line and the configuration.
        assert karma_main(broken, "diagnose", "--ai", "anthropic", "--no-ai") == 0

    def test_ai_explanation_in_ci(
        self,
        broken: GitRepo,
        api: FakeAPI,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        answer = {
            "summary": "double() now triples its argument.",
            "cause": "quad(1) is 9 instead of 4.",
            "fix": "Return x * 2 in app/util.py.",
            "kind": "regression",
            "confidence": "high",
            "file": "app/core.py",
            "line": 4,
        }
        api.answer(anthropic_reply(json.dumps(answer)))
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setenv("GITHUB_WORKSPACE", str(broken.path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        assert karma_main(broken, "run", "--ci", "--ai", "anthropic", "--ai-url", api.url) == 1

        captured = capfd.readouterr()
        assert "double() now triples its argument." in captured.err
        title = "title=Karma%3A may have broken tests/test_core.py%3A%3Atest_quad"
        assert f"::notice file=app/util.py,line=2,{title}::" in captured.out
        assert f"::notice file=app/core.py,line=4,{title}::double() now triples" in captured.out
        text = summary.read_text(encoding="utf-8")
        assert "### 🔎 Diagnosis" in text
        assert "🤖 claude-sonnet-5" in text
        assert "Fix: Return x * 2 in app/util.py." in text
        (request,) = api.requests
        assert request["headers"]["x-api-key"] == "sk-ant-test-key"
        assert "sk-ant-test-key" not in json.dumps(request["body"])
