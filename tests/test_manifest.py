import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from versioning import manifest


def record(path, content, decision="ADOPT"):
    return manifest.record_version(path, "skill", content, decision, "eval.json", "unit test")


def test_chain_follows_actual_rollback_content(tmp_path):
    path = tmp_path / "versions.json"
    record(path, "v0")
    record(path, "v1")
    rollback = record(path, "v0", "ROLLBACK")
    following = record(path, "v2")
    assert following["prev_hash"] == rollback["skill_hash"]
    assert following["prev_sha256"] == rollback["adopted_sha256"]


def test_invalid_decision_preserves_manifest(tmp_path):
    path = tmp_path / "versions.json"
    record(path, "v0")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        record(path, "v1", "UNKNOWN")
    assert path.read_bytes() == before


def test_failed_atomic_replace_preserves_previous_json(tmp_path, monkeypatch):
    path = tmp_path / "versions.json"
    record(path, "v0")
    before = path.read_bytes()
    def fail(*args):
        raise OSError("injected disk failure")
    monkeypatch.setattr("os.replace", fail)
    with pytest.raises(OSError):
        record(path, "v1")
    assert path.read_bytes() == before
    assert len(json.loads(path.read_text())["versions"]) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_appends_do_not_lose_versions(tmp_path):
    path = tmp_path / "versions.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: record(path, str(i)), range(12)))
    versions = manifest.load_manifest(path)["versions"]
    assert [r["version"] for r in versions] == [f"v{i}" for i in range(1, 13)]
    assert len({r["skill_hash"] for r in versions}) == 12
    assert all(r["adopted_at"].endswith("+00:00") for r in versions)
