## Saving the Graph in the json format So storing that into a Json file


import json 
import os
import hashlib
from typing import Optional,Dict

CACHE_FILE = ".karma_cache.json"

def compute_hash(file_paths : list) -> str:
    h = hashlib.sha256()

    for path in sorted(file_paths):
        if os.path.exists(path):
            with open(path,"rb") as f:
                h.update(f.read())

    return h.hexdigest()

def save_cache(graph: Dict, file_paths: list):
    data = {
        "hash": compute_hash(file_paths),
        "graph": graph
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[Karma] Cache saved to {CACHE_FILE}")



def load_cache(file_paths: list) -> Optional[Dict]:
    if not os.path.exists(CACHE_FILE):
        return None  # Nothing here bud

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, KeyError):
        print("[Karma] Cache corrupted, rebuilding...")
        return None

    current_hash = compute_hash(file_paths)
    if data.get("hash") != current_hash:
        print("[Karma] Cache stale, rebuilding...")
        return None  # We got some changes here  so rebuilding the cache 

    print("[Karma] Cache hit! Skipping AST rebuild.")
    return data.get("graph")







    





