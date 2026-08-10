import subprocess


def get_changed_files(base: str = "main", head: str = "HEAD") -> list[str]:
    """
    Input:  base = "main", head = "HEAD"
    Output: ["core/git_diff.py", "languages/python_parser.py"]
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base, head],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [f for f in files if "__pycache__" not in f and not f.endswith(".pyc")]
    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e.stderr}")
        return []
    except FileNotFoundError:
        print("Git not found on this system")
        return []
    except Exception as e:
        print(f"Error getting changed files: {e}")
        return []



## checking



def main():
    print(get_changed_files())


if __name__ == "__main__":
    main()
# test 
