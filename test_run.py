from core.git_diff import get_changed_files

files = get_changed_files("8abc019", "HEAD")
print(f"Changed files: {files}")