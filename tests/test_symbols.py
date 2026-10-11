"""Function-level selection (karma.symbols): precise where it can be, whole files otherwise."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from karma import cli
from karma.config import Config
from karma.git import ChangeSet, get_changes
from karma.graph import build_graph
from karma.reporter import describe_selection, format_explanation
from karma.selector import Selection, select_tests
from karma.symbols import diff_units
from tests.helpers import GitRepo

ROOT = Path(__file__).resolve().parent.parent
FUNCTION = Config(granularity="function")

TAX = """\
RATE = 0.2


def rate(region):
    return RATE


def discount(amount):
    return amount * 0.9
"""
INVOICE = """\
from app.tax import discount, rate


def total(amount):
    return amount * (1 + rate("eu"))


def sale(amount):
    return discount(amount)
"""
TEST_INVOICE = """\
from app.invoice import sale, total


def test_total():
    assert total(100) == 120


def test_sale():
    assert sale(100) == 90


class TestRate:
    def test_fixture(self, eu_rate):
        assert eu_rate == 0.2
"""
CONFTEST = """\
import pytest

from app import tax


@pytest.fixture
def eu_rate():
    return tax.rate("eu")
"""
TEST_OTHER = """\
import app.tax


def test_discount():
    assert app.tax.discount(10) == 9


def test_unrelated():
    assert True
