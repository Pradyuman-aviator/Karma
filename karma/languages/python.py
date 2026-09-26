"""Static import analysis for Python source files.

Analysis happens in two steps so the expensive part can be cached per file:

1. :func:`parse_imports` turns source code into :class:`ImportRef` records. The result
   depends only on the file's bytes.
2. :class:`ModuleResolver` maps those records onto files in the repository. This is
   cheap and re-done on every run, because it depends on which files exist.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import re
import sys
import threading
import tokenize
import warnings
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

    ``doctest`` marks imports made by ``>>>`` examples in docstrings; they only count
    when pytest runs doctests (``--doctest-modules``).
    """

    module: str
    names: tuple[str, ...] = ()
    level: int = 0
    doctest: bool = False

    def to_json(self) -> list[Any]:
        data: list[Any] = [self.module, list(self.names), self.level]
        return [*data, 1] if self.doctest else data

    @classmethod
    def from_json(cls, data: Sequence[Any]) -> ImportRef:
        if len(data) not in (3, 4):
            raise ValueError(f"malformed import record: {data!r}")
        module, names, level = data[:3]
        if not (isinstance(module, str) and isinstance(level, int) and isinstance(names, list)):
            raise ValueError(f"malformed import record: {data!r}")
        return cls(module, tuple(str(n) for n in names), level, doctest=len(data) == 4)


# --------------------------------------------------------------------------- parsing


def parse_file(data: bytes, path: str) -> tuple[ImportRef, ...]:
    """Imports of a Python module, or of a doctest text file (anything not ``.py``)."""
    if path.endswith(".py"):
        return parse_imports(data, path)
    return parse_doctest_text(data.decode("utf-8", "replace"))


def parse_doctest_text(text: str) -> tuple[ImportRef, ...]:
    """Imports made by the ``>>>`` examples of a doctest text file (e.g. ``test*.txt``)."""
    source = _doctest_source(text)
    return parse_imports(source, "<doctest>", docstrings=False) if source else ()


def _doctest_source(text: str) -> str:
    """The import statements among the ``>>>`` examples in ``text``.

    Only imports matter for dependencies, and parsing just those (with their ``...``
    continuation lines) is far cheaper than parsing whole examples and their output.
    """
    if ">>>" not in text:
        return ""
    lines: list[str] = []
    in_import = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(">>>"):
            code = stripped[3:].strip()
            in_import = code.startswith(("import ", "from ")) or "import_module(" in code
            if in_import:
                lines.append(code)
        elif in_import and stripped.startswith("..."):
            lines.append(stripped[3:].strip())
        else:
            in_import = False
    return "\n".join(lines) + "\n" if lines else ""


