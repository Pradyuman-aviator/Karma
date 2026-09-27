from __future__ import annotations

from pathlib import Path

import pytest

from karma.config import DEFAULT_RUN_ALL_ON, Config, load_config
from karma.errors import ConfigError


def write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def test_defaults_without_any_config(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()


def test_defaults_with_an_unrelated_pyproject(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    assert load_config(tmp_path) == Config()


def test_full_configuration(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        """
[tool.karma]
test-patterns = ["check_*.py"]
source-roots = ["lib"]
exclude = "docs/*"
extend-run-all-on = ["Dockerfile"]
pytest-args = ["-x"]

[tool.karma.mappings]
"data/*.json" = ["tests/test_data.py"]
"templates/*" = "app/render.py"
""",
    )

    config = load_config(tmp_path)

    assert config == Config(
        test_patterns=("check_*.py",),
        source_roots=("lib",),
        exclude=("docs/*",),
        run_all_on=(*DEFAULT_RUN_ALL_ON, "Dockerfile"),
        pytest_args=("-x",),
        mappings=(
            ("data/*.json", ("tests/test_data.py",)),
            ("templates/*", ("app/render.py",)),
        ),
    )


def test_run_all_on_replaces_and_extend_appends(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.karma]\nrun-all-on = ["a.cfg"]\nextend-run-all-on = ["b.cfg"]\n',
    )
    assert load_config(tmp_path).run_all_on == ("a.cfg", "b.cfg")


def test_diagnosis_settings(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.karma]\ndiagnose = true\nai = "ollama"\nai-model = " qwen2.5-coder "\n'
        'ai-url = "http://localhost:11434/v1"\n',
    )
    config = load_config(tmp_path)
    assert (config.diagnose, config.ai, config.ai_model, config.ai_url) == (
        True,
        "ollama",
        "qwen2.5-coder",
        "http://localhost:11434/v1",
    )
    assert (Config().diagnose, Config().ai) == (False, None)  # off unless asked for


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('[tool.karma]\ndiagnose = "yes"\n', "diagnose must be true or false"),
        ('[tool.karma]\nai = "gpt"\n', "ai must be one of: anthropic, ollama, openai"),
        ("[tool.karma]\nai-model = 3\n", "ai-model must be a non-empty string"),
        ('[tool.karma]\nai-url = " "\n', "ai-url must be a non-empty string"),
        ("[tool.karma]\ntest-pattern = []\n", "unknown .* test-pattern"),
        ("[tool.karma]\nexclude = [1, 2]\n", "exclude must be a list of strings"),
        ("[tool.karma]\nmappings = []\n", r"mappings\] must be a table"),
        ('[tool.karma.mappings]\n"a" = [1]\n', "mappings.'a' must be a list"),
        ("[tool]\nkarma = 1\n", r"\[tool.karma\] must be a table"),
        ("[tool.karma\n", "invalid TOML"),
    ],
)
def test_invalid_configuration_is_reported(tmp_path: Path, content: str, message: str) -> None:
    write(tmp_path, "pyproject.toml", content)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_unreadable_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)


class TestPytestPythonFiles:
    def test_from_pyproject_as_string(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "pyproject.toml",
            '[tool.pytest.ini_options]\npython_files = "check_*.py *_spec.py"\n',
        )
        assert load_config(tmp_path).test_patterns == ("check_*.py", "*_spec.py")

    def test_from_pyproject_as_list(self, tmp_path: Path) -> None:
        write(tmp_path, "pyproject.toml", '[tool.pytest.ini_options]\npython_files = ["a_*.py"]\n')
        assert load_config(tmp_path).test_patterns == ("a_*.py",)

    @pytest.mark.parametrize(
        ("filename", "section"),
        [("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")],
    )
    def test_from_ini_files(self, tmp_path: Path, filename: str, section: str) -> None:
        write(tmp_path, filename, f"[{section}]\npython_files = spec_*.py\n")
        assert load_config(tmp_path).test_patterns == ("spec_*.py",)

    def test_karma_setting_wins(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "pyproject.toml",
            '[tool.karma]\ntest-patterns = ["k_*.py"]\n'
            '[tool.pytest.ini_options]\npython_files = "p_*.py"\n',
        )
        assert load_config(tmp_path).test_patterns == ("k_*.py",)

    def test_broken_ini_is_ignored(self, tmp_path: Path) -> None:
        write(tmp_path, "setup.cfg", "this is not [an ini file\n")
        assert load_config(tmp_path).test_patterns == Config().test_patterns
