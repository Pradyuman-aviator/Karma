import unittest
from unittest.mock import patch, MagicMock
from karma.git import get_changed_files
from karma.languages.python import extract_internal_imports, get_all_python_files


class TestGitDiffParser(unittest.TestCase):
    @patch("subprocess.run")
    def test_get_changed_files_filtering(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="core/git_diff.py\ncore/__pycache__/git_diff.cpython-312.pyc\nReadme.MD\n",
            returncode=0
        )
        files = get_changed_files("main", "HEAD")
        self.assertIn("core/git_diff.py", files)
        self.assertIn("Readme.MD", files)
        self.assertNotIn("core/__pycache__/git_diff.cpython-312.pyc", files)

    def test_extract_internal_imports(self):
        repo_files = {"karma/git.py", "karma/cli.py", "karma/languages/python.py"}
        imports = extract_internal_imports("karma/cli.py", repo_files)
        self.assertIn("karma/git.py", imports)


if __name__ == "__main__":
    unittest.main()
