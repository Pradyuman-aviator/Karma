from pathlib import Path
import sys

# Ensure repository root is in sys.path when script is executed directly
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from languages.python_parser import build_dependency_map


def build_forward_graph(root_dir: str = ".") -> dict[str, set[str]]:
    """
    Builds forward dependency graph mapping each file to the set of internal files it imports.
    e.g. "tests/test_git_diff.py" -> {"core/git_diff.py"}
    """
    return build_dependency_map(root_dir=root_dir)


def build_reverse_graph(dep_map: dict[str, set[str]]) -> dict[str, set[str]]:
    """
    Converts a forward dependency map (file -> files it imports)
    into a reverse dependency graph (file -> files that depend on / import it).

    Forward: "tests/test_git_diff.py" -> {"core/git_diff.py"}
    Reverse: "core/git_diff.py"       -> {"tests/test_git_diff.py"}
    """
    reverse_graph: dict[str, set[str]] = {}

    for file_path, imported_files in dep_map.items():
        if file_path not in reverse_graph:
            reverse_graph[file_path] = set()

        for imported in imported_files:
            if imported not in reverse_graph:
                reverse_graph[imported] = set()
            reverse_graph[imported].add(file_path)

    return reverse_graph


if __name__ == "__main__":
    forward = build_forward_graph(".")
    reverse = build_reverse_graph(forward)

    print("=== Forward Dependency Graph ===")
    for src, dsts in forward.items():
        print(f"  {src} -> {dsts}")

    print("\n=== Reverse Dependency Graph (Impact Graph) ===")
    for src, dsts in reverse.items():
        print(f"  {src} -> {dsts}")
