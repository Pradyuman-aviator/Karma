"""Symbol-level test selection: narrow a file-level selection to test functions.

File-level selection runs a test file when it transitively imports a changed file.
This module looks inside the files on that path and keeps only the test functions and
classes that can reach what actually changed, the way regression test selection
research suggests doing it for a dynamic language:

* **What changed** is decided per top-level *unit* (function, class, simple assignment,
  imported name), by comparing normalised ASTs of the two versions. Comments,
  formatting and moved code are not changes (like Ekstazi's smart checksums).
* **Who is affected** is decided by *names*, not by a call graph: a unit is affected
  when its code mentions an affected name, resolved through imports, attribute chains
  on modules (``pkg.mod.fn``), re-exports, star imports, string targets
  (``mock.patch("pkg.mod.fn")``), fixtures (by parameter name) and conftest scopes.
  Static method-level call graphs were found to be unsafe and imprecise (Legunsen et
  al., FSE 2016); matching names over-approximates instead (NameRTS, ISSTA 2026).
* **When unsure, fall back to the whole file** (as HyRTS does): module-level code that
  changed or depends on a change, dynamic features (``exec``, ``globals()``,
  ``sys.modules``), import-time hooks (metaclasses, ``__init_subclass__``), autouse
  fixtures, pytest hooks, files that do not parse, and imports this analysis cannot
  see (``importlib``, ``pytest_plugins``). A whole file affects every file importing
  it, which is exactly file-level selection.

The result can only *remove* tests from the file-level selection, never add any.
"""

from __future__ import annotations

import ast
import fnmatch
import logging
import re
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from karma.config import Config
from karma.errors import GitError
from karma.git import ChangeSet, file_versions
from karma.graph import DependencyGraph, matches_any
from karma.languages.python import ImportRef, ModuleResolver, _parse

log = logging.getLogger(__name__)

#: ``(file, name)``; a name of ``None`` means "anything in the file" (a module object)
Key = tuple[str, "str | None"]
WHOLE = "*"

# Calls that can read or rebind any name in a module (`locals()` cannot change them).
_DYNAMIC_CALLS = frozenset({"exec", "eval", "globals"})
_ATTR_FUNCTIONS = frozenset({"getattr", "hasattr", "setattr", "delattr"})
_MUTATORS = frozenset(
    {
        "append",
        "appendleft",
        "add",
        "clear",
        "discard",
        "extend",
        "extendleft",
        "insert",
        "pop",
        "popitem",
        "popleft",
        "register",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "update",
        "__setitem__",
        "__delitem__",
    }
)
# Names whose change affects every test of the file (or, in a conftest, every test
# beneath it): they run around or configure all of them.
_FILE_WIDE = frozenset(
    {
        "pytestmark",
        "pytest_plugins",
        "setup_module",
        "teardown_module",
        "setUpModule",
        "tearDownModule",
        "setup_function",
        "teardown_function",
        "setup",
        "teardown",
        "load_tests",
        "__test__",
        "__getattr__",
        "__all__",
    }
)
# Class members that run when a class (or a subclass) is created, at import time.
_CREATION_HOOKS = frozenset({"__init_subclass__", "__class_getitem__", "__prepare__"})
_COMPOUND = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    *((ast.Match,) if hasattr(ast, "Match") else ()),
    *((ast.TryStar,) if hasattr(ast, "TryStar") else ()),
)
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,7}")


# --------------------------------------------------------------------------- what changed


@dataclass(frozen=True)
class _Split:
    """A module's top-level statements: named units and the rest ("module code")."""

    #: name -> (fingerprint, first line)
    units: dict[str, tuple[str, int]]
    #: fingerprints of the other statements, in order
    code: tuple[str, ...]
    #: ImportRefs of the top-level imports, to compare which repository files they load
    imports: tuple[ImportRef, ...]


