from __future__ import annotations

import textwrap
import warnings

import pytest

from karma.languages.python import ImportRef, ModuleResolver, parse_imports


def parse(source: str) -> tuple[ImportRef, ...]:
    return parse_imports(textwrap.dedent(source))


class TestParseImports:
    def test_import_statements(self) -> None:
        assert parse("import os, a.b as c\n") == (ImportRef("os"), ImportRef("a.b"))

    def test_from_imports_keep_names_and_level(self) -> None:
        refs = parse(
            """
            from a.b import c, d as e
            from . import sibling
            from ..pkg.mod import *
            """
        )
        assert refs == (
            ImportRef("a.b", ("c", "d")),
            ImportRef("", ("sibling",), 1),
            ImportRef("pkg.mod", ("*",), 2),
        )

    def test_imports_nested_in_functions_and_type_checking_blocks(self) -> None:
        refs = parse(
            """
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import only_for_types
            def lazy():
                import deferred
            """
        )
        assert ImportRef("only_for_types") in refs
        assert ImportRef("deferred") in refs

    def test_dynamic_imports_with_literal_arguments(self) -> None:
        refs = parse(
            """
            import importlib
            from importlib import import_module
            importlib.import_module("plugins.alpha")
            import_module("plugins.beta")
            __import__("plugins.gamma")
            importlib.import_module(".delta", package="plugins.sub")
            importlib.import_module("..eps", "plugins.sub")
            importlib.import_module(name_from_config)
            importlib.import_module(".no_package")
            importlib.import_module("....too_high", "a.b")
            __import__(".relative_not_supported")
            import_module("")
            """
        )
        dynamic = [r.module for r in refs if r.module.startswith("plugins")]
        assert dynamic == [
            "plugins.alpha",
            "plugins.beta",
            "plugins.gamma",
            "plugins.sub.delta",
            "plugins.eps",
        ]

    def test_relative_import_module_of_the_package_itself(self) -> None:
        assert ImportRef("pkg") in parse('import importlib\nimportlib.import_module(".", "pkg")\n')

    def test_pytest_plugins_declarations(self) -> None:
        refs = parse(
            """
            pytest_plugins = ["fixtures.db", ("fixtures.http",)]
            pytest_plugins: list = "fixtures.single"
            other = ["not.a.plugin"]
            """
        )
        modules = {r.module for r in refs}
        assert {"fixtures.db", "fixtures.http", "fixtures.single"} <= modules
        assert "not.a.plugin" not in modules

    def test_bytes_with_encoding_cookie(self) -> None:
        source = "# -*- coding: latin-1 -*-\nimport caf\xe9\n".encode("latin-1")
        assert parse_imports(source) == (ImportRef("café"),)

    def test_unparseable_file_is_scanned_token_by_token(self) -> None:
        refs = parse(
            """
            import os; import app  # two statements on one line
            from pkg import (
                a,  # a comment here used to swallow everything after it
                b,
            )
            if TYPE_CHECKING: import typed_only
            from.mod import y
            from ..up import z as zed
            import deep.pkg.mod as m, other \\
                , third
            pytest_plugins = ["plugin.one", "plugin.two"]
            importlib.import_module("dyn.mod")
            try:
                pass
            except ValueError, TypeError:  # Python 3.14 syntax: not parseable before
                pass
            """
        )
        assert refs == (
            ImportRef("os"),
            ImportRef("app"),
            ImportRef("pkg", ("a", "b")),
            ImportRef("typed_only"),
            ImportRef("mod", ("y",), 1),
            ImportRef("up", ("z",), 2),
            ImportRef("deep.pkg.mod"),
            ImportRef("other"),
            ImportRef("third"),
            ImportRef("plugin.one"),
            ImportRef("plugin.two"),
            ImportRef("dyn.mod"),
        )

    def test_warnings_as_errors_do_not_force_the_fallback(self) -> None:
        # "\d" is a SyntaxWarning; under -W error it used to become a SyntaxError.
        source = 'import re\nPATTERN = "\\d+"\nfrom pkg import (\n    a,  # c\n    b,\n)\n'
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            refs = parse_imports(source)
        assert refs == (ImportRef("re"), ImportRef("pkg", ("a", "b")))

    def test_pathologically_deep_files_do_not_crash(self) -> None:
        source = "import app\nX = " + "+".join(["1"] * 100_000) + "\n"
        assert parse_imports(source) == (ImportRef("app"),)

    def test_annotation_only_pytest_plugins(self) -> None:
        refs = parse("pytest_plugins: list[str]\npytest_plugins = ['fx']\n")
        assert refs == (ImportRef("fx"),)

    def test_untokenizable_file_falls_back_to_a_line_scan(self) -> None:
        refs = parse(
            """
            import alpha, beta as b
            from .pkg import (one,
                              two as deux)  # trailing comment
            from gamma import delta \\
                , epsilon
            this is not valid python (
            """
        )
        assert refs == (
            ImportRef("alpha"),
            ImportRef("beta"),
            ImportRef("pkg", ("one", "two"), 1),
            ImportRef("gamma", ("delta", "epsilon")),
        )

    def test_null_bytes_do_not_crash(self) -> None:
        assert parse_imports(b"import a\x00\n") == (ImportRef("a"),)

    def test_duplicates_are_removed_but_order_is_stable(self) -> None:
        assert parse("import b\nimport a\nimport b\n") == (ImportRef("b"), ImportRef("a"))


