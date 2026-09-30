"""版本 manifest：记录每个被采用的 skill 版本，含内容 hash 与评测报告引用。

manifest 是一个 JSON 文件（versions.json），按时间顺序追加。
每个版本记录：
  - version: 语义化版本号（v0/v1/...）
  - skill_name: skill 名
  - skill_hash: SKILL.md 内容的 sha256（前 12 位），用于快速判定版本是否变化
  - adopted_at: 采用时间（ISO8601）
  - decision: ADOPT / ROLLBACK
  - eval_report: 对应评测报告文件路径
  - prev_hash: 上一个被采用版本的 hash（回退链）
  - notes: 采用/回退理由
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional


def hash_skill(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"skill_name": "oncall-service-investigation", "versions": []}


def save_manifest(manifest_path: Path, manifest: Dict[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def latest_adopted(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """返回最近一个 decision==ADOPT 的版本。"""
    for v in reversed(manifest["versions"]):
        if v.get("decision") == "ADOPT":
            return v
    return None


def record_version(manifest_path: Path, skill_name: str, skill_content: str,
                   decision: str, eval_report: str, notes: str, *, candidate_content=None, adopted_label=None) -> Dict[str, Any]:
    """追加一条版本记录。decision ∈ {ADOPT, ROLLBACK}。"""
    manifest = load_manifest(manifest_path)
    manifest["skill_name"] = skill_name
    prev = latest_adopted(manifest)
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
        "adopted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "decision": decision,
        "eval_report": eval_report,
        "prev_hash": prev_hash,
        "notes": notes,
    }
    manifest["versions"].append(entry)
    save_manifest(manifest_path, manifest)
    return entry