def _fingerprint(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _split(tree: ast.Module) -> _Split:
    named: dict[str, list[tuple[str, int, str]]] = {}  # name -> [(kind, line, fingerprint)]
    code: list[str] = []
    anon_binds: set[str] = set()
    imports: list[ImportRef] = []
    for index, stmt in enumerate(tree.body):
        if _ignored(stmt, index):
            continue
        for name, kind, fp in _named(stmt):
            named.setdefault(name, []).append((kind, stmt.lineno, fp))
        if isinstance(stmt, ast.Import):
            imports.extend(ImportRef(alias.name) for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom) and not _is_star(stmt):
            imports.append(
                ImportRef(stmt.module or "", tuple(a.name for a in stmt.names), stmt.level)
            )
        if not _named(stmt):
            code.append(_fingerprint(stmt))
            anon_binds.update(_bound_names(stmt))
    units: dict[str, tuple[str, int]] = {}
    for name, entries in named.items():
        kinds = {kind for kind, _, _ in entries}
        if name in anon_binds or (len(entries) > 1 and kinds != {"import"}):
            # Defined more than once: order matters, so treat it as module code.
            code.extend(fp for _, _, fp in entries)
            continue
        units[name] = ("\n".join(sorted(fp for _, _, fp in entries)), entries[0][1])
    return _Split(units, tuple(code), tuple(imports))


def _ignored(stmt: ast.stmt, index: int) -> bool:
    """Statements that never run on import: the docstring, ``__main__`` and type checks."""
    if index == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return isinstance(stmt.value.value, str)
    if _is_type_checking(stmt):
        return True
    if isinstance(stmt, ast.If) and not stmt.orelse:
        test = stmt.test
        # `if __name__ == "__main__":`, possibly `and` another condition
        conditions = [test]
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            conditions = test.values
        return any(_is_main_check(c) for c in conditions)
    return False


def _is_main_check(test: ast.expr) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, *test.comparators]
    names = {s.id for s in sides if isinstance(s, ast.Name)}
    values = {s.value for s in sides if isinstance(s, ast.Constant)}
    return names == {"__name__"} and values == {"__main__"}


