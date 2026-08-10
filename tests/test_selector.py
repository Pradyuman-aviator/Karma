import unittest
from core.dep_graph import build_reverse_graph
from core.test_selector import get_affected_tests, is_test_file


class TestSelector(unittest.TestCase):
    def test_build_reverse_graph(self):
        dep_map = {
            "tests/test_git_diff.py": {"core/git_diff.py"},
            "core/git_diff.py": set(),
        }
        rev = build_reverse_graph(dep_map)
        self.assertEqual(rev["core/git_diff.py"], {"tests/test_git_diff.py"})

    def test_is_test_file(self):
        self.assertTrue(is_test_file("tests/test_git_diff.py"))
        self.assertTrue(is_test_file("tests/unit/test_selector.py"))
        self.assertFalse(is_test_file("core/test_selector.py"))
        self.assertFalse(is_test_file("core/git_diff.py"))

    def test_get_affected_tests_transitive(self):
        dep_map = {
            "core/git_diff.py": set(),
            "core/test_selector.py": {"core/git_diff.py"},
            "tests/test_selector.py": {"core/test_selector.py"},
            "tests/test_git_diff.py": {"core/git_diff.py"},
        }
        changed = ["core/git_diff.py"]
        affected = get_affected_tests(changed, dep_map)
        self.assertEqual(affected, ["tests/test_git_diff.py", "tests/test_selector.py"])

    def test_get_affected_tests_no_impact(self):
        dep_map = {
            "core/git_diff.py": set(),
            "tests/test_selector.py": set(),
        }
        changed = ["Readme.MD"]
        affected = get_affected_tests(changed, dep_map)
        self.assertEqual(affected, [])


if __name__ == "__main__":
    unittest.main()