"""


def project(repo: GitRepo, files: dict[str, str] | None = None) -> GitRepo:
    base = {
        "app/__init__.py": "",
        "app/tax.py": TAX,
        "app/invoice.py": INVOICE,
        "tests/conftest.py": CONFTEST,
        "tests/test_invoice.py": TEST_INVOICE,
        "tests/test_other.py": TEST_OTHER,
    }
    base.update(files or {})
    for rel, content in base.items():
        repo.write(rel, textwrap.dedent(content))
    repo.commit("base")
    return repo


def edit(repo: GitRepo, rel: str, old: str, new: str) -> None:
    path = repo.path / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    repo.write(rel, text.replace(old, new, 1))


def select(repo: GitRepo, config: Config = FUNCTION, changes: ChangeSet | None = None) -> Selection:
    changes = changes or get_changes("main", cwd=repo.path)
    graph = build_graph(repo.path, source_roots=config.source_roots, deleted=changes.deleted)
    return select_tests(changes, graph, config, repo.path)


def targets(selection: Selection) -> list[str]:
    return sorted(selection.targets())


# --------------------------------------------------------------------------- what changed


class TestDiffUnits:
    def test_body_change_names_the_function(self) -> None:
        change = diff_units(TAX.encode(), TAX.replace("* 0.9", "* 0.8").encode(), "tax.py")
        assert not change.whole
        assert dict(change.names) == {"discount": 8}

    def test_comments_formatting_and_order_are_not_changes(self) -> None:
        after = "# header\nRATE = 0.2  # rate\n\n\ndef discount(amount):\n    return amount*0.9\n"
        after += "\n\ndef rate(region):\n\n    return RATE\n"
        change = diff_units(TAX.encode(), after.encode(), "tax.py")
        assert not change.whole
        assert not change.names

    def test_added_and_deleted_units_are_changes(self) -> None:
        after = TAX.replace("def discount", "def cut") + "\ndef extra():\n    return 1\n"
        change = diff_units(TAX.encode(), after.encode(), "tax.py")
        assert set(change.names) == {"discount", "cut", "extra"}

    def test_module_code_change_is_whole(self) -> None:
        change = diff_units(TAX.encode(), (TAX + "print('hi')\n").encode(), "tax.py")
        assert change.whole
        assert "module-level" in change.why

    def test_main_block_and_docstring_are_ignored(self) -> None:
        after = '"""Doc."""\n' + TAX + "\nif __name__ == '__main__':\n    print(rate('x'))\n"
        change = diff_units(TAX.encode(), after.encode(), "tax.py")
        assert not change.whole
        assert not change.names

    def test_type_checking_block_is_ignored(self) -> None:
        after = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import os\n" + TAX
        change = diff_units(TAX.encode(), after.encode(), "tax.py")
        assert not change.whole
        assert set(change.names) == {"TYPE_CHECKING"}  # the new import

    @pytest.mark.parametrize(
        ("before", "after", "why"),
        [
            (None, b"x = 1\n", "added"),
            (b"x = 1\n", None, "deleted"),
            (b"x = 1\n", b"x = 1\n", "no difference"),
            (b"x = 1\n", b"def (:\n", "not parseable"),
        ],
    )
    def test_whole_file_cases(self, before: bytes | None, after: bytes | None, why: str) -> None:
        change = diff_units(before, after, "m.py")
        assert change.whole
        assert why in change.why

    def test_redefined_name_is_module_code(self) -> None:
        before = "def f():\n    return 1\nf = wrap(f)\n"
        change = diff_units(before.encode(), before.replace("1", "2").encode(), "m.py")
        assert change.whole

    def test_not_python(self) -> None:
        assert diff_units(b"a", b"b", "data.json").whole


# --------------------------------------------------------------------------- selection


class TestFunctionSelection:
    def test_selects_only_the_tests_using_the_changed_function(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        selection = select(repo)
        assert selection.granularity == "function"
        assert targets(selection) == [
            "tests/test_invoice.py::TestRate",
            "tests/test_invoice.py::test_total",
        ]
        assert selection.narrowed_out == ("tests/test_other.py",)
        assert selection.item_reasons["tests/test_invoice.py::test_total"] == (
            "tests/test_invoice.py::test_total",
            "app/invoice.py::total",
            "app/tax.py::rate (changed, line 4)",
        )
        # Through the conftest fixture, by parameter name.
        assert (
            "tests/conftest.py::eu_rate"
            in selection.item_reasons["tests/test_invoice.py::TestRate"]
        )

    def test_attribute_chain_on_a_module(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        assert targets(select(repo)) == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]

    def test_constant_change_reaches_its_readers(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "RATE = 0.2", "RATE = 0.25")
        assert targets(select(repo)) == [
            "tests/test_invoice.py::TestRate",
            "tests/test_invoice.py::test_total",
        ]

    def test_comment_only_change_selects_nothing(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "def rate", "# the rate\ndef rate")
        selection = select(repo)
        assert selection.tests == ()
        assert set(selection.narrowed_out) == {"tests/test_invoice.py", "tests/test_other.py"}

    def test_module_level_change_falls_back_to_whole_files(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "RATE = 0.2", "RATE = 0.2\nprint('loaded')")
        selection = select(repo)
        assert targets(selection) == ["tests/test_invoice.py", "tests/test_other.py"]
        assert selection.items == {}

    def test_file_granularity_is_unchanged(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        selection = select(repo, Config())
        assert selection.granularity == "file"
        assert targets(selection) == ["tests/test_invoice.py", "tests/test_other.py"]

    def test_editing_one_test_selects_only_it(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "tests/test_other.py", "assert True", "assert 1")
        assert targets(select(repo)) == ["tests/test_other.py::test_unrelated"]

    def test_new_test_file_runs_whole(self, repo: GitRepo) -> None:
        project(repo)
        repo.write("tests/test_new.py", "def test_new():\n    pass\n")
        assert targets(select(repo)) == ["tests/test_new.py"]

    def test_deleted_function_selects_its_users(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "def discount(amount):\n    return amount * 0.9\n", "")
        assert targets(select(repo)) == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]

    def test_reexport_through_package_init(self, repo: GitRepo) -> None:
        project(
            repo,
            {
                "app/__init__.py": "from app.tax import rate\n",
                "tests/test_pkg.py": "from app import rate\n\ndef test_pkg():\n    rate('x')\n",
            },
        )
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        assert "tests/test_pkg.py::test_pkg" in targets(select(repo))

    def test_mock_patch_string_target(self, repo: GitRepo) -> None:
        test = (
            "from unittest import mock\n\n"
            "def test_patched():\n"
            "    with mock.patch('app.tax.discount'):\n"
            "        pass\n\n"
            "def test_plain():\n    pass\n"
        )
        project(repo, {"tests/test_patch.py": test})
        repo.write("tests/test_patch.py", test + "\nimport app.tax\n")
        repo.commit("import")
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        found = targets(select(repo))
        assert "tests/test_patch.py::test_patched" in found
        assert "tests/test_patch.py::test_plain" not in found

    def test_star_import(self, repo: GitRepo) -> None:
        test = (
            "from app.tax import *\n\ndef test_star():\n    rate('x')\n\ndef test_x():\n    pass\n"
        )
        project(repo, {"tests/test_star.py": test})
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        found = targets(select(repo))
        assert "tests/test_star.py::test_star" in found
        assert "tests/test_star.py::test_x" not in found

    def test_registry_decorator_marks_the_registry(self, repo: GitRepo) -> None:
        project(
            repo,
            {
                "app/web.py": (
                    "class App:\n"
                    "    def __init__(self):\n        self.routes = {}\n"
                    "    def route(self, path):\n"
                    "        def add(fn):\n"
                    "            self.routes[path] = fn\n"
                    "            return fn\n"
                    "        return add\n\n"
                    "app = App()\n\n"
                    "@app.route('/')\n"
                    "def home():\n    return 'home'\n"
                ),
                "tests/test_web.py": (
                    "from app.web import app\n\n"
                    "def test_route():\n    assert app.routes['/']() == 'home'\n\n"
                    "def test_other():\n    pass\n"
                ),
            },
        )
        edit(repo, "app/web.py", "return 'home'", "return 'HOME'")
        assert targets(select(repo)) == ["tests/test_web.py::test_route"]

    def test_module_level_registration_marks_the_registry(self, repo: GitRepo) -> None:
        project(
            repo,
            {
                "app/routes.py": (
                    "ROUTES = {}\n\ndef home():\n    return 'home'\n\nROUTES.update(home=home)\n"
                ),
                "tests/test_routes.py": (
                    "from app.routes import ROUTES\n\n"
                    "def test_routes():\n    assert ROUTES['home']() == 'home'\n\n"
                    "def test_other():\n    pass\n"
                ),
            },
        )
        edit(repo, "app/routes.py", "return 'home'", "return 'HOME'")
        assert targets(select(repo)) == ["tests/test_routes.py::test_routes"]

    def test_global_state_written_by_a_changed_function(self, repo: GitRepo) -> None:
        project(
            repo,
            {
                "app/cache.py": (
                    "CACHE = {}\n\n"
                    "def fill():\n    CACHE['k'] = 1\n\n"
                    "def read():\n    return CACHE.get('k')\n"
                ),
                "tests/test_cache.py": (
                    "from app.cache import read\n\n"
                    "def test_read():\n    read()\n\n"
                    "def test_other():\n    pass\n"
                ),
            },
        )
        edit(repo, "app/cache.py", "CACHE['k'] = 1", "CACHE['k'] = 2")
        assert targets(select(repo)) == ["tests/test_cache.py::test_read"]

    def test_dynamic_code_falls_back_to_whole_file(self, repo: GitRepo) -> None:
        project(repo, {"app/dyn.py": "def run():\n    exec('x = 1')\n\ndef other():\n    pass\n"})
        repo.write(
            "tests/test_dyn.py",
            "from app.dyn import other, run\n\ndef test_a():\n    other()\n\n"
            "def test_b():\n    run()\n",
        )
        repo.commit("dyn")
        edit(repo, "app/dyn.py", "x = 1", "x = 2")
        assert targets(select(repo)) == ["tests/test_dyn.py"]

    def test_metaclass_hook_falls_back_to_whole_file(self, repo: GitRepo) -> None:
        project(
            repo,
            {
                "app/base.py": (
                    "REGISTRY = []\n\n"
                    "class Base:\n"
                    "    def __init_subclass__(cls):\n        REGISTRY.append(cls)\n"
                ),
                "tests/test_base.py": (
                    "from app.base import Base\n\n"
                    "def test_a():\n    pass\n\ndef test_b():\n    Base\n"
                ),
            },
        )
        edit(repo, "app/base.py", "REGISTRY.append(cls)", "REGISTRY.insert(0, cls)")
        assert targets(select(repo)) == ["tests/test_base.py"]

    def test_autouse_fixture_runs_every_test_beneath(self, repo: GitRepo) -> None:
        conftest = CONFTEST + "\n\n@pytest.fixture(autouse=True)\ndef env():\n    return 1\n"
        project(repo, {"tests/conftest.py": conftest})
        edit(repo, "tests/conftest.py", "return 1", "return 2")
        assert targets(select(repo)) == ["tests/test_invoice.py", "tests/test_other.py"]

    def test_unrelated_conftest_fixture_change_selects_its_users(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "tests/conftest.py", 'tax.rate("eu")', 'tax.rate("us")')
        assert targets(select(repo)) == ["tests/test_invoice.py::TestRate"]

    def test_unittest_case_with_any_name_runs_whole_file(self, repo: GitRepo) -> None:
        test = (
            "import unittest\nfrom app.tax import rate\n\n"
            "class Checks(unittest.TestCase):\n"
            "    def test_rate(self):\n        rate('x')\n"
        )
        project(repo, {"tests/test_unit.py": test})
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        assert "tests/test_unit.py" in targets(select(repo))

    def test_test_class_with_init_is_not_an_item(self, repo: GitRepo) -> None:
        test = (
            "from app.tax import rate\n\n"
            "class TestHelper:\n    def __init__(self):\n        rate('x')\n\n"
            "def test_a():\n    pass\n"
        )
        project(repo, {"tests/test_helper.py": test})
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        assert "tests/test_helper.py" in select(repo).narrowed_out

    def test_dynamic_import_is_opaque(self, repo: GitRepo) -> None:
        test = (
            "import importlib\nmod = 'app.' + 'tax'\n\n"
            "def test_a():\n    importlib.import_module(mod).rate('x')\n\n"
            "def test_b():\n    pass\n"
        )
        project(repo, {"tests/test_dyn.py": test})
        repo.write("tests/test_dyn.py", test + "\nimport app.tax  # noqa\n")
        repo.commit("edge")
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        # `importlib` is dynamic: the test file cannot be narrowed safely.
        assert "tests/test_dyn.py" in targets(select(repo))

    def test_custom_python_functions(self, repo: GitRepo) -> None:
        test = (
            "from app.tax import rate\n\n"
            "def check_rate():\n    rate('x')\n\n"
            "def check_x():\n    pass\n"
        )
        project(repo, {"tests/test_check.py": test})
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        config = replace(FUNCTION, python_functions=("check_*",))
        assert "tests/test_check.py::check_rate" in targets(select(repo, config))
        assert "tests/test_check.py::check_x" not in targets(select(repo, config))

    def test_doctest_modules_stays_at_file_level(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        selection = select(repo, replace(FUNCTION, doctest_modules=True))
        assert selection.granularity == "file"

    def test_explicit_files_compare_with_head(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        changes = ChangeSet.from_paths(["app/tax.py"], repo.path)
        assert targets(select(repo, changes=changes)) == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]

    def test_explicit_unchanged_file_is_whole(self, repo: GitRepo) -> None:
        project(repo)
        changes = ChangeSet.from_paths(["app/tax.py"], repo.path)
        assert targets(select(repo, changes=changes)) == [
            "tests/test_invoice.py",
            "tests/test_other.py",
        ]

    def test_staged_changes_compare_the_index(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        repo.git("add", "app/tax.py")
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")  # not staged
        changes = get_changes(cwd=repo.path, staged=True)
        assert targets(select(repo, changes=changes)) == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]

    def test_committed_head_changes(self, repo: GitRepo) -> None:
        project(repo)
        repo.branch("feature")
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        repo.commit("cut")
        changes = get_changes("main", "HEAD", cwd=repo.path)
        assert targets(select(repo, changes=changes)) == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]

    def test_outside_git_stays_at_file_level(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "test_app.py").write_text(
            "from app import f\n\ndef test_f():\n    f()\n", encoding="utf-8"
        )
        changes = ChangeSet.from_paths(["app.py"], tmp_path)
        graph = build_graph(tmp_path)
        selection = select_tests(changes, graph, FUNCTION, tmp_path)
        assert selection.targets() == ["test_app.py"]


# --------------------------------------------------------------------------- output


class TestOutput:
    def test_explanation_and_summary(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        selection = select(repo)
        text = format_explanation(selection)
        assert "::test_total" in text
        assert "app/tax.py::rate (changed, line 4)" in text
        assert "Skipped at function level" in text
        assert "narrowed to 2 test functions in 1 file" in describe_selection(selection)

    def test_json(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        data = select(repo).to_dict()
        assert data["granularity"] == "function"
        assert data["targets"] == [
            "tests/test_invoice.py::TestRate",
            "tests/test_invoice.py::test_total",
        ]

    def test_cli_flag(self, repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
        project(repo)
        edit(repo, "app/tax.py", "* 0.9", "* 0.8")
        argv = ["-q", "select", "--repo", str(repo.path), "--granularity", "function"]
        assert cli.main([*argv, "--format", "json"]) == cli.EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data["targets"] == [
            "tests/test_invoice.py::test_sale",
            "tests/test_other.py::test_discount",
        ]
        assert cli.main(argv) == cli.EXIT_OK
        assert capsys.readouterr().out.split() == data["targets"]

    def test_explanations_are_the_same_on_every_run(self, repo: GitRepo) -> None:
        project(repo)
        edit(repo, "app/tax.py", "return RATE", "return RATE * 1")
        outputs = set()
        command = ["-m", "karma", "-q", "select", "--granularity", "function", "--format", "json"]
        for seed in ("1", "2", "3"):
            proc = subprocess.run(
                [sys.executable, *command],
                cwd=repo.path,
                env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=True,
            )
            outputs.add(proc.stdout)
        assert len(outputs) == 1
