from __future__ import annotations

from pathlib import Path

import pytest

from karma.config import load_config
from karma.pytest_config import (
    DEFAULT_NORECURSEDIRS,
    PytestOptions,
    analyse_args,
    entry_point_plugins,
    find_settings,
)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestFindSettings:
    def test_no_configuration(self, tmp_path: Path) -> None:
        settings = find_settings(tmp_path)
        assert settings.inifile is None
        assert settings.norecursedirs == DEFAULT_NORECURSEDIRS

    def test_pytest_ini_wins_over_pyproject(self, tmp_path: Path) -> None:
        # Regression: Karma used to prefer pyproject.toml; pytest prefers pytest.ini.
        write(tmp_path / "pytest.ini", "[pytest]\npython_files = check_*.py\n")
        write(
            tmp_path / "pyproject.toml", '[tool.pytest.ini_options]\npython_files = "test_*.py"\n'
        )
        assert find_settings(tmp_path).python_files == ("check_*.py",)

    def test_an_empty_pytest_ini_still_wins(self, tmp_path: Path) -> None:
        ini = write(tmp_path / "pytest.ini", "")
        write(tmp_path / "pyproject.toml", '[tool.pytest.ini_options]\npython_files = "x_*.py"\n')
        settings = find_settings(tmp_path)
        assert settings.inifile == ini
        assert settings.python_files == ()

    @pytest.mark.parametrize("name", ["pytest.toml", ".pytest.toml"])
    def test_pytest_toml(self, tmp_path: Path, name: str) -> None:
        write(tmp_path / name, '[pytest]\npython_files = ["spec_*.py"]\naddopts = ["-x"]\n')
        write(tmp_path / "pytest.ini", "[pytest]\npython_files = ini_*.py\n")
        settings = find_settings(tmp_path)
        assert settings.python_files == ("spec_*.py",)
        assert settings.addopts == ("-x",)

    def test_native_tool_pytest_table(self, tmp_path: Path) -> None:
        write(tmp_path / "pyproject.toml", '[tool.pytest]\npython_files = ["check_*.py"]\n')
        assert find_settings(tmp_path).python_files == ("check_*.py",)

    def test_pyproject_without_pytest_section_is_skipped(self, tmp_path: Path) -> None:
        write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
        write(tmp_path / "setup.cfg", "[tool:pytest]\npython_files = cfg_*.py\n")
        assert find_settings(tmp_path).python_files == ("cfg_*.py",)

    def test_tox_ini_without_pytest_section_is_skipped(self, tmp_path: Path) -> None:
        write(tmp_path / "tox.ini", "[tox]\nenvlist = py\n")
        assert find_settings(tmp_path).inifile is None

    def test_searches_upwards_and_rebases_paths(self, tmp_path: Path) -> None:
        write(
            tmp_path / "pytest.ini",
            "[pytest]\ntestpaths = service/tests other\npythonpath = service/lib ..\n",
        )
        service = tmp_path / "service"
        service.mkdir()
        settings = find_settings(service)
        assert settings.testpaths == ("tests",)  # other/ is outside the analysed directory
        assert settings.pythonpath == ("lib",)

    def test_addopts_are_shell_split(self, tmp_path: Path) -> None:
        write(tmp_path / "pytest.ini", '[pytest]\naddopts = -p tests.plugin -k "not slow"\n')
        assert find_settings(tmp_path).addopts == ("-p", "tests.plugin", "-k", "not slow")

    def test_unreadable_config_is_skipped(self, tmp_path: Path) -> None:
        write(tmp_path / "pytest.toml", "not = [valid toml")
        write(tmp_path / "setup.cfg", "[tool:pytest]\npython_files = ok_*.py\n")
        assert find_settings(tmp_path).python_files == ("ok_*.py",)


class TestAnalyseArgs:
    def test_plugins(self) -> None:
        options = analyse_args(["-p", "a.plugin", "-pb.plugin", "-p=c", "-p", "no:cacheprovider"])
        assert options.plugins == ("a.plugin", "b.plugin", "c")

    def test_doctests_and_junit(self) -> None:
        options = analyse_args(
            [
                "--doctest-modules",
                "--doctest-glob",
                "*.rst",
                "--doctest-glob=*.txt",
                "--junitxml=r.xml",
            ]
        )
        assert options == PytestOptions(
            plugins=(),
            doctest_modules=True,
            doctest_globs=("*.rst", "*.txt"),
            junitxml="r.xml",
        )

    def test_junit_xml_spelling_and_separate_value(self) -> None:
        assert analyse_args(["--junit-xml", "out.xml"]).junitxml == "out.xml"

    def test_unrelated_arguments(self) -> None:
        assert analyse_args(["-x", "--pdb", "-q", "tests/"]) == PytestOptions()


def test_entry_point_plugins() -> None:
    pyproject = {"project": {"entry-points": {"pytest11": {"x": "pkg.plugin:hook", "y": "other"}}}}
    assert entry_point_plugins(pyproject) == ("pkg.plugin", "other")
    assert entry_point_plugins({"project": {"entry-points": []}}) == ()
    assert entry_point_plugins({}) == ()


class TestLoadConfigIntegration:
    def test_pytest_settings_flow_into_karma_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(
            tmp_path / "pyproject.toml",
            """
[project.entry-points.pytest11]
mine = "pkg.plugin"

[tool.pytest.ini_options]
python_files = ["check_*.py"]
testpaths = ["tests"]
pythonpath = ["lib"]
addopts = "-p tests.fixtures --doctest-modules"
""",
        )
        monkeypatch.setenv("PYTEST_ADDOPTS", "--junitxml=ci.xml")

        config = load_config(tmp_path)

        assert config.test_patterns == ("check_*.py",)
        assert config.testpaths == ("tests",)
        assert "lib" in config.source_roots
        assert config.plugins == ("pkg.plugin", "tests.fixtures")
        assert config.doctest_modules
        assert config.junitxml == "ci.xml"

    def test_command_line_pytest_args_are_added(self, tmp_path: Path) -> None:
        config = load_config(tmp_path).with_pytest_args(["-p", "x", "--junitxml", "o.xml"])
        assert config.plugins == ("x",)
        assert config.junitxml == "o.xml"
