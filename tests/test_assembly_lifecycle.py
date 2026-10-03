"""模块化总装的现有安全机制回归：只修候选，整目录恢复，不调用模型。"""
import importlib
import json
import os

import pytest

from conftest import ROOT


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "examples/assembly"))
    config = importlib.import_module("assembly.config")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    skills = importlib.import_module("assembly.lifecycle.skills")
    snapshots = importlib.import_module("assembly.lifecycle.snapshots")
    contracts = importlib.import_module("assembly.contracts")
    return skills, snapshots, contracts


def test_candidate_preserves_full_tree_and_adopted_version(assembly):
    skills, _, contracts = assembly
    adopted = skills.init_adopted()
    support = adopted / "references/evidence.md"
    support.parent.mkdir()
    support.write_text("keep reference")
    before = contracts.tree_hashes(adopted)
    bundle = skills.create_candidate(adopted, "offline-test", [
        dict(action="patch", file_path="SKILL.md", old_string="只读查询和报告", new_string="严格只读查询和报告")])
    assert contracts.tree_hashes(adopted) == before
    from pathlib import Path
    candidate = Path(bundle.candidate_dir)
    assert (candidate / "references/evidence.md").read_text() == "keep reference"
    assert bundle.base_skill_hash != bundle.candidate_hash


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "a/../b", "a\\b"])
def test_candidate_paths_cannot_escape_and_failure_has_no_bundle(assembly, path):
    skills, _, contracts = assembly
    adopted = skills.init_adopted()
    before = contracts.tree_hashes(adopted)
    with pytest.raises(ValueError):
        skills.create_candidate(adopted, "offline-test", {path: "payload"})
    assert contracts.tree_hashes(adopted) == before
    # 实现明确保留失败副本诊断；调度器只能消费成功返回的 CandidateBundle。
    evidence = list((skills.config.OUTPUT_DIR / "lifecycle/candidates").glob('*.json'))
    assert len(evidence) == 1
    failure = json.loads(evidence[0].read_text())
    assert failure['failed'] is True and failure['adopted_unchanged'] is True
    assert 'candidate' not in failure


def test_ambiguous_patch_is_rejected_without_changing_adopted(assembly):
    skills, _, contracts = assembly
    adopted = skills.init_adopted()
    before = contracts.tree_hashes(adopted)
    with pytest.raises(ValueError):
        skills.create_candidate(adopted, "offline-test", [
            dict(action="patch", file_path="SKILL.md", old_string="支付", new_string="changed")])
    assert contracts.tree_hashes(adopted) == before


def test_full_directory_restore_removes_new_files(assembly):
    skills, snapshots, contracts = assembly
    adopted = skills.init_adopted()
    before = contracts.tree_hashes(adopted)
    saved = snapshots.snapshot(adopted, "v0")
    (adopted / "SKILL.md").write_text("changed")
    (adopted / "new-script.py").write_text("unreviewed")
    assert snapshots.restore(saved, adopted) == before
    assert not (adopted / "new-script.py").exists()
    with pytest.raises(FileExistsError):
        snapshots.snapshot(adopted, "v0")


def test_tampered_snapshot_preserves_current_version(assembly):
    skills, snapshots, contracts = assembly
    adopted = skills.init_adopted()
    saved = snapshots.snapshot(adopted, "v0")
    from pathlib import Path
    (Path(saved.snapshot_dir) / "SKILL.md").write_text("tampered")
    before = contracts.tree_hashes(adopted)
    with pytest.raises(ValueError):
        snapshots.restore(saved, adopted)
    assert contracts.tree_hashes(adopted) == before


def test_failed_switch_restores_previous_directory(assembly, monkeypatch):
    skills, snapshots, contracts = assembly
    adopted = skills.init_adopted()
    saved = snapshots.snapshot(adopted, "v0")
    (adopted / "SKILL.md").write_text("current version")
    before = contracts.tree_hashes(adopted)
    native_replace = os.replace
    def fail_stage(source, target):
        if source.name.startswith(".restore-stage-"):
            raise OSError("injected switch failure")
        return native_replace(source, target)
    monkeypatch.setattr(snapshots.os, "replace", fail_stage)
    with pytest.raises(OSError):
        snapshots.restore(saved, adopted)
    assert contracts.tree_hashes(adopted) == before


def test_payment_judge_selfcheck_and_capstone_route_contract(assembly, monkeypatch):
    judge = importlib.import_module("assembly.eval.judge")
    check = judge.judge_self_check()
    assert check.passed
    assert all(not row.passed for row in check.details if row.case_id.startswith("known_bad:"))
    # 综合实践扩展会安装全局评分器包装，另起进程避免污染其他测试。
    import subprocess
    import sys
    code = ("import sys; sys.path.insert(0,'examples/assembly'); "
            "sys.path.insert(0,'examples/capstone'); from payment_grader import verify; verify()")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
