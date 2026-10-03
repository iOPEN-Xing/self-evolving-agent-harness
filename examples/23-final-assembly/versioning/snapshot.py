"""不可覆盖的会话历史恢复点；先完整验证，再通过 SQLite 事务恢复。

调用方必须暂停写入、关闭 SessionDB 后恢复。这是课程演示的时间点恢复，
会删除恢复点之后的会话；生产中的 Skill 回退应只回退技能文件，保留历史。
SessionDB 导入只恢复会话/消息，不恢复队列、进程或 Gateway 的运行所有权。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path


def _directory(root, label):
    if (not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", label)
            or label == "skill_snapshots"):
        raise ValueError("快照标签必须是单个安全文件名，不能使用保留名称")
    root = Path(root)
    path = root / label
    if root.is_symlink() or path.is_symlink():
        raise ValueError("快照目录不能是符号链接")
    return path


def _session_counts(rows):
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"].strip()
        or not isinstance(r.get("messages"), list)
        or any(not isinstance(m, dict) for m in r["messages"]) for r in rows
    ):
        raise ValueError("快照必须包含有编号的会话和消息列表")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("快照会话编号重复")
    return {r["id"]: len(r["messages"]) for r in rows}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _messages(rows):
    # 数据库可能重新分配 message.id；核对持久化的消息语义而非自增主键。
    fields = ('role', 'content', 'tool_calls', 'tool_call_id', 'tool_name')
    return json.dumps({r['id']: [{field: m.get(field) for field in fields} for m in r['messages']] for r in rows},
                      sort_keys=True, ensure_ascii=False)


def _read_snapshot(root, label):
    path = _directory(root, label)
    names = ("manifest.json", "state_snapshot.json", "SKILL.md")
    if any((path / name).is_symlink() for name in names):
        raise ValueError("快照文件不能是符号链接")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1
            or manifest.get("version") != label):
        raise ValueError("快照清单格式或版本不匹配")
    state, skill = [(path / name).read_bytes() for name in names[1:]]
    if manifest.get("files") != {"state_snapshot.json": _digest(state), "SKILL.md": _digest(skill)}:
        raise ValueError("快照内容哈希不匹配")
    rows = json.loads(state)
    _session_counts(rows)
    return rows, skill.decode("utf-8")


def take_snapshot(db, version_label: str, skill_content: str, snapshot_dir: Path) -> dict:
    """在临时目录写完状态、技能及 SHA-256 清单后发布；同标签不可重写。"""
    destination = _directory(snapshot_dir, version_label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    exported = db.export_all()
    counts = _session_counts(exported)
    state = json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8")
    skill = skill_content.encode("utf-8")
    manifest = {"schema": 1, "version": version_label,
                "files": {"state_snapshot.json": _digest(state), "SKILL.md": _digest(skill)}}
    stage = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination.parent))
    try:
        for name, data in (("state_snapshot.json", state), ("SKILL.md", skill),
                           ("manifest.json", json.dumps(manifest).encode("utf-8"))):
            with (stage / name).open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # 单写入方协议；并发发布到同标签时，非空目录的 rename 会失败。
        stage.rename(destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"version": version_label, "snapshot_file": str(destination / "state_snapshot.json"),
            "skill_file": str(destination / "SKILL.md"), "sessions": len(counts),
            "messages": sum(counts.values())}


def restore_snapshot(db_path: Path, version_label: str, snapshot_dir: Path) -> dict:
    """导入隔离 SQLite 数据库并核对数量；失败不触碰现有库或 WAL/SHM。"""
    rows, _ = _read_snapshot(snapshot_dir, version_label)
    expected = _session_counts(rows)
    expected_messages = _messages(rows)
    db_path = Path(db_path)
    if db_path.is_symlink():
        raise ValueError("恢复目标不能是符号链接")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 延迟导入允许在没有整套 Hermes/模型依赖时验证恢复算法。
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory(prefix=".restore-", dir=db_path.parent) as temp:
        staged_path = Path(temp) / "state.db"
        db = SessionDB(db_path=staged_path)
        try:
            result = db.import_sessions(rows)
            if (not isinstance(result, dict) or result.get("ok") is False or result.get("errors")
                    or result.get("imported") != len(rows) or result.get("skipped", 0) != 0):
                raise ValueError("会话导入失败或不完整，保留原数据库")
            restored = db.export_all()
            if _session_counts(restored) != expected or _messages(restored) != expected_messages:
                raise ValueError("恢复后的会话编号、消息数量或内容不一致")
        finally:
            db.close()
        source = sqlite3.connect(staged_path)
        try:
            if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("恢复数据库完整性检查失败")
            target = sqlite3.connect(db_path, timeout=5)
            try:
                deadline = time.monotonic() + 5

                def progress(status, remaining, total):
                    # SQLITE_DONE 回调发生在提交之后，不能此时再谎报恢复失败。
                    if status != sqlite3.SQLITE_DONE and time.monotonic() > deadline:
                        raise TimeoutError("恢复事务超时；请确认所有数据库写入方已停止")

                # backup 在目标库中使用事务，兼容 WAL；不删除主库/侧文件。
                source.backup(target, pages=128, progress=progress, sleep=0.05)
            finally:
                target.close()
        finally:
            source.close()
    return {"version": version_label, "import_result": result,
            "sessions": len(expected), "messages": sum(expected.values())}


def restore_skill(version_label: str, snapshot_dir: Path) -> str:
    """返回经过完整清单核验的技能；丢失文件或旧格式均显式失败。"""
    _, skill = _read_snapshot(snapshot_dir, version_label)
    return skill
