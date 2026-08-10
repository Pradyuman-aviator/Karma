from collections import deque
from pathlib import Path

from core.dep_graph import build_reverse_graph


def is_test_file(file_path: str) -> bool:
    """
    Helper to check if a file is a test file.
    Filters for files inside `tests/` directory or files starting with `test_` outside core/src modules.
    """
    path = Path(file_path)
    parts = [p.lower() for p in path.parts]
    name = path.name.lower()

    if not name.endswith(".py"):
        return False

    if "tests" in parts:
        return True

    # Exclude core application directories if not inside tests folder
    if parts and parts[0] in ("core", "languages", "src", "lib"):
        return False

    return name.startswith("test_") or name.endswith("_test.py")


def get_affected_tests(
    changed_files: list[str],
    dep_map: dict[str, set[str]]
) -> list[str]:
    """
    Function 2: Performs BFS using collections.deque starting from `changed_files`
    to find all directly and indirectly affected test files via the reverse graph.
    """
    reverse_graph = build_reverse_graph(dep_map)

    queue: deque[str] = deque(changed_files)
    visited: set[str] = set(changed_files)

    affected_tests: set[str] = set()

    while queue:
        current_file = queue.popleft()

        # If current_file is a test file, collect it
        if is_test_file(current_file):
            affected_tests.add(current_file)

        # Explore all files that depend on `current_file`
        dependents = reverse_graph.get(current_file, set())
        for dependent in dependents:
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)

    return sorted(affected_tests)


if __name__ == "__main__":
    # Test Demonstration
    sample_dep_map = {
        "tests/test_git_diff.py": {"core/git_diff.py"},
        "core/git_diff.py": set(),
        "tests/test_selector.py": {"core/test_selector.py"},
        "core/test_selector.py": {"core/git_diff.py"},
    }

    sample_changed = ["core/git_diff.py"]

    print("=== Function 1: Reverse Graph ===")
    rev = build_reverse_graph(sample_dep_map)
    for src, dsts in rev.items():
        print(f"  {src} -> {dsts}")

    print("\n=== Function 2: BFS Test Selection ===")
    affected = get_affected_tests(sample_changed, sample_dep_map)
    print(f"  Changed Files  : {sample_changed}")
    print(f"  Affected Tests : {affected}")