def parse_imports(
    source: Source, filename: str = "<unknown>", *, docstrings: bool = True
) -> tuple[ImportRef, ...]:
    """Return every module ``source`` may import, in a stable order.

    Covers ``import``/``from`` statements anywhere in the file (including inside
    functions and ``if TYPE_CHECKING:`` blocks), ``importlib.import_module("x")`` and
    ``__import__("x")`` calls with literal arguments, ``pytest_plugins`` declarations,
    and (marked ``doctest``) imports in docstring examples. If the file cannot be parsed
    (e.g. it uses syntax newer than the running interpreter) a token-based scan is used
    so its edges are not lost.
    """
    try:
        tree = _parse(source, filename)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        log.debug("%s: not parseable by this Python (%s); scanning tokens", filename, exc)
        return tuple(dict.fromkeys(_scan_imports(source)))

    text_hint = source if isinstance(source, bytes) else source.encode("utf-8", "replace")
    refs: dict[ImportRef, None] = {}  # insertion-ordered set
    if docstrings and (b">>> import" in text_hint or b">>> from" in text_hint):
        for ref in _docstring_imports(tree):
            refs[ref] = None
    # Import statements can only appear as statements, so walking statement blocks
    # (a small fraction of all AST nodes) finds them all, in source order.
    for node in _statements(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs[ImportRef(alias.name)] = None
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            refs[ImportRef(node.module or "", names, node.level)] = None
        elif (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.value is not None  # a bare annotation `pytest_plugins: list[str]`
            and _is_pytest_plugins(node)
        ):
            for plugin in _string_literals(node.value):
                for name in _plugin_names(plugin):
                    refs[ImportRef(name)] = None
    # Dynamic imports are expressions and need a full walk; only pay for it when needed.
    if b"import_module" in text_hint or b"__import__" in text_hint:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                dynamic = _dynamic_import(node)
                if dynamic is not None:
                    refs[dynamic] = None
    return tuple(refs)


# Before 3.11, CPython builds the AST recursively in C without a depth check: a deeply
# nested expression (e.g. generated code) overflows the C stack and kills the process
# instead of raising RecursionError. Large files are therefore parsed on a thread with
# a much bigger stack.
_NEEDS_BIG_STACK = sys.version_info < (3, 11)
_BIG_FILE = 64 * 1024
_BIG_STACK = 128 * 1024 * 1024  # the largest size Windows accepts is just under 256 MiB


def _parse(source: Source, filename: str) -> ast.Module:
    def parse() -> ast.Module:
        with warnings.catch_warnings():
            # e.g. SyntaxWarning for "\d": under PYTHONWARNINGS=error it would become a
            # SyntaxError and push a perfectly valid file onto the lossy fallback.
            warnings.simplefilter("ignore")
            return ast.parse(source, filename=filename)

    if not (_NEEDS_BIG_STACK and len(source) > _BIG_FILE):
        return parse()
    result: list[ast.Module] = []
    error: list[BaseException] = []

    def work() -> None:
        try:
            result.append(parse())
        except BaseException as exc:  # re-raised in the calling thread
            error.append(exc)

    previous = threading.stack_size(_BIG_STACK)
    try:
        thread = threading.Thread(target=work, name="karma-parse")
        thread.start()
    finally:
        threading.stack_size(previous)
    thread.join()
    if error:
        raise error[0]
    return result[0]


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


def _docstring_imports(tree: ast.Module) -> Iterator[ImportRef]:
    """Imports in the ``>>>`` examples of module, class and function docstrings."""
    owners: list[ast.AST] = [tree]
    owners.extend(
        node
        for node in _statements(tree.body)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for owner in owners:
        assert isinstance(owner, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        docstring = ast.get_docstring(owner, clean=False)
        if docstring and ">>>" in docstring:
            for ref in parse_imports(_doctest_source(docstring), docstrings=False):
                yield ImportRef(ref.module, ref.names, ref.level, doctest=True)


def _plugin_names(value: str) -> Iterator[str]:
    # pytest accepts a comma-separated string: pytest_plugins = "a.b,c.d"
    return (name.strip() for name in value.split(",") if name.strip())


def _is_pytest_plugins(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets)


def _string_literals(node: ast.expr) -> Iterator[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for element in node.elts:
            yield from _string_literals(element)


# --------------------------------------------------------------------------- fallback scan

# Statements after which `:` starts an inline block, as in `if X: import y`.
_COMPOUND = frozenset(
    {
        "if",
        "elif",
        "else",
        "try",
        "except",
        "finally",
        "with",
        "for",
        "while",
        "def",
        "class",
        "async",
        "match",
        "case",
    }
)
_SKIP_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.ENCODING,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ERRORTOKEN,  # e.g. a stray NUL byte on Python < 3.12
    }
)
_Token = tuple[int, str]


def _scan_imports(source: Source) -> Iterator[ImportRef]:
    """Extract imports without the grammar, for files ``ast`` cannot parse.

    Works on tokens, so comments, ``;``-separated statements, one-line compound
    statements, parenthesised and backslash-continued imports are all handled. If even
    tokenizing fails (e.g. an unterminated string) a line-based regex is the last resort.
    """
    data = source if isinstance(source, bytes) else source.encode("utf-8")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError, LookupError):
        yield from _scan_lines(data.decode("utf-8", "replace"))
        return

    statement: list[_Token] = []
    depth = 0
    for kind, text, *_ in tokens:
        if kind in _SKIP_TOKENS:
            continue
        if kind == tokenize.OP:
            if text in ("(", "[", "{"):
                depth += 1
            elif text in (")", "]", "}"):
                depth = max(depth - 1, 0)
        ends = kind in (tokenize.NEWLINE, tokenize.ENDMARKER) or (
            kind == tokenize.OP
            and depth == 0
            and (text == ";" or (text == ":" and bool(statement) and statement[0][1] in _COMPOUND))
        )
        if ends:
            yield from _imports_in(statement)
            statement = []
        else:
            statement.append((kind, text))


def _imports_in(statement: list[_Token]) -> Iterator[ImportRef]:
    words = [text for _, text in statement]
    if not words:
        return
    if words[0] == "import":
        for group in _split(words[1:], ","):
            module = "".join(group[: group.index("as")] if "as" in group else group)
            if module:
                yield ImportRef(module)
    elif words[0] == "from" and "import" in words:
        split = words.index("import")
        head = words[1:split]
        level = 0
        while head and set(head[0]) == {"."}:  # "." and "..." tokens
            level += len(head.pop(0))
        names = [g[0] for g in _split([w for w in words[split + 1 :] if w not in "()"], ",") if g]
        yield ImportRef("".join(head), tuple(names), level)
    elif words[0] == "pytest_plugins" and "=" in words:
        for kind, text in statement[words.index("=") + 1 :]:
            if kind == tokenize.STRING:
                with contextlib.suppress(ValueError, SyntaxError):
                    for name in _plugin_names(str(ast.literal_eval(text))):
                        yield ImportRef(name)
    for i, (_kind, text) in enumerate(statement[:-2]):
        # importlib.import_module("x") / __import__("x") with a literal, absolute name
        if text in ("import_module", "__import__") and statement[i + 1][1] == "(":
            literal_kind, literal = statement[i + 2]
            if literal_kind == tokenize.STRING:
                with contextlib.suppress(ValueError, SyntaxError):
                    name = ast.literal_eval(literal)
                    if isinstance(name, str) and name and not name.startswith("."):
                        yield ImportRef(name)


def _split(words: list[str], separator: str) -> Iterator[list[str]]:
    group: list[str] = []
    for word in words:
        if word == separator:
            yield group
            group = []
        else:
            group.append(word)
    yield group


_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([\w.][\w. \t,]*)", re.MULTILINE)
_FROM_RE = re.compile(
    r"^[ \t]*from[ \t]*(\.*)[ \t]*([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|(?:[^\n\\]|\\\n)+)",
    re.MULTILINE,
)


def _scan_lines(text: str) -> Iterator[ImportRef]:
    """Last resort when the source cannot even be tokenized."""
    text = re.sub(r"#[^\n]*", "", text).replace(";", "\n")
    for match in _IMPORT_RE.finditer(text):
        for item in match.group(1).split(","):
            module = item.split(" as ")[0].strip()
            if module:
                yield ImportRef(module)
    for match in _FROM_RE.finditer(text):
        dots, module, names_blob = match.groups()
        names_blob = names_blob.strip().strip("()").replace("\\\n", " ")
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

    pytest's prepend mode also puts the import root of every ``conftest.py`` on
    ``sys.path``, so directories holding a conftest above the importer are roots too.
    """

    def __init__(
        self,
        files: Iterable[str],
        source_roots: Sequence[str] = ("", "src"),
        *,
        doctests: bool = False,
    ) -> None:
        """``doctests``: also follow imports in docstring examples (``--doctest-modules``)."""
        self._files = frozenset(files)
        self._doctests = doctests
        self._roots = tuple(dict.fromkeys(_normalise_root(r) for r in source_roots))
        self._packages = frozenset(
            _parent(f) for f in self._files if f.rpartition("/")[2] == "__init__.py"
        )
        self._conftest_dirs = frozenset(
            _parent(f) for f in self._files if f.rpartition("/")[2] == "conftest.py"
        )
        # package directory -> its direct submodules, for `from pkg import *`
        self._children: dict[str, list[str]] = {}
        for f in sorted(self._files):
            directory, _, name = f.rpartition("/")
            if name == "__init__.py":
                if directory:
                    self._children.setdefault(_parent(directory), []).append(f)
            elif name.endswith(".py"):
                self._children.setdefault(directory, []).append(f)
        # The same imports recur across many files: memoise per (import, root) and per
        # directory. This keeps resolving a large repository fast.
        self._absolute_memo: dict[tuple[ImportRef, str], tuple[str, ...]] = {}
        self._roots_memo: dict[str, tuple[str, ...]] = {}

    def resolve(self, importer: str, refs: Iterable[ImportRef]) -> set[str]:
        """Return the repository files that ``importer`` depends on via ``refs``."""
        roots = self._roots_for(_parent(importer))
        found: set[str] = set()
        for ref in refs:
            if ref.doctest and not self._doctests:
                continue
            if ref.level:
                found.update(self._resolve_relative(importer, ref))
                continue
            for root in roots:
                key = (ref, root)
                hits = self._absolute_memo.get(key)
                if hits is None:
                    hits = self._absolute_memo[key] = tuple(self._resolve_absolute(ref, root))
                found.update(hits)
        found.discard(importer)
        return found

    def _roots_for(self, directory: str) -> tuple[str, ...]:
        """Source roots, the directory's own import root, and those of conftests above."""
        roots = self._roots_memo.get(directory)
        if roots is None:
            dynamic = [self._import_root(directory)]
            current = directory
            while True:
                if current in self._conftest_dirs:
                    dynamic.append(self._import_root(current))
                if not current:
                    break
                current = _parent(current)
            roots = self._roots_memo[directory] = tuple(dict.fromkeys((*self._roots, *dynamic)))
        return roots

    def _import_root(self, directory: str) -> str:
        """The first directory, from ``directory`` upwards, that is not a package."""
        while directory and directory in self._packages:
            directory = _parent(directory)
        return directory

    def _module_file(self, path: str) -> str | None:
        # A package wins over a module of the same name, as in Python's own finder.
        for candidate in (_join(path, "__init__.py"), f"{path}.py"):
            if candidate in self._files:
                return candidate
        return None

    def _star(self, package_dir: str) -> list[str]:
        # `from pkg import *` imports whatever pkg.__all__ lists, which can be submodules.
        return self._children.get(package_dir, []) if package_dir in self._packages else []

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
            if name == "*":
                yield from self._star(_join(root, *parts))
                continue
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
            if name == "*":
                yield from self._star(_join(package, *parts))
                continue
            found = self._module_file(_join(package, *parts, name))
            if found:
                yield found
