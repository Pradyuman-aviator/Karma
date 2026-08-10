import unittest
from unittest.mock import patch, MagicMock
from core.git_diff import get_changed_files
from languages.python_parser import extract_internal_imports, get_all_python_files


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
        repo_files = {"core/git_diff.py", "cli.py", "languages/python_parser.py"}
        imports = extract_internal_imports("cli.py", repo_files)
        self.assertIn("core/git_diff.py", imports)


if __name__ == "__main__":
    unittest.main()