class TestImportRefJson:
    def test_round_trip(self) -> None:
        ref = ImportRef("a.b", ("c",), 2)
        assert ImportRef.from_json(ref.to_json()) == ref

    @pytest.mark.parametrize("data", [[1, [], 0], ["a", "b", 0], ["a", [], "0"]])
    def test_rejects_malformed_records(self, data: list[object]) -> None:
        with pytest.raises(ValueError, match="malformed"):
            ImportRef.from_json(data)


def resolve(files: set[str], importer: str, source: str, **kwargs: tuple[str, ...]) -> set[str]:
    resolver = ModuleResolver(files | {importer}, **kwargs)
    return resolver.resolve(importer, parse(source))


class TestModuleResolver:
    def test_ignores_third_party_and_stdlib_imports(self) -> None:
        assert resolve({"app.py"}, "main.py", "import os\nimport pytest\n") == set()

    def test_module_and_package_files(self) -> None:
        files = {"core/__init__.py", "core/git.py", "util.py"}
        assert resolve(files, "main.py", "import util\nimport core.git\n") == {
            "util.py",
            "core/__init__.py",
            "core/git.py",
        }

    def test_from_package_import_submodule(self) -> None:
        # Regression: this used to resolve only to core/__init__.py.
        files = {"core/__init__.py", "core/git.py", "core/cache.py"}
        assert resolve(files, "main.py", "from core import git, not_a_module\n") == {
            "core/__init__.py",
            "core/git.py",
        }

    def test_from_module_import_name(self) -> None:
        files = {"core/__init__.py", "core/git.py"}
        assert resolve(files, "main.py", "from core.git import get_changes\n") == {
            "core/__init__.py",
            "core/git.py",
        }

    def test_namespace_packages_without_init(self) -> None:
        assert resolve({"ns/sub/mod.py"}, "main.py", "import ns.sub.mod\n") == {"ns/sub/mod.py"}

    def test_src_layout(self) -> None:
        files = {"src/pkg/__init__.py", "src/pkg/core.py"}
        assert resolve(files, "tests/test_core.py", "from pkg.core import thing\n") == {
            "src/pkg/__init__.py",
            "src/pkg/core.py",
        }

    def test_custom_source_roots(self) -> None:
        files = {"lib/python/tool.py"}
        importer = "tests/test_tool.py"
        assert resolve(files, importer, "import tool\n") == set()
        roots = ("", "./lib/python/")
        assert resolve(files, importer, "import tool\n", source_roots=roots) == {
            "lib/python/tool.py"
        }

    def test_sibling_import_from_a_non_package_test_directory(self) -> None:
        # pytest's default "prepend" import mode puts tests/ on sys.path.
        files = {"tests/helpers.py"}
        assert resolve(files, "tests/test_x.py", "import helpers\n") == {"tests/helpers.py"}

    def test_import_root_is_the_first_non_package_directory(self) -> None:
        files = {"tests/__init__.py", "tests/unit/__init__.py", "tests/unit/helpers.py"}
        importer = "tests/unit/test_x.py"
        assert resolve(files, importer, "import helpers\n") == set()
        assert resolve(files, importer, "from tests.unit import helpers\n") == {
            "tests/__init__.py",
            "tests/unit/__init__.py",
            "tests/unit/helpers.py",
        }

    def test_relative_imports(self) -> None:
        files = {
            "pkg/__init__.py",
            "pkg/a.py",
            "pkg/sub/__init__.py",
            "pkg/sub/b.py",
            "pkg/sub/deep/__init__.py",
            "pkg/sub/deep/c.py",
        }
        importer = "pkg/sub/mod.py"
        assert resolve(files, importer, "from . import b\n") == {
            "pkg/sub/__init__.py",
            "pkg/sub/b.py",
        }
        assert resolve(files, importer, "from .b import x\n") == {"pkg/sub/b.py"}
        assert resolve(files, importer, "from .. import a\n") == {"pkg/__init__.py", "pkg/a.py"}
        assert resolve(files, importer, "from ..a import x\n") == {"pkg/a.py"}
        assert resolve(files, importer, "from .deep.c import x\n") == {
            "pkg/sub/deep/__init__.py",
            "pkg/sub/deep/c.py",
        }
        assert resolve(files, importer, "from .deep import *\n") == {
            "pkg/sub/deep/__init__.py",
            "pkg/sub/deep/c.py",  # may be in deep.__all__
        }

    def test_relative_import_inside_package_init(self) -> None:
        files = {"pkg/__init__.py", "pkg/a.py"}
        assert resolve(files, "pkg/__init__.py", "from . import a\nfrom .a import x\n") == {
            "pkg/a.py"
        }

    def test_relative_import_beyond_the_top_level_is_ignored(self) -> None:
        assert resolve({"a.py"}, "pkg/mod.py", "from ... import a\n") == set()
        assert resolve({"__init__.py"}, "mod.py", "from . import x\n") == set()

    def test_a_file_never_depends_on_itself(self) -> None:
        assert resolve(set(), "loop.py", "import loop\n") == set()

    def test_a_package_wins_over_a_module_of_the_same_name(self) -> None:
        files = {"pkg/__init__.py", "pkg/mod.py", "pkg/mod/__init__.py"}
        assert "pkg/mod/__init__.py" in resolve(files, "main.py", "from pkg.mod import X\n")
        assert "pkg/mod.py" not in resolve(files, "main.py", "from pkg.mod import X\n")

    def test_star_imports_include_submodules(self) -> None:
        files = {"pkg/__init__.py", "pkg/sub.py", "pkg/inner/__init__.py", "other.py"}
        assert resolve(files, "main.py", "from pkg import *\n") == {
            "pkg/__init__.py",
            "pkg/sub.py",
            "pkg/inner/__init__.py",
        }
        assert resolve(files, "pkg/sub.py", "from . import *\n") == {
            "pkg/__init__.py",
            "pkg/inner/__init__.py",
        }
        assert resolve({"ns/a.py"}, "main.py", "from ns import *\n") == set()

    def test_conftest_directories_are_import_roots(self) -> None:
        # pytest (prepend mode) puts tests/ on sys.path because of tests/conftest.py.
        files = {"tests/conftest.py", "tests/helpers.py", "tests/unit/test_x.py"}
        assert resolve(files, "tests/unit/test_x.py", "from helpers import make\n") == {
            "tests/helpers.py"
        }
        # ...but only for files below that conftest.
        assert resolve(files, "other/test_y.py", "from helpers import make\n") == set()

    def test_deleted_modules_can_still_be_resolved(self) -> None:
        # build_graph passes deleted paths in so importers keep their edges.
        assert resolve({"gone.py"}, "main.py", "import gone\n") == {"gone.py"}
