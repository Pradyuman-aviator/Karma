import unittest
from karma.languages.python import extract_internal_imports


class TestPythonParser(unittest.TestCase):

    def test_extract_internal_imports(self):
        repo_files = {"karma/git.py", "karma/cli.py", "karma/languages/python.py"}
        imports = extract_internal_imports("karma/cli.py", repo_files)
        self.assertIn("karma/git.py", imports)


if __name__ == "__main__":
    unittest.main()
