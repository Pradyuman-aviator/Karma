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
from tests.helpers import GitRepo

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
        assert "Slowest:" in out

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
