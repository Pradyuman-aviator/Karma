from __future__ import annotations

import pytest

from karma.config import Config
from karma.git import ChangeSet
from karma.graph import DependencyGraph
from karma.selector import Selection, is_test_file, select_tests

# app/util.py <- app/core.py <- tests/test_core.py
#                            <- tests/api/test_api.py (via app/api.py)
GRAPH = DependencyGraph(
    {
        "app/__init__.py": set(),
        "app/util.py": set(),
        "app/core.py": {"app/util.py"},
        "app/api.py": {"app/core.py"},
        "app/render.py": set(),
        "tests/conftest.py": {"tests/fixtures.py"},
        "tests/fixtures.py": set(),
        "tests/test_core.py": {"app/core.py"},
        "tests/test_util.py": {"app/util.py"},
        "tests/api/conftest.py": set(),
        "tests/api/test_api.py": {"app/api.py"},
        "tests/test_render.py": {"app/render.py"},
    }
)
ALL_TESTS = (
    "tests/api/test_api.py",
    "tests/test_core.py",
    "tests/test_render.py",
    "tests/test_util.py",
)


def select(
    *modified: str, deleted: tuple[str, ...] = (), config: Config | None = None
) -> Selection:
    changes = ChangeSet(modified=modified, deleted=deleted)
    return select_tests(changes, GRAPH, config or Config())


class TestIsTestFile:
    @pytest.mark.parametrize(
        "path",
        ["tests/test_x.py", "test_x.py", "pkg/x_test.py", "tests/unit/deep/test_y.py"],
    )
    def test_matches_pytest_defaults(self, path: str) -> None:
        assert is_test_file(path, Config().test_patterns)

    @pytest.mark.parametrize(
        "path",
        [
            "tests/conftest.py",
            "tests/__init__.py",
            "tests/helpers.py",  # regression: everything under tests/ used to count
            "tests/test_data.json",
            "app/core.py",
        ],
    )
    def test_rejects_non_test_modules(self, path: str) -> None:
        assert not is_test_file(path, Config().test_patterns)

    def test_custom_patterns(self) -> None:
        assert is_test_file("checks/check_api.py", ["check_*.py"])
        assert is_test_file("qa/suite.py", ["qa/*.py"])


class TestSelectTests:
    def test_transitive_dependents_are_selected(self) -> None:
        selection = select("app/util.py")

        assert selection.tests == (
            "tests/api/test_api.py",
            "tests/test_core.py",
            "tests/test_util.py",
        )
        assert selection.total_tests == 4
        assert selection.skipped_fraction == pytest.approx(0.25)
        assert not selection.run_all

    def test_reasons_are_the_shortest_dependency_chain(self) -> None:
        selection = select("app/util.py")

        assert selection.reasons["tests/test_util.py"] == ("tests/test_util.py", "app/util.py")
        assert selection.reasons["tests/api/test_api.py"] == (
            "tests/api/test_api.py",
            "app/api.py",
            "app/core.py",
            "app/util.py",
        )

    def test_changed_test_file_selects_itself(self) -> None:
        selection = select("tests/test_render.py")
        assert selection.tests == ("tests/test_render.py",)
        assert selection.reasons == {"tests/test_render.py": ("tests/test_render.py",)}

    def test_deleted_test_files_are_never_selected(self) -> None:
        assert select(deleted=("tests/test_removed.py",)).tests == ()

    def test_deleted_module_selects_its_importers(self) -> None:
        graph = DependencyGraph({"tests/test_x.py": {"gone.py"}})
        selection = select_tests(ChangeSet(deleted=("gone.py",)), graph, Config())
        assert selection.tests == ("tests/test_x.py",)

    def test_non_python_changes_have_no_impact(self) -> None:
        selection = select("README.md", "docs/guide.md")
        assert selection.tests == ()
        assert selection.no_impact == ("README.md", "docs/guide.md")

    def test_no_impact_is_exact_when_several_changes_reach_one_test(self) -> None:
        # Both files reach tests/test_core.py, but only one can be its recorded parent.
        selection = select("app/util.py", "app/core.py", "README.md")
        assert selection.no_impact == ("README.md",)

    def test_conftest_change_selects_every_test_below_it(self) -> None:
        assert select("tests/api/conftest.py").tests == ("tests/api/test_api.py",)
        assert select("tests/conftest.py").tests == ALL_TESTS

    def test_module_used_by_a_conftest_selects_its_scope(self) -> None:
        selection = select("tests/fixtures.py")
        assert selection.tests == ALL_TESTS
        assert selection.reasons["tests/test_core.py"] == (
            "tests/test_core.py",
            "tests/conftest.py",
            "tests/fixtures.py",
        )

    def test_root_conftest_applies_to_everything(self) -> None:
        graph = DependencyGraph({"conftest.py": set(), "a/test_a.py": set(), "test_b.py": set()})
        selection = select_tests(ChangeSet(modified=("conftest.py",)), graph, Config())
        assert selection.tests == ("a/test_a.py", "test_b.py")

    @pytest.mark.parametrize(
        "trigger", ["pyproject.toml", "requirements-dev.txt", "sub/requirements.txt", "uv.lock"]
    )
    def test_run_all_triggers(self, trigger: str) -> None:
        selection = select("app/render.py", trigger)

        assert selection.run_all
        assert selection.tests == ALL_TESTS
        assert selection.run_all_reason is not None
        assert trigger in selection.run_all_reason
        assert selection.skipped_fraction == 0

    def test_run_all_triggers_can_be_disabled(self) -> None:
        selection = select("pyproject.toml", config=Config(run_all_on=()))
        assert not selection.run_all
        assert selection.tests == ()

    def test_mappings_connect_data_files_to_tests(self) -> None:
        config = Config(mappings=(("fixtures/*.json", ("tests/test_render.py",)),))
        selection = select("fixtures/users.json", config=config)

        assert selection.tests == ("tests/test_render.py",)
        assert selection.reasons["tests/test_render.py"] == (
            "tests/test_render.py",
            "fixtures/users.json",
        )
        assert selection.no_impact == ()

    def test_mappings_can_target_source_modules(self) -> None:
        config = Config(mappings=(("templates/*", ("app/core.py",)),))
        selection = select("templates/page.html", config=config)
        assert selection.tests == ("tests/api/test_api.py", "tests/test_core.py")

    def test_cycles_terminate(self) -> None:
        graph = DependencyGraph({"a.py": {"b.py"}, "b.py": {"a.py"}, "test_a.py": {"a.py"}})
        selection = select_tests(ChangeSet(modified=("b.py",)), graph, Config())
        assert selection.tests == ("test_a.py",)

    def test_empty_change_set(self) -> None:
        selection = select()
        assert selection.tests == ()
        assert selection.skipped_fraction == 1.0

    def test_no_tests_at_all(self) -> None:
        selection = select_tests(ChangeSet(modified=("a.py",)), DependencyGraph({}), Config())
        assert selection.skipped_fraction == 0.0


def test_selection_to_dict() -> None:
    data = select("app/core.py", "README.md").to_dict()

    assert data == {
        "tests": ["tests/api/test_api.py", "tests/test_core.py"],
        "selected": 2,
        "total": 4,
        "run_all": False,
        "run_all_reason": None,
        "changed": ["README.md", "app/core.py"],
        "no_impact": ["README.md"],
        "reasons": {
            "tests/api/test_api.py": ["tests/api/test_api.py", "app/api.py", "app/core.py"],
            "tests/test_core.py": ["tests/test_core.py", "app/core.py"],
        },
    }