def _is_type_checking(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or stmt.orelse:
        return False
    test = stmt.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_star(stmt: ast.ImportFrom) -> bool:
    return any(alias.name == "*" for alias in stmt.names)


def _named(stmt: ast.stmt) -> list[tuple[str, str, str]]:
    """``(name, kind, fingerprint)`` for each unit a top-level statement defines."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [(stmt.name, "def", _fingerprint(stmt))]
    if isinstance(stmt, ast.ClassDef):
        return [(stmt.name, "class", _fingerprint(stmt))]
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            return [(target.id, "data", _fingerprint(stmt))]
    if (
        isinstance(stmt, ast.AnnAssign)
        and stmt.value is not None
        and isinstance(stmt.target, ast.Name)
        and stmt.simple
    ):
        return [(stmt.target.id, "data", _fingerprint(stmt))]
    if isinstance(stmt, ast.Import):
        return [
            ((a.asname or a.name.partition(".")[0]), "import", f"import {a.name} as {a.asname}")
            for a in stmt.names
        ]
    if isinstance(stmt, ast.ImportFrom) and not _is_star(stmt):
        where = f"{'.' * stmt.level}{stmt.module or ''}"
        return [
            ((a.asname or a.name), "import", f"from {where} import {a.name} as {a.asname}")
            for a in stmt.names
        ]
    return []


def _bound_names(stmt: ast.stmt) -> set[str]:
    """Names a module-level statement may bind (not looking inside nested scopes)."""
    names: set[str] = set()
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            stack.extend(node.decorator_list)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.partition(".")[0])
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


@dataclass(frozen=True)
class FileChange:
    """What changed in one file: everything (``whole``), or these top-level names."""

    whole: bool
    names: Mapping[str, int] = field(default_factory=dict)  # name -> line
    why: str = ""


def diff_units(
    before: bytes | None,
    after: bytes | None,
    path: str,
    resolver: ModuleResolver | None = None,
) -> FileChange:
    """Compare two versions of a Python file unit by unit."""
    if before is None or after is None:
        return FileChange(True, why="added" if before is None else "deleted")
    if before == after:
        return FileChange(True, why="no difference to compare (whole file)")
    if not path.endswith(".py"):
        return FileChange(True, why="not Python")
    try:
        old, new = _split(_parse(before, path)), _split(_parse(after, path))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return FileChange(True, why="not parseable")
    if old.code != new.code:
        return FileChange(True, why="module-level code changed")
    if resolver is not None:
        loaded_before = resolver.resolve(path, old.imports)
        if loaded_before != resolver.resolve(path, new.imports):
            return FileChange(True, why="imports a different set of modules")
    names: dict[str, int] = {}
    for name in sorted(old.units.keys() | new.units.keys()):
        was, now = old.units.get(name), new.units.get(name)
        if was is None or now is None or was[0] != now[0]:
            names[name] = (now or was or ("", 0))[1]
    return FileChange(False, names)


# --------------------------------------------------------------------------- what depends on it


@dataclass
class _Unit:
    """A top-level unit, or (``name`` None) an anonymous module-level statement."""

    name: str | None
    kind: str  # def, class, data, import, code (simple statement), compound
    line: int
    refs: set[Key] = field(default_factory=set)
    #: keys it may change when it is affected (globals, mutated module objects)
    writes: set[Key] = field(default_factory=set)
    #: data-like names passed to calls: may be mutated by them
    arg_keys: set[Key] = field(default_factory=set)
    #: objects its decorators register it with (``@app.route`` -> app)
    deco_receivers: set[Key] = field(default_factory=set)
    #: decorator functions applied to it (their own writes happen on import)
    deco_funcs: set[Key] = field(default_factory=set)
    #: objects it calls methods on (``app.add_route(...)``)
    receivers: set[Key] = field(default_factory=set)
    #: a class's bases: their last names, and what they refer to
    base_names: tuple[str, ...] = ()
    base_keys: set[Key] = field(default_factory=set)
    binds: tuple[str, ...] = ()
    dynamic: bool = False
    #: runs code when a class (or subclass) is created: __init_subclass__, metaclass
    creation_hook: bool = False
    fixture: bool = False
    #: ``@pytest.fixture(name="client")``: the name tests request it by
    fixture_alias: str | None = None
    autouse: bool = False
    has_init: bool = False
    has_bases: bool = False


@dataclass
class _Binding:
    kind: str  # "module" (import a.b), "from" (from m import n), "star"
    module: str
    level: int = 0
    attr: str = ""


class _Module:
    """The names a file defines and the names each of its units refers to."""

    def __init__(self, path: str, tree: ast.Module, resolver: ModuleResolver) -> None:
        self.path = path
        self._resolver = resolver
        self.bindings: dict[str, list[_Binding]] = {}
        self.stars: list[_Binding] = []
        self.import_refs: list[ImportRef] = []
        self.locals: set[str] = set()
        self.units: dict[str, _Unit] = {}
        self.anonymous: list[_Unit] = []
        self.string_files: set[str] = set()
        #: imports a module whose name is computed: names cannot follow it
        self.opaque = False
        self._collect_bindings(tree)

        split = _split(tree)
        for index, stmt in enumerate(tree.body):
            if _ignored(stmt, index):
                continue
            named = _named(stmt)
            if named and all(name in split.units for name, _, _ in named):
                for name, kind, _ in named:
                    unit = self.units.get(name)
                    if unit is None:
                        unit = self.units[name] = _Unit(name, kind, stmt.lineno)
                    self._analyse(stmt, unit, only_alias=name if kind == "import" else None)
            else:
                kind = "compound" if isinstance(stmt, _COMPOUND) else "code"
                unit = _Unit(None, kind, stmt.lineno, binds=tuple(sorted(_bound_names(stmt))))
                self._analyse(stmt, unit)
                self.anonymous.append(unit)
        self.bound_files = resolver.resolve(path, self.import_refs) | self.string_files
        self.star_files = {f for b in self.stars for f in resolver.resolve(path, [_star_ref(b)])}
        # target file -> units referring to something in it
        self.by_target: dict[str, list[_Unit]] = {}
        for unit in self.all_units():
            for target in {f for f, _ in unit.refs}:
                self.by_target.setdefault(target, []).append(unit)

    def all_units(self) -> Iterator[_Unit]:
        yield from self.units.values()
        yield from self.anonymous

    def kind_of(self, name: str) -> str:
        unit = self.units.get(name)
        if unit is not None:
            return unit.kind
        return "data" if name in self.locals else "unknown"

    # ------------------------------------------------------------------ bindings
    def _collect_bindings(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.import_refs.append(ImportRef(alias.name))
            elif isinstance(node, ast.ImportFrom):
                names = tuple(a.name for a in node.names)
                self.import_refs.append(ImportRef(node.module or "", names, node.level))
        for stmt in _module_statements(tree.body):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.asname:
                        binding = _Binding("module", alias.name)
                    else:
                        binding = _Binding("module", alias.name.partition(".")[0])
                    self.bindings.setdefault(alias.asname or binding.module, []).append(binding)
            elif isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    if alias.name == "*":
                        self.stars.append(_Binding("star", stmt.module or "", stmt.level))
                    else:
                        binding = _Binding("from", stmt.module or "", stmt.level, alias.name)
                        self.bindings.setdefault(alias.asname or alias.name, []).append(binding)
            else:
                self.locals.update(_bound_names(stmt) if not _is_nested_scope(stmt) else ())
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.locals.add(stmt.name)

    def _local_bindings(self, node: ast.AST) -> dict[str, list[_Binding]]:
        """Imports inside a function or class body bind names there."""
        found: dict[str, list[_Binding]] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for alias in sub.names:
                    module = alias.name if alias.asname else alias.name.partition(".")[0]
                    found.setdefault(alias.asname or module, []).append(_Binding("module", module))
            elif isinstance(sub, ast.ImportFrom):
                for alias in sub.names:
                    if alias.name != "*":
                        binding = _Binding("from", sub.module or "", sub.level, alias.name)
                        found.setdefault(alias.asname or alias.name, []).append(binding)
        return found

    # ------------------------------------------------------------------ resolution
    def _modules(self, module: str, level: int) -> list[str]:
        return self._resolver.module_files(self.path, module, level)

    def _walk(self, module: str, level: int, attrs: Sequence[str]) -> set[Key]:
        """Keys for ``module.attr1.attr2...``, descending into submodules."""
        files = self._modules(module, level)
        if not files:
            return set()
        if not attrs:
            return {(f, None) for f in files}
        head = attrs[0]
        keys: set[Key] = {(f, head) for f in files}
        sub = f"{module}.{head}" if module else head
        if self._modules(sub, level):
            keys |= self._walk(sub, level, attrs[1:])
        return keys

    def resolve(
        self,
        name: str,
        attrs: Sequence[str] = (),
        local: Mapping[str, list[_Binding]] | None = None,
    ) -> set[Key]:
        """Keys that ``name.attr...`` may refer to, inside this file."""
        keys: set[Key] = set()
        bindings = [*self.bindings.get(name, ()), *(local or {}).get(name, ())]
        for binding in bindings:
            if binding.kind == "module":
                keys |= self._walk(binding.module, 0, attrs)
            else:  # from module import attr
                keys |= {(f, binding.attr) for f in self._modules(binding.module, binding.level)}
                sub = f"{binding.module}.{binding.attr}" if binding.module else binding.attr
                if self._modules(sub, binding.level):
                    keys |= self._walk(sub, binding.level, attrs)
        if name in self.locals or not bindings:
            keys.add((self.path, name))
        if not bindings and name not in self.locals:
            for star in self.stars:
                keys |= {(f, name) for f in self._modules(star.module, star.level)}
                sub = f"{star.module}.{name}" if star.module else name
                keys |= {(f, None) for f in self._modules(sub, star.level)}
        return keys

    def prefixed_modules(self, prefix: str) -> list[str]:
        found = self._resolver.modules_with_prefix(self.path, prefix)
        self.string_files.update(found)
        return found

    def _string_keys(self, text: str) -> set[Key]:
        """``"name"`` (a fixture, an attribute) or ``"pkg.mod.fn"`` (a patch target)."""
        if "." not in text:
            return {(self.path, text)}
        parts = text.split(".")
        for i in range(len(parts), 0, -1):
            files = self._modules(".".join(parts[:i]), 0)
            if files:
                keys = self._walk(".".join(parts[:i]), 0, parts[i : i + 1])
                self.string_files.update(f for f, _ in keys)
                return keys
        return set()

    # ------------------------------------------------------------------ units
    def _analyse(self, stmt: ast.stmt, unit: _Unit, only_alias: str | None = None) -> None:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                bound = alias.asname or (
                    alias.name.partition(".")[0] if isinstance(stmt, ast.Import) else alias.name
                )
                if only_alias is None or bound == only_alias:
                    unit.refs |= self.resolve(bound) - {(self.path, bound)}
            return
        local = self._local_bindings(stmt)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self._decorators(stmt, unit, local)
        if isinstance(stmt, ast.ClassDef):
            chains = [_chain(b) for b in stmt.bases]
            unit.base_names = tuple(c[-1] if c else "?" for c in chains)
            for chain in chains:
                if chain:
                    unit.base_keys |= self.resolve(chain[0], chain[1:], local)
            unit.has_bases = any(name != "object" for name in unit.base_names)
            members = {
                s.name for s in stmt.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            unit.has_init = "__init__" in members
            metaclass = any(k.arg == "metaclass" for k in stmt.keywords)
            bases_type = any(isinstance(b, ast.Name) and b.id == "type" for b in stmt.bases)
            unit.creation_hook = bool(members & _CREATION_HOOKS) or metaclass or bases_type
        _References(self, unit, local).visit(stmt)
        if unit.name is None:
            # A module-level call is run for its effect: `app.add_route("/", view)`.
            unit.writes |= unit.receivers

    def _decorators(
        self,
        stmt: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        unit: _Unit,
        local: Mapping[str, list[_Binding]],
    ) -> None:
        for decorator in stmt.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            chain = _chain(target)
            if "fixture" in chain:
                unit.fixture = True
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        value = keyword.value
                        if keyword.arg == "name" and isinstance(value, ast.Constant):
                            unit.fixture_alias = str(value.value)
                    unit.autouse = any(
                        k.arg == "autouse"
                        and not (isinstance(k.value, ast.Constant) and not k.value.value)
                        for k in decorator.keywords
                    )
            if not chain:
                continue
            if len(chain) > 1:
                unit.deco_receivers |= self.resolve(chain[0], chain[1:-1], local)
            else:
                unit.deco_funcs |= self.resolve(chain[0], (), local)


def _module_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-level statements, including inside ``if``/``try``/``with`` blocks."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt):
            yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in ("body", "orelse", "finalbody", "handlers", "cases"):
            block = getattr(node, name, None)
            if isinstance(block, list):
                stack.extend(reversed([s for s in block if isinstance(s, ast.AST)]))


def _is_nested_scope(stmt: ast.AST) -> bool:
    return isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def _star_ref(binding: _Binding) -> ImportRef:
    return ImportRef(binding.module, ("*",), binding.level)


def _literal_prefix(node: ast.AST | None) -> str:
    """The constant start of a computed string: ``f"a.b{x}"`` or ``"a.b" + x``."""
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            return node.left.value
        return _literal_prefix(node.left)
    return ""


def _chain(node: ast.AST) -> list[str]:
    """``a.b.c`` -> ``["a", "b", "c"]``; ``[]`` if not rooted at a plain name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return []
    parts.append(node.id)
    return parts[::-1]


class _References(ast.NodeVisitor):
    """Collect what one unit refers to, writes to, and whether it is dynamic."""

    def __init__(self, module: _Module, unit: _Unit, local: Mapping[str, list[_Binding]]):
        self.module = module
        self.unit = unit
        self.local = local

    def _keys(self, chain: Sequence[str]) -> set[Key]:
        return self.module.resolve(chain[0], chain[1:], self.local)

    def _module_level(self, name: str) -> bool:
        return name in self.module.locals or name in self.module.bindings or name in self.local

    def visit_Name(self, node: ast.Name) -> None:
        self.unit.refs |= self._keys([node.id])

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = _chain(node)
        if not chain:
            self.generic_visit(node)
            return
        keys = self._keys(chain)
        self.unit.refs |= keys
        if isinstance(node.ctx, (ast.Store, ast.Del)) and self._module_level(chain[0]):
            # `mod.X = ...` / `Config.flag = ...`: the object is changed for everyone.
            self.unit.writes |= keys

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            chain = _chain(node.value)
            if chain == ["sys", "modules"]:
                self.unit.dynamic = True  # sys.modules[name] = ...: replaces a module
            if chain and self._module_level(chain[0]):
                self.unit.writes |= self._keys(chain)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.unit.writes |= {(self.module.path, name) for name in node.names}

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, str) and len(value) <= 200 and _DOTTED.fullmatch(value):
            self.unit.refs |= self.module._string_keys(value)

    def visit_arg(self, node: ast.arg) -> None:
        # A parameter can request a pytest fixture of that name.
        self.unit.refs.add((self.module.path, node.arg))
        if node.annotation is not None:
            self.visit(node.annotation)

    def visit_Call(self, node: ast.Call) -> None:
        func_chain = _chain(node.func)
        if func_chain and func_chain[-1] in ("import_module", "__import__"):
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                prefix = _literal_prefix(first)
                if prefix and "." in prefix:
                    # import_module(f"pkg.plugins.{name}"): any module under that prefix
                    found = self.module.prefixed_modules(prefix)
                    self.unit.refs |= {(f, None) for f in found}
                else:
                    self.module.opaque = True
        if len(func_chain) > 1 and self._module_level(func_chain[0]):
            receiver = self._keys(func_chain[:-1])
            self.unit.receivers |= receiver
            if func_chain[-1] in _MUTATORS:
                self.unit.writes |= receiver
        if func_chain[:2] == ["sys", "modules"] and func_chain[-1] in _MUTATORS:
            self.unit.dynamic = True
        if len(func_chain) == 1 and func_chain[0] in _DYNAMIC_CALLS:
            literal = node.args and isinstance(node.args[0], ast.Constant)
            # eval("2+2") only evaluates; exec(...) and globals() can rebind names.
            if not (func_chain[0] == "eval" and literal):
                self.unit.dynamic = True
        if func_chain == ["vars"]:
            target = _chain(node.args[0]) if node.args else []
            if not node.args or (target and any(n is None for _, n in self._keys(target))):
                self.unit.dynamic = True  # vars() / vars(module): a module's namespace
        if func_chain in (["setattr"], ["delattr"]) and node.args:
            target = _chain(node.args[0])
            if target and any(name is None for _, name in self._keys(target)):
                self.unit.dynamic = True  # setattr(module, ...)
        if func_chain and func_chain[-1] in _ATTR_FUNCTIONS and len(node.args) >= 2:
            target, attr = _chain(node.args[0]), node.args[1]
            if target and isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                self.unit.refs |= self._keys([*target, attr.value])
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            chain = _chain(arg)
            if chain and self._module_level(chain[0]):
                self.unit.arg_keys |= self._keys(chain)
        self.generic_visit(node)


