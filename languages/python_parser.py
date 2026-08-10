import ast
from pathlib import Path
import subprocess


def get_all_python_files(root_dir: str = ".") -> list[str]:
    """
    Step 1 — Poori repo mein saari .py files dhundho.
    Tries `git ls-files` first for fast & gitignore-aware search;
    falls back to `pathlib.Path.rglob("*.py")` if git is unavailable.
    Returns normalized relative paths with forward slashes (e.g. 'core/git_diff.py').
    """
    files: list[str] = []

    try:
        result = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        files = [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError, Exception):
        pass

    if not files:
        root = Path(root_dir)
        files = [
            p.relative_to(root).as_posix()
            for p in root.rglob("*.py")
            if "__pycache__" not in p.parts
            and ".venv" not in p.parts
            and "venv" not in p.parts
        ]

    return sorted(files)


def extract_internal_imports(
    file_path: str | Path,
    all_repo_files: set[str],
    root_dir: str = "."
) -> set[str]:
    """
    Step 2 — Read file and ast.parse()
    Step 3 — Extract import and from X import Y nodes
    Step 4 — Resolve import module to actual relative file path in repo ("from core.git_diff ..." -> "core/git_diff.py")
    Step 5 — Keep ONLY internal repo files, ignore external libraries (subprocess, pytest, etc.)
    """
    path = Path(file_path)
    if not path.is_file():
        return set()

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()

    # Relative path of current file normalized with forward slashes
    try:
        rel_file = path.relative_to(Path(root_dir).resolve()).as_posix()
    except ValueError:
        rel_file = path.as_posix().replace("\\", "/")

    file_dir = Path(rel_file).parent

    internal_imports: set[str] = set()

    def resolve_candidate(module_str: str) -> str | None:
        """
        Helper to check if a module string maps to an existing file in all_repo_files.
        e.g. "core.git_diff" -> "core/git_diff.py" or "core/git_diff/__init__.py"
        """
        path_str = module_str.replace(".", "/")
        cand1 = f"{path_str}.py"
        cand2 = f"{path_str}/__init__.py"

        if cand1 in all_repo_files and cand1 != rel_file:
            return cand1
        if cand2 in all_repo_files and cand2 != rel_file:
            return cand2
        return None

    for node in ast.walk(tree):
        # Handle `import X`, `import X.Y`
        if isinstance(node, ast.Import):
            for alias in node.names:
                matched = resolve_candidate(alias.name)
                if matched:
                    internal_imports.add(matched)
                else:
                    parts = alias.name.split(".")
                    for i in range(len(parts), 0, -1):
                        sub = ".".join(parts[:i])
                        matched = resolve_candidate(sub)
                        if matched:
                            internal_imports.add(matched)
                            break

        # Handle `from X import Y`, `from .X import Y`
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                # Absolute import: e.g. from core.git_diff import get_changed_files
                if node.module:
                    matched = resolve_candidate(node.module)
                    if matched:
                        internal_imports.add(matched)
                    else:
                        for alias in node.names:
                            full_mod = f"{node.module}.{alias.name}"
                            matched_alias = resolve_candidate(full_mod)
                            if matched_alias:
                                internal_imports.add(matched_alias)
            else:
                # Relative import: e.g. level=1 for `from .git_diff import ...`
                base_parts = list(file_dir.parts) if file_dir != Path(".") else []
                pop_count = node.level - 1
                if pop_count <= len(base_parts):
                    if pop_count > 0:
                        rel_base = Path(*base_parts[:-pop_count])
                    else:
                        rel_base = file_dir if file_dir != Path(".") else Path("")

                    module_parts = node.module.split(".") if node.module else []

                    mod_path = (rel_base / Path(*module_parts)).as_posix() if (rel_base / Path(*module_parts)) != Path(".") else ""
                    if mod_path:
                        matched = resolve_candidate(mod_path.replace("/", "."))
                        if matched:
                            internal_imports.add(matched)

                    for alias in node.names:
                        alias_path = (rel_base / Path(*module_parts) / alias.name).as_posix()
                        matched_alias = resolve_candidate(alias_path.replace("/", "."))
                        if matched_alias:
                            internal_imports.add(matched_alias)

    return internal_imports


def build_dependency_map(root_dir: str = ".") -> dict[str, set[str]]:
    """
    Scans all Python files in the repository and returns a mapping from each
    file path to its set of internal imported file paths.
    """
    all_files = get_all_python_files(root_dir)
    all_files_set = set(all_files)

    dep_map: dict[str, set[str]] = {}
    for file_path in all_files:
        internal_deps = extract_internal_imports(file_path, all_files_set, root_dir)
        dep_map[file_path] = internal_deps

    return dep_map


if __name__ == "__main__":
    print("=== Step 1: Finding all .py files in repo ===")
    py_files = get_all_python_files()
    print(f"Total .py files found: {len(py_files)}")
    for f in py_files:
        print(f" - {f}")

    print("\n=== Steps 2-5: Parsing AST & Extracting Internal File Dependencies ===")
    dep_map = build_dependency_map()
    for file_path, deps in dep_map.items():
        deps_list = sorted(deps)
        print(f"\nFile: {file_path}")
        if deps_list:
            print(f"  Internal Imports -> {deps_list}")
        else:
            print("  Internal Imports -> None (No internal files imported)")
