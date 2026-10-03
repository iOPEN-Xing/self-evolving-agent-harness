"""真实 SQLite 文件上的故障注入；适配器只模拟 SessionDB 的公开导入/导出契约。"""
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from conftest import load_module


class SQLiteSessions:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT)")

    def import_sessions(self, rows):
        self.conn.executemany("INSERT INTO sessions VALUES (?, ?)",
                              [(r["id"], json.dumps(r)) for r in rows])
        self.conn.commit()
        return {"ok": True, "imported": len(rows), "skipped": 0, "errors": []}

    def export_all(self):
        return [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM sessions ORDER BY id")]

    def search_sessions(self, limit):
        return self.export_all()[:limit]

    def get_messages(self, sid):
        return next(r["messages"] for r in self.export_all() if r["id"] == sid)

    def close(self):
        self.conn.close()


@pytest.fixture
def snapshot(monkeypatch):
    # 兼容旧实现的顶层依赖，以便先证明修复前会破坏原库。
    monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=SQLiteSessions))
    return load_module("snapshot_under_test", "examples/23-final-assembly/versioning/snapshot.py")


def seed(path, sid="live"):
    db = SQLiteSessions(path)
    db.import_sessions([{"id": sid, "messages": [{"role": "user", "content": sid}]}])
    return db


def test_corrupt_snapshot_never_deletes_live_database(snapshot, tmp_path):
    live = tmp_path / "state.db"
    seed(live).close()
    before = live.read_bytes()
    saved = tmp_path / "snapshots/v0"
    saved.mkdir(parents=True)
    (saved / "state_snapshot.json").write_text("not-json")
    with pytest.raises((ValueError, FileNotFoundError)):
        snapshot.restore_snapshot(live, "v0", tmp_path / "snapshots")
    assert live.read_bytes() == before


def test_round_trip_and_immutable_snapshot(snapshot, tmp_path):
    source = seed(tmp_path / "source.db", "saved")
    root = tmp_path / "snapshots"
    snapshot.take_snapshot(source, "v0", "skill v0", root)
    with pytest.raises(FileExistsError):
        snapshot.take_snapshot(source, "v0", "replacement", root)
    source.close()
    live = tmp_path / "state.db"
    seed(live).close()
    result = snapshot.restore_snapshot(live, "v0", root)
    assert (result["sessions"], result["messages"]) == (1, 1)
    db = SQLiteSessions(live)
    assert db.export_all()[0]["id"] == "saved"
    db.close()
    assert snapshot.restore_skill("v0", root) == "skill v0"


@pytest.mark.parametrize("label", ["../escape", "absolute", "skill_snapshots", "", "a/b"])
def test_labels_cannot_escape_snapshot_root(snapshot, tmp_path, label):
    if label == "absolute":
        label = str(tmp_path / "outside")
    db = seed(tmp_path / "state.db")
    with pytest.raises(ValueError):
        snapshot.take_snapshot(db, label, "skill", tmp_path / "snapshots")
    db.close()


def test_missing_skill_is_an_error(snapshot, tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshot.restore_skill("v0", tmp_path)


def test_tampering_rejected_before_activation(snapshot, tmp_path):
    live = tmp_path / "state.db"
    db = seed(live)
    saved = snapshot.take_snapshot(db, "v0", "skill", tmp_path / "snapshots")
    db.close()
    before = live.read_bytes()
    path = tmp_path / "snapshots/v0/state_snapshot.json"
    path.write_text('[{"id":"tampered","messages":[]}]')
    with pytest.raises(ValueError):
        snapshot.restore_snapshot(live, "v0", tmp_path / "snapshots")
    assert live.read_bytes() == before
    assert saved["sessions"] == 1


def test_partial_import_and_exception_preserve_live(snapshot, tmp_path, monkeypatch):
    live = tmp_path / "state.db"
    db = seed(live)
    snapshot.take_snapshot(db, "v0", "skill", tmp_path / "snapshots")
    db.close()
    before = live.read_bytes()
    for failure in ("partial", "exception"):
        class FailingDB(SQLiteSessions):
            def import_sessions(self, rows):
                if failure == "exception":
                    raise RuntimeError("injected failure")
                return {"imported": 0, "skipped": 0, "errors": []}
        monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=FailingDB))
        with pytest.raises((ValueError, RuntimeError)):
            snapshot.restore_snapshot(live, "v0", tmp_path / "snapshots")
        assert live.read_bytes() == before
    assert not list(tmp_path.glob(".restore-*"))


def test_symlink_and_skill_tampering_rejected(snapshot, tmp_path):
    db = seed(tmp_path / "state.db")
    root = tmp_path / "snapshots"
    saved = snapshot.take_snapshot(db, "v0", "skill", root)
    db.close()
    skill = root / "v0/SKILL.md"
    skill.write_text("changed")
    with pytest.raises(ValueError):
        snapshot.restore_skill("v0", root)
    skill.unlink()
    skill.symlink_to(tmp_path / "state.db")
    with pytest.raises(ValueError):
        snapshot.restore_snapshot(tmp_path / "state.db", "v0", root)
    assert saved["messages"] == 1


def test_restore_into_wal_database(snapshot, tmp_path):
    source = seed(tmp_path / "source.db", "saved")
    snapshot.take_snapshot(source, "v0", "skill", tmp_path / "snapshots")
    source.close()
    live = tmp_path / "state.db"
    seed(live).close()
    reader = sqlite3.connect(live)
    assert reader.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    snapshot.restore_snapshot(live, "v0", tmp_path / "snapshots")
    assert reader.execute("SELECT id FROM sessions").fetchall() == [("saved",)]
    reader.close()


def test_same_message_count_with_changed_content_cannot_activate(snapshot, tmp_path, monkeypatch):
    live = tmp_path / "state.db"
    db = seed(live)
    snapshot.take_snapshot(db, "v0", "skill", tmp_path / "snapshots")
    db.close()
    before = live.read_bytes()
    class LossyDB(SQLiteSessions):
        def import_sessions(self, rows):
            rows[0]["messages"][0]["content"] = "lost original content"
            return super().import_sessions(rows)
    monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=LossyDB))
    with pytest.raises(ValueError):
        snapshot.restore_snapshot(live, "v0", tmp_path / "snapshots")
    assert live.read_bytes() == before
