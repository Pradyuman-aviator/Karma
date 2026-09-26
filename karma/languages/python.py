"""Static import analysis for Python source files.

Analysis happens in two steps so the expensive part can be cached per file:

1. :func:`parse_imports` turns source code into :class:`ImportRef` records. The result
   depends only on the file's bytes.
2. :class:`ModuleResolver` maps those records onto files in the repository. This is
   cheap and re-done on every run, because it depends on which files exist.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Union

log = logging.getLogger(__name__)

Source = Union[str, bytes]


@dataclass(frozen=True)
class ImportRef:
    """One imported module, before it is resolved to a file.

    ``import a.b``            -> ``ImportRef("a.b")``
    ``from a.b import c, d``  -> ``ImportRef("a.b", ("c", "d"))``
    ``from ..x import y``     -> ``ImportRef("x", ("y",), level=2)``
    """

    module: str
    names: tuple[str, ...] = ()
    level: int = 0

    def to_json(self) -> list[Any]:
        return [self.module, list(self.names), self.level]

    @classmethod
    def from_json(cls, data: Sequence[Any]) -> ImportRef:
        module, names, level = data
        if not (isinstance(module, str) and isinstance(level, int) and isinstance(names, list)):
            raise ValueError(f"malformed import record: {data!r}")
        return cls(module, tuple(str(n) for n in names), level)


# --------------------------------------------------------------------------- parsing


def parse_imports(source: Source, filename: str = "<unknown>") -> tuple[ImportRef, ...]:
    """Return every module ``source`` may import, in a stable order.

    Covers ``import``/``from`` statements anywhere in the file (including inside
    functions and ``if TYPE_CHECKING:`` blocks), ``importlib.import_module("x")`` and
    ``__import__("x")`` calls with literal arguments, and ``pytest_plugins``
    declarations. If the file cannot be parsed (for example it uses syntax newer than
    the running interpreter) a line-based scan is used so its edges are not lost.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        log.debug("%s: not parseable by this Python; falling back to a line scan", filename)
        return tuple(dict.fromkeys(_scan_imports(source)))

    refs: dict[ImportRef, None] = {}  # insertion-ordered set
    # Import statements can only appear as statements, so walking statement blocks
    # (a small fraction of all AST nodes) finds them all, in source order.
    for node in _statements(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs[ImportRef(alias.name)] = None
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            refs[ImportRef(node.module or "", names, node.level)] = None
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_pytest_plugins(node):
            assert node.value is not None
            for plugin in _string_literals(node.value):
                refs[ImportRef(plugin)] = None
    # Dynamic imports are expressions and need a full walk; only pay for it when needed.
    text_hint = source if isinstance(source, bytes) else source.encode("utf-8", "replace")
    if b"import_module" in text_hint or b"__import__" in text_hint:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                ref = _dynamic_import(node)
                if ref is not None:
                    refs[ref] = None
    return tuple(refs)


_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _statements(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Yield every statement in ``body`` and in all nested blocks, depth-first."""
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        for field_name in _BLOCK_FIELDS:
            children = getattr(node, field_name, None)
            if isinstance(children, list):
                stack.extend(reversed(children))


def _dynamic_import(call: ast.Call) -> ImportRef | None:
    """Recognise ``importlib.import_module("m"[, "pkg"])`` and ``__import__("m")``."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in ("import_module", "__import__") or not call.args:
        return None
    first = call.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    module: str = first.value
    if not module.startswith("."):
        return ImportRef(module) if module else None
    if name != "import_module":
        return None
    # Relative: needs a literal package, e.g. import_module(".mod", "pkg.sub")
    package_node = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "package":
            package_node = keyword.value
    if not (isinstance(package_node, ast.Constant) and isinstance(package_node.value, str)):
        return None
    level = len(module) - len(module.lstrip("."))
    parts = package_node.value.split(".")
    if level - 1 >= len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    rest = module[level:]
    return ImportRef(".".join([*base, rest] if rest else base))


def _is_pytest_plugins(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets)


def _string_literals(node: ast.expr) -> Iterator[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for element in node.elts:
            yield from _string_literals(element)


_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([\w.][\w. \t,]*)", re.MULTILINE)
_FROM_RE = re.compile(
    r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|(?:[^\n\\]|\\\n)+)",
    re.MULTILINE,
)


def _scan_imports(source: Source) -> Iterator[ImportRef]:
    """Best-effort regex extraction of imports, used only when ``ast.parse`` fails."""
    text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
    for match in _IMPORT_RE.finditer(text):
        for item in match.group(1).split(","):
            module = item.split(" as ")[0].strip()
            if module:
                yield ImportRef(module)
    for match in _FROM_RE.finditer(text):
        dots, module, names_blob = match.groups()
        names_blob = names_blob.split("#")[0].strip("()").replace("\\\n", " ")
        names = tuple(n.split(" as ")[0].strip() for n in names_blob.split(",") if n.strip())
        yield ImportRef(module, names, len(dots))


# --------------------------------------------------------------------------- resolving


def _parent(path: str) -> str:
    """``"a/b/c.py" -> "a/b"``; ``"c.py" -> ""`` (the analysis root)."""
    return path.rpartition("/")[0]


def _normalise_root(root: str) -> str:
    root = root.replace("\\", "/").strip("/")
    while root.startswith("./"):
        root = root[2:]
    return "" if root == "." else root


def _join(*parts: str) -> str:
    return "/".join(p for p in parts if p)


class ModuleResolver:
    """Maps :class:`ImportRef` records to repository files.

    Absolute imports are looked up under each *source root* (the repository root and
    ``src/`` by default) and under the importing file's own import root: the first
    directory above it that is not a package. That last rule mirrors how Python runs
    scripts and how pytest's default ``prepend`` import mode loads test modules, so
    ``import helpers`` inside ``tests/test_x.py`` resolves to ``tests/helpers.py``.

    Importing ``a.b.c`` executes ``a/__init__.py`` and ``a/b/__init__.py`` too, so those
    are reported as dependencies as well. Namespace packages (no ``__init__.py``) work.
    """

    def __init__(self, files: Iterable[str], source_roots: Sequence[str] = ("", "src")) -> None:
        self._files = frozenset(files)
        self._roots = tuple(dict.fromkeys(_normalise_root(r) for r in source_roots))
        self._packages = frozenset(
            _parent(f) for f in self._files if f.rpartition("/")[2] == "__init__.py"
        )

    def resolve(self, importer: str, refs: Iterable[ImportRef]) -> set[str]:
        """Return the repository files that ``importer`` depends on via ``refs``."""
        roots = (*self._roots, self._import_root(importer))
        found: set[str] = set()
        for ref in refs:
            if ref.level:
                found.update(self._resolve_relative(importer, ref))
                continue
            for root in dict.fromkeys(roots):
                found.update(self._resolve_absolute(ref, root))
        found.discard(importer)
        return found

    def _import_root(self, importer: str) -> str:
        directory = _parent(importer)
        while directory and directory in self._packages:
            directory = _parent(directory)
        return directory

    def _module_file(self, path: str) -> str | None:
        for candidate in (f"{path}.py", _join(path, "__init__.py")):
            if candidate in self._files:
                return candidate
        return None

    def _resolve_absolute(self, ref: ImportRef, root: str) -> Iterator[str]:
        if not ref.module:
            return
        parts = ref.module.split(".")
        # Every package along the dotted path is executed on import.
        for i in range(1, len(parts) + 1):
            found = self._module_file(_join(root, *parts[:i]))
            if found:
                yield found
        # ``from a import b`` may import the submodule ``a/b.py``.
        for name in ref.names:
            if name != "*":
                found = self._module_file(_join(root, *parts, name))
                if found:
                    yield found

    def _resolve_relative(self, importer: str, ref: ImportRef) -> Iterator[str]:
        package = _parent(importer)
        for _ in range(ref.level - 1):
            if not package:
                return  # beyond the top-level package: invalid at runtime anyway
            package = _parent(package)
        parts: list[str] = ref.module.split(".") if ref.module else []
        if not parts:
            found = self._module_file(package) if package else None
            if found:
                yield found
        for i in range(1, len(parts) + 1):
            found = self._module_file(_join(package, *parts[:i]))
            if found:
                yield found
        for name in ref.names:
            if name != "*":
                found = self._module_file(_join(package, *parts, name))
                if found:
                    yield found
