"""采用前重新枚举源码与配置，既核对内容也核对文件集合。"""
import hashlib
import json
import os
from pathlib import Path

EXCLUDED = {"output", "__pycache__", "node_modules", "tests", "history"}


def fingerprint(repo, entrypoint, dependencies=()):
    repo = Path(repo).resolve()
    entrypoint = Path(entrypoint)
    roots = [repo / "examples/23-final-assembly", *map(Path, dependencies)]
    shared = repo / "harness_engineering"
    if shared.exists() and shared not in roots:
        roots.append(shared)
    files = {entrypoint}
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"源码根目录缺失或为符号链接: {root}")
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in EXCLUDED)
            if any((Path(directory) / d).is_symlink() for d in dirs):
                raise ValueError("源码目录不能使用符号链接")
            files.update(Path(directory) / name for name in names
                         if name.endswith(".py") or (root == roots[0] and name.endswith(".json")))
    hashes = {}
    for path in sorted(files):
        if path.is_symlink():
            raise ValueError(f"源码文件不能使用符号链接: {path}")
        path = path.resolve()
        key = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
        hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"schema": 2, "sha256": digest, "files": hashes,
            "entrypoint": str(entrypoint.resolve().relative_to(repo)),
            "dependencies": [str(Path(p).resolve()) for p in dependencies]}


def unchanged(manifest, repo):
    """旧清单缺少枚举范围，不能证明没有新增源码，因此保守拒绝。"""
    try:
        if not isinstance(manifest, dict) or manifest.get("schema") != 2:
            return False
        current = fingerprint(repo, Path(repo) / manifest["entrypoint"], manifest["dependencies"])
        return current == manifest
    except (OSError, ValueError, KeyError, TypeError):
        return False
