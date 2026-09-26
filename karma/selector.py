from collections import deque
from pathlib import Path

from karma.graph import DependencyGraph


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


def get_affected_tests(changed_files: list[str], graph: DependencyGraph) -> list[str]:
    """
    Function 2: Performs BFS using collections.deque starting from `changed_files`
    to find all directly and indirectly affected test files via the reverse graph.
    """
    queue: deque[str] = deque(changed_files)
    visited: set[str] = set(changed_files)

    affected_tests: set[str] = set()

    while queue:
        current_file = queue.popleft()

        # If current_file is a test file, collect it
        if is_test_file(current_file):
            affected_tests.add(current_file)

        # Explore all files that depend on `current_file`
        dependents = graph.dependents(current_file)
        for dependent in dependents:
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)

    return sorted(affected_tests)