# --------------------------------------------------------------------------- propagation


@dataclass(frozen=True)
class Refinement:
    """Symbol-level narrowing of a file-level selection."""

    #: test file -> node ids to run; a file absent from it runs whole
    items: Mapping[str, tuple[str, ...]]
    #: node id -> ("tests/t.py::test_x", "app/b.py::total", ..., "app/tax.py::rate")
    item_reasons: Mapping[str, tuple[str, ...]]
    #: test files of the file-level selection that no affected symbol reaches
    dropped: tuple[str, ...]
    #: changed file -> line of each changed symbol, or "whole: reason"
    changes: Mapping[str, FileChange]


def refine(
    tests: Sequence[str],
    candidates: Iterable[str],
    changes: ChangeSet,
    graph: DependencyGraph,
    config: Config,
    root: Path,
    all_tests: Sequence[str],
) -> Refinement:
    """Narrow file-level ``tests`` to the test functions affected by ``changes``.

    ``candidates`` are the files file-level selection reached (only they can matter).
    """
    resolver = ModuleResolver([*graph.files, *changes.deleted], config.source_roots)
    changed = list(changes.all)
    try:
        versions = file_versions(changes, [p for p in changed if p.endswith(".py")], root)
    except (GitError, OSError, ValueError) as exc:  # stay at file level
        log.info("symbol-level selection unavailable (%s); selecting whole files", exc)
        versions = {}
    file_changes: dict[str, FileChange] = {}
    for path in changed:
        if path in changes.submodules or path not in versions:
            file_changes[path] = FileChange(True, why="not compared")
        else:
            before, after = versions[path]
            file_changes[path] = diff_units(before, after, path, resolver)
    engine = _Engine(graph, config, resolver, root, set(candidates), list(all_tests))
    for path, change in file_changes.items():
        if change.whole:
            engine.make_whole(path, ("changed", path, change.why))
        else:
            for name, line in change.names.items():
                engine.mark(path, name, ("changed", path, f"line {line}"))
    engine.run()
    return engine.result(tests, file_changes)


