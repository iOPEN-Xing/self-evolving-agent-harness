"""快照恢复点：用 SessionDB.export_all() 做整库时间点存档。

每个被采用的版本对应一个恢复点：
  - snapshots/<version>.json  整库导出（会话+消息）
  - 同时把当时的 SKILL.md 内容存一份到 skill_snapshots/<version>.md
回退时：从快照恢复 state.db，从 skill_snapshots 恢复旧版 SKILL.md。
"""

import json
import shutil
from pathlib import Path
from typing import Any

# hermes_state 在 .deps/hermes-agent 下，由调用方把 sys.path 配好。
from hermes_state import SessionDB  # noqa: E402


def take_snapshot(db: SessionDB, version_label: str,
                  skill_content: str, snapshot_dir: Path) -> dict:
    """对当前 state.db 做整库快照，并保存当时的 skill 内容。"""
    snap_dir = snapshot_dir / version_label
    skill_snap_dir = snapshot_dir / "skill_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    skill_snap_dir.mkdir(parents=True, exist_ok=True)

    exported = db.export_all()
    snap_file = snap_dir / "state_snapshot.json"
    snap_file.write_text(
        json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    skill_file = skill_snap_dir / f"{version_label}.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    n_sessions = len(exported)
    n_msgs = sum(len(s.get("messages", [])) for s in exported)
    return {
        "version": version_label,
        "snapshot_file": str(snap_file),
        "skill_file": str(skill_file),
        "sessions": n_sessions,
        "messages": n_msgs,
    }


def restore_snapshot(db_path: Path, version_label: str, snapshot_dir: Path) -> dict:
    """清空 state.db 并从 <version>.json 恢复。返回恢复后的统计。"""
    snap_file = snapshot_dir / version_label / "state_snapshot.json"
    if not snap_file.exists():
        raise FileNotFoundError(f"快照不存在: {snap_file}")

    # 删库（含 WAL/SHM）
    db_path.unlink(missing_ok=True)
    for ext in ("-wal", "-shm"):
        p = db_path.parent / f"{db_path.name}{ext}"
        p.unlink(missing_ok=True)

    db = SessionDB(db_path=db_path)
    snapshot_data = json.loads(snap_file.read_text(encoding="utf-8"))
    result = db.import_sessions(snapshot_data)
    restored = db.search_sessions(limit=100000)
    n_msgs = sum(len(db.get_messages(s["id"])) for s in restored)
    db.close()
    return {
        "version": version_label,
        "import_result": result,
        "sessions": len(restored),
        "messages": n_msgs,
    }


def restore_skill(version_label: str, snapshot_dir: Path) -> str:
    """从 skill_snapshots 读回某版本的 SKILL.md 内容（用于回退 skill）。"""
    f = snapshot_dir / "skill_snapshots" / f"{version_label}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""
