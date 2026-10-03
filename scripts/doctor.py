#!/usr/bin/env python3
"""只读环境盘点；不联网、不加载个人 .env、不导入上游或调用模型。"""
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_value(path, *arguments):
    try:
        result = subprocess.run(["git", "-C", str(path), *arguments],
                                capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def inspect_environment():
    packages = []
    for line in (ROOT / "requirements-dev.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, required = line.split("==")
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        packages.append(dict(name=name, required=required, installed=installed, matched=installed == required))
    vendors = []
    for name in ("hermes-agent", "hermes-agent-self-evolution", "SkillClaw", "OpenViking"):
        path = ROOT / ".deps" / name
        toplevel = git_value(path, "rev-parse", "--show-toplevel")
        valid = toplevel is not None and Path(toplevel).resolve() == path.resolve()
        status = git_value(path, "status", "--porcelain") if valid else None
        vendors.append(dict(name=name, valid_checkout=valid,
            commit=git_value(path, "rev-parse", "HEAD") if valid else None,
            dirty=bool(status) if status is not None else None))
    return dict(python=sys.version.split()[0], python_compatible=sys.version_info[:2] == (3, 12),
                repository_commit=git_value(ROOT, "rev-parse", "HEAD"), packages=packages,
                offline_dependencies_ready=all(p["matched"] for p in packages), vendors=vendors,
                vendor_sources_present=all(v["valid_checkout"] for v in vendors),
                upstream_environment_verified=False, model_execution_verified=False,
                note="仅盘点文件与版本；真实模型、上游安装及沙箱验收须另行执行。")


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), ensure_ascii=False, indent=2))
