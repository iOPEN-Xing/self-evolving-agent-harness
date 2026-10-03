"""版本 manifest：记录每个被采用的 skill 版本，含内容 hash 与评测报告引用。

manifest 是一个 JSON 文件（versions.json），按时间顺序追加。
每个版本记录：
  - version: 语义化版本号（v0/v1/...）
  - skill_name: skill 名
  - skill_hash: SKILL.md 内容的 sha256（前 12 位），用于快速判定版本是否变化
  - adopted_at: 采用时间（ISO8601）
  - decision: ADOPT / ROLLBACK / REJECT
  - eval_report: 对应评测报告文件路径
  - prev_hash: 上一个被采用版本的 hash（回退链）
  - notes: 采用/回退理由
"""

import json
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional


def hash_skill(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"skill_name": "oncall-service-investigation", "versions": []}


def save_manifest(manifest_path: Path, manifest: Dict[str, Any]) -> None:
    """同目录临时文件 + 原子替换，异常不会留下半份 JSON。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(suffix=".tmp", dir=manifest_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, manifest_path)
    finally:
        Path(name).unlink(missing_ok=True)


def latest_adopted(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """返回最近一个 decision==ADOPT 的版本。"""
    for v in reversed(manifest["versions"]):
        if v.get("decision") == "ADOPT":
            return v
    return None


def record_version(manifest_path: Path, skill_name: str, skill_content: str,
                   decision: str, eval_report: str, notes: str, *, candidate_content=None, adopted_label=None) -> Dict[str, Any]:
    """追加实际保留内容；POSIX 文件锁覆盖读取、编号、写入整个事务。"""
    if decision not in {"ADOPT", "ROLLBACK", "REJECT"}:
        raise ValueError("非法版本决策")
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    with manifest_path.with_suffix(manifest_path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _record_version(manifest_path, skill_name, skill_content, decision, eval_report,
                                   notes, candidate_content, adopted_label)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _record_version(manifest_path, skill_name, skill_content, decision, eval_report,
                    notes, candidate_content, adopted_label):
    manifest = load_manifest(manifest_path)
    manifest["skill_name"] = skill_name
    # ROLLBACK/REJECT 也记录了实际保留内容，不能沿最近 ADOPT 跳过回退。
    prev = manifest["versions"][-1] if manifest["versions"] else None
    prev_hash = prev["skill_hash"] if prev else None

    n = len(manifest["versions"]) + 1
    version_id = f"v{n}"
    entry = {
        "version": version_id,
        "skill_name": skill_name,
        "skill_hash": hash_skill(skill_content),
        "adopted_sha256": hashlib.sha256(skill_content.encode()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate_content.encode()).hexdigest() if candidate_content else None,
        "adopted_label": adopted_label,
        "adopted_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "eval_report": eval_report,
        "prev_hash": prev_hash,
        "prev_sha256": prev.get("adopted_sha256") if prev else None,
        "notes": notes,
    }
    manifest["versions"].append(entry)
    save_manifest(manifest_path, manifest)
    return entry
