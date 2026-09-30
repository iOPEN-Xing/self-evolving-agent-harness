"""记录本次运行实际使用的源码指纹；采用前再次核对，防止运行中改版。"""

import hashlib
import json
import os
from pathlib import Path


def fingerprint(repo, entrypoint, dependencies=()):
    repo = Path(repo).resolve()
    roots = [repo / "examples/23-final-assembly", *map(Path, dependencies)]
    files = {Path(entrypoint).resolve()}
    for root in roots:
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in
                             {"output", "__pycache__", "node_modules", "tests", "history"})
            files.update(Path(directory) / name for name in names if (name.endswith(".py") or (root == roots[0] and name.endswith(".json"))))
    hashes = {}
    for path in sorted(files):
        key = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
        hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": hashes, "entrypoint": str(Path(entrypoint).relative_to(repo))}


def unchanged(manifest, repo):
    for name, digest in manifest["files"].items():
        path = Path(repo) / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False
    return True
