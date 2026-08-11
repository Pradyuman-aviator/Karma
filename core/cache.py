import json
import os
import hashlib
from typing import Optional, Dict, Any, List


CACHE_FILE = ".karma_cache.json"


def _cache_path(repo: str = ".") -> str:
    return os.path.join(repo, CACHE_FILE)


def compute_hash(file_paths: list, repo: str = ".") -> str:
    h = hashlib.sha256()

    for path in sorted(file_paths):
        full = path if os.path.isabs(path) else os.path.join(repo, path)
        if os.path.exists(full):
            with open(full, "rb") as f:
                h.update(f.read())

    return h.hexdigest()


def _serialize_graph(graph: Dict[str, Any]) -> Dict[str, List[str]]:
    return {key: sorted(list(value)) for key, value in graph.items()}


def _deserialize_graph(graph: Dict[str, Any]) -> Dict[str, set]:
    return {key: set(value) for key, value in graph.items()}


def save_cache(graph: Dict, file_paths: list, repo: str = ".") -> None:
    data = {
        "hash": compute_hash(file_paths, repo=repo),
        "graph": _serialize_graph(graph),
    }
    path = _cache_path(repo)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[Karma] Cache saved to {path}")


def load_cache(file_paths: list, repo: str = ".") -> Optional[Dict]:
    path = _cache_path(repo)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, KeyError, OSError):
        print("[Karma] Cache corrupted, rebuilding...")
        return None

    current_hash = compute_hash(file_paths, repo=repo)
    if data.get("hash") != current_hash:
        print("[Karma] Cache stale, rebuilding...")
        return None

    print("[Karma] Cache hit! Skipping AST rebuild.")
    return _deserialize_graph(data.get("graph") or {})