Origin = tuple[str, str, str]  # ("changed", file, detail)


class _Engine:
    def __init__(
        self,
        graph: DependencyGraph,
        config: Config,
        resolver: ModuleResolver,
        root: Path,
        candidates: set[str],
        all_tests: list[str],
    ) -> None:
        self.graph = graph
        self.config = config
        self.resolver = resolver
        self.root = root
        self.candidates = candidates
        self.all_tests = all_tests
        self.test_set = frozenset(all_tests)
        self.names: dict[str, set[str]] = {}
        self.whole: set[str] = set()
        #: (file, name or WHOLE) -> what made it affected
        self.why: dict[tuple[str, str], tuple[str, str] | Origin] = {}
        self.queue: deque[tuple[str, str]] = deque()
        self._queued: set[tuple[str, str]] = set()
        self._modules: dict[str, _Module | None] = {}
        self._reverse: dict[str, set[str]] | None = None
        self._done_anonymous: set[int] = set()
        plugins = resolver.resolve("conftest.py", [ImportRef(p) for p in config.plugins])
        self.plugins = plugins

    # ------------------------------------------------------------------ analysis cache
    def module(self, path: str) -> _Module | None:
        if path not in self._modules:
            self._modules[path] = None
            if path.endswith(".py"):
                try:
                    data = (self.root / path).read_bytes()
                    self._modules[path] = _Module(path, _parse(data, path), self.resolver)
                except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                    log.debug("%s: analysed as a whole file (%s)", path, exc)
        return self._modules[path]

    def reverse(self, path: str) -> set[str]:
        """Candidate files whose code refers to ``path`` (imports, strings) or imports it."""
        if self._reverse is None:
            self._reverse = {}
            for candidate in self.candidates:
                module = self.module(candidate)
                if module is not None:
                    for target in module.by_target:
                        self._reverse.setdefault(target, set()).add(candidate)
        found = set(self._reverse.get(path, ()))
        found.update(d for d in self.graph.dependents(path) if d in self.candidates)
        return found

    # ------------------------------------------------------------------ marking
    def affected_any(self, path: str) -> bool:
        return path in self.whole or bool(self.names.get(path))

    def hit(self, key: Key) -> tuple[str, str] | None:
        path, name = key
        if path in self.whole:
            return (path, WHOLE)
        names = self.names.get(path)
        if not names:
            return None
        if name is None:
            return (path, min(names))
        return (path, name) if name in names else None

    def make_whole(self, path: str, via: tuple[str, str] | Origin) -> None:
        if path in self.whole:
            return
        self.whole.add(path)
        self.why[(path, WHOLE)] = via
        self._changed(path)
        directory, _, filename = path.rpartition("/")
        if filename in ("conftest.py", "__init__.py"):
            prefix = f"{directory}/" if directory else ""
            for test in self.all_tests:
                if test.startswith(prefix) and test != path:
                    self.make_whole(test, (path, WHOLE))

    def mark(self, path: str, name: str, via: tuple[str, str] | Origin) -> None:
        if path in self.whole or name in self.names.get(path, ()):
            return
        self.names.setdefault(path, set()).add(name)
        self.why[(path, name)] = via
        self._changed(path)
        module = self.module(path)
        here = (path, name)
        if module is None:
            return
        unit = module.units.get(name)
        if unit is not None:
            self._consequences(module, unit, here)
        is_test = path in self.test_set
        filename = path.rpartition("/")[2]
        scoped = is_test or filename == "conftest.py" or path in self.plugins
        runs_around_tests = scoped and (
            name in _FILE_WIDE or name.startswith("pytest_") or (unit is not None and unit.autouse)
        )
        # __getattr__ and __all__ change how every other module sees this one. (__all__
        # lists names as strings, so only a change to the list itself counts.)
        all_changed = name == "__all__" and len(via) == 3
        if runs_around_tests or name == "__getattr__" or all_changed:
            self.make_whole(path, here)
        if unit is not None and unit.fixture_alias and path not in self.whole:
            self.mark(path, unit.fixture_alias, here)
        # A conftest's fixtures are requested by name from every test beneath it.
        if (
            filename == "conftest.py"
            and path not in self.whole
            and unit is not None
            and (unit.fixture)
        ):
            directory = path.rpartition("/")[0]
            prefix = f"{directory}/" if directory else ""
            for target in sorted(self.candidates):
                fixtures_visible = target in self.test_set or target.endswith("/conftest.py")
                if target != path and target.startswith(prefix) and fixtures_visible:
                    self.mark(target, unit.fixture_alias or name, here)

    def _changed(self, path: str) -> None:
        for dependent in sorted(self.reverse(path) | {path}):
            if (dependent, path) not in self._queued:
                self._queued.add((dependent, path))
                self.queue.append((dependent, path))
        if path in self.plugins:
            for test in self.all_tests:
                self.make_whole(test, (path, WHOLE))
        for pattern, targets in self.config.mappings:
            if matches_any(path, [pattern]):
                for target in sorted(self.graph.files):
                    if matches_any(target, targets):
                        self.make_whole(target, (path, WHOLE))

    def mark_key(self, key: Key, via: tuple[str, str], depth: int = 0) -> None:
        path, name = key
        if name is None:
            self.make_whole(path, via)
            return
        self.mark(path, name, via)
        # A name imported from elsewhere: the object itself lives in that module.
        module = self.module(path)
        if module is not None and depth < 8:
            unit = module.units.get(name)
            if unit is not None and unit.kind == "import":
                for origin in _ordered(unit.refs):
                    self.mark_key(origin, via, depth + 1)

    def kind(self, key: Key, depth: int = 0) -> str:
        path, name = key
        module = self.module(path)
        if module is None or name is None:
            return "module"
        kind = module.kind_of(name)
        if kind == "import":
            if depth > 8:
                return "unknown"
            kinds = {self.kind(k, depth + 1) for k in module.units[name].refs}
            return "data" if "data" in kinds else (kinds.pop() if len(kinds) == 1 else "unknown")
        return kind

    def _consequences(self, module: _Module, unit: _Unit, here: tuple[str, str]) -> None:
        """An affected unit changes more than itself: what it writes, what it registers."""
        if unit.dynamic or unit.creation_hook:
            self.make_whole(module.path, here)
            return
        for key in _ordered(unit.writes):
            self.mark_key(key, here)
        for key in _ordered(unit.arg_keys | unit.deco_receivers):
            if self.kind(key) == "data":
                self.mark_key(key, here)
        for key in _ordered(unit.deco_funcs):
            target = self.module(key[0])
            decorator = target.units.get(key[1] or "") if target is not None else None
            if decorator is not None and decorator.kind == "def":
                for written in _ordered(decorator.writes):
                    self.mark_key(written, here)

    # ------------------------------------------------------------------ fixpoint
    def run(self) -> None:
        while self.queue:
            path, trigger = self.queue.popleft()
            self._queued.discard((path, trigger))
            if path in self.candidates or path == trigger:
                self._reconsider(path, trigger)

    def _reconsider(self, path: str, trigger: str) -> None:
        if path in self.whole:
            return
        module = self.module(path)
        if module is None:
            if path != trigger and self.affected_any(trigger):
                self.make_whole(path, self._any_hit(trigger))
            return
        if path != trigger and trigger in self.graph.dependencies(path):
            if trigger in self.whole:
                self.make_whole(path, (trigger, WHOLE))
                return
            # (Not counting the packages a file merely lives in: they run on import.)
            ancestor = trigger.endswith("/__init__.py") and path.startswith(
                trigger[: -len("__init__.py")]
            )
            opaque = (module.opaque and not ancestor) or trigger not in module.bound_files
            if opaque and self.affected_any(trigger):
                # Loaded in a way names cannot follow (importlib, pytest_plugins, ...).
                self.make_whole(path, self._any_hit(trigger))
                return
        for unit in module.by_target.get(trigger, ()):
            if unit.name is not None:
                if unit.name in self.names.get(path, ()):
                    continue
                found = self._first_hit(unit)
                if found is not None:
                    self.mark(path, unit.name, found)
            elif id(unit) not in self._done_anonymous:
                found = self._first_hit(unit)
                if found is not None:
                    self._done_anonymous.add(id(unit))
                    here = (path, f"<line {unit.line}>")
                    self.why[here] = found
                    # Module code that uses a change: what it binds or changes is affected.
                    for name in unit.binds:
                        self.mark(path, name, here)
                    self._consequences(module, unit, here)
            if path in self.whole:
                return

    def _any_hit(self, path: str) -> tuple[str, str]:
        return (path, WHOLE) if path in self.whole else (path, min(self.names[path]))

    def _first_hit(self, unit: _Unit) -> tuple[str, str] | None:
        for key in _ordered(unit.refs):
            found = self.hit(key)
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------ result
    def result(self, tests: Sequence[str], changes: Mapping[str, FileChange]) -> Refinement:
        items: dict[str, tuple[str, ...]] = {}
        reasons: dict[str, tuple[str, ...]] = {}
        dropped: list[str] = []
        for test in tests:
            module = self.module(test) if test not in self.whole else None
            if module is None:
                continue  # whole file: changed throughout, unparseable or not Python
            selected = self._items(module, self.names.get(test, set()))
            if selected is None:
                continue
            if not selected:
                dropped.append(test)
                continue
            items[test] = tuple(f"{test}::{name}" for name in sorted(selected))
            for name in sorted(selected):
                reasons[f"{test}::{name}"] = self.chain(test, name)
        return Refinement(items, reasons, tuple(dropped), dict(changes))

    def _items(self, module: _Module, names: set[str]) -> list[str] | None:
        """The affected test items among ``names``, or ``None`` to run the whole file
        (when pytest may collect an affected item this analysis cannot name)."""
        functions = self.config.python_functions
        classes = self.config.python_classes
        selected = []
        for name in sorted(names):
            unit = module.units.get(name)
            test_like = _pytest_match(name, functions) or _pytest_match(name, classes)
            if unit is None:
                if test_like and name in module.locals:
                    return None  # defined by module code (in an `if`, twice, ...)
                continue
            if unit.kind == "def" and _pytest_match(name, functions) and not unit.fixture:
                selected.append(name)
            elif unit.kind == "class" and _pytest_match(name, classes):
                if not unit.has_init:  # pytest does not collect classes with __init__
                    selected.append(name)
            elif unit.kind == "class" and self._maybe_testcase(unit):
                return None  # unittest.TestCase subclasses are collected whatever their name
            elif test_like and unit.kind in ("data", "import"):
                return None
        return selected

    def _maybe_testcase(self, unit: _Unit, depth: int = 0) -> bool:
        if any("Test" in name for name in unit.base_names):
            return True
        if depth > 8:
            return True
        for path, name in unit.base_keys:
            module = self.module(path)
            base = module.units.get(name or "") if module is not None else None
            if base is not None and base.kind == "class" and self._maybe_testcase(base, depth + 1):
                return True
        return False

    def chain(self, path: str, name: str) -> tuple[str, ...]:
        out = [f"{path}::{name}"]
        key: tuple[str, str] | Origin = (path, name)
        seen = {key}
        while True:
            via = self.why.get(key)  # type: ignore[arg-type]
            if via is None:
                break
            if len(via) == 3:
                out[-1] += f" (changed, {via[2]})"
                break
            label = via[0] if via[1] == WHOLE else f"{via[0]}::{via[1]}"
            if via in seen:
                break
            seen.add(via)
            if label != out[-1]:
                out.append(label)
            key = via
        return tuple(out)


def _ordered(keys: Iterable[Key]) -> list[Key]:
    """A stable order, so explanations are the same on every run."""
    return sorted(keys, key=lambda key: (key[0], key[1] or ""))


def _pytest_match(name: str, patterns: Sequence[str]) -> bool:
    """pytest's python_functions / python_classes: prefixes, or globs."""
    for pattern in patterns:
        if any(c in pattern for c in "*?["):
            if fnmatch.fnmatchcase(name, pattern):
                return True
        elif name.startswith(pattern):
            return True
    return False
