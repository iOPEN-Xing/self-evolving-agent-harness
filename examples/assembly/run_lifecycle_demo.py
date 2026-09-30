#!/usr/bin/env python3
"""真实验收技能候选、Hermes Curator、快照恢复和 GEPA。

在仓库根目录运行（先由 shell 加载 ~/.hermes/.env）：
  PYTHONDONTWRITEBYTECODE=1 .deps/hermes-agent/.venv/bin/python examples/assembly/run_lifecycle_demo.py
默认重新调用真实模型；--resume 核查源码和候选哈希后复用已完成记录。
候选输入校验增强须有绑定具体代码版本与结果文件的独立兼容复核。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

sys.dont_write_bytecode = True

from assembly import config
from assembly.contracts import CandidateBundle, Event, tree_hashes, write_json
from assembly.lifecycle.skills import create_candidate, init_adopted, run_curator
from assembly.lifecycle.snapshots import restore, snapshot
from assembly.gepa import gepa_search

EVIDENCE = config.OUTPUT_DIR / "lifecycle"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for name in ("GLM_API_KEY", "BIGMODEL_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            message = message.replace(secret, "[密钥已隐藏]")
    return message


def _bundle_check(bundle: CandidateBundle) -> dict[str, Any]:
    path = Path(bundle.candidate_dir)
    assert path.parent.resolve() == (config.OUTPUT_DIR / "candidates").resolve()
    assert not path.is_symlink()
    assert path.name == bundle.candidate_id
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) == 3 and not parts[0] and parts[2].strip()
    header = yaml.safe_load(parts[1])
    assert isinstance(header, dict) and header.get("name") == bundle.skill_name
    assert isinstance(header.get("description"), str) and header["description"].strip()
    assert _sha(path / "SKILL.md") == bundle.candidate_hash
    manifest = tree_hashes(path)
    metadata = _load(EVIDENCE / "candidates" / f"{bundle.candidate_id}.json")
    # 完整树与契约中主技能哈希分别检查，支持脚本和参考文件随版本变化。
    expected = metadata["candidate_manifest"]
    assert manifest == expected
    assert metadata["candidate"] == bundle.to_dict()
    assert metadata["base_manifest"]["SKILL.md"] == bundle.base_skill_hash
    return {"candidate_id": bundle.candidate_id, "origin": bundle.origin,
            "candidate_dir": str(path), "skill_hash": bundle.candidate_hash,
            "manifest": manifest, "ready_for_eval": True}


def _resume_record(name: str, adopted_manifest: dict[str, str]) -> dict[str, Any] | None:
    path = EVIDENCE / f"{name}_result.json"
    if not path.exists():
        return None
    record = _load(path)
    fingerprints = record.get("source_sha256", {})
    aliases = {"gepa.py": config.ASSEMBLY_DIR / "assembly/gepa.py",
               "skills.py": config.ASSEMBLY_DIR / "assembly/lifecycle/skills.py",
               "curator.py": config.HERMES_SRC / "agent/curator.py"}
    def matched(name: str, digest: str) -> bool:
        source = aliases.get(name, Path(name))
        if not source.is_file():
            return False
        current = _sha(source)
        if current == digest:
            return True
        # 搜索代码不变、候选输出经逐文件复核时，可复用新增输入校验前的实跑。
        # 原始来源指纹始终保留；只接受本条记录和确切源版本的独立复核记录。
        review_path = EVIDENCE / "evidence_revalidation.json"
        if name == "skills.py" and review_path.exists():
            review = _load(review_path)
            return (review.get("artifact_sha256") == _sha(path)
                    and review.get("executed_skills_sha256") == digest
                    and review.get("reviewed_skills_sha256") == current
                    and review.get("passed") is True)
        return False
    if not fingerprints or not all(matched(p, h) for p, h in fingerprints.items()):
        return None
    recorded_base = record.get("adopted_manifest_before", record.get("adopted_before", record.get("adopted_before_manifest")))
    if recorded_base != adopted_manifest:
        return None
    raw_bundles = record.get("candidates", record.get("candidate_bundles", []))
    for raw in raw_bundles:
        _bundle_check(CandidateBundle(**raw))
    return record


def candidate_isolation(adopted: Path) -> tuple[dict[str, Any], list[CandidateBundle]]:
    before = tree_hashes(adopted)
    candidate = create_candidate(adopted, "foreground_review", {
        "SKILL.md": (adopted / "SKILL.md").read_text(encoding="utf-8")
        + "\n## 渠道受理与通知核验\n\n渠道受理成功不等于最终到账；"
          "商户通知缺失不等于未付款，应分别核实交易状态与通知状态。\n",
        "references/channel-notification.md": "# 查询补充\n\n分别核实受理、到账、商户通知，不混用结论。\n",
    })
    after = tree_hashes(adopted)
    assert before == after
    checked = _bundle_check(candidate)
    assert checked["manifest"] != before
    result = {"passed": True, "adopted_before": before, "adopted_after": after,
              "adopted_unchanged": before == after, "candidate": candidate.to_dict(),
              "candidate_manifest": checked["manifest"]}
    write_json(EVIDENCE / "candidate_isolation.json", result)
    return result, [candidate]


def snapshot_restore(adopted: Path, run_id: str) -> dict[str, Any]:
    before = tree_hashes(adopted)
    saved = snapshot(adopted, f"lifecycle-{run_id}")
    extra = adopted / f"restore-extra-{uuid.uuid4().hex}.txt"
    changed: dict[str, str] = {}
    try:
        (adopted / "SKILL.md").write_text("验收用临时错误版本；随后立即恢复。\n", encoding="utf-8")
        extra.write_text("恢复必须删除这个快照中不存在的文件。\n", encoding="utf-8")
        changed = tree_hashes(adopted)
        assert changed != before
    finally:
        restored = restore(saved, adopted)
    assert restored == saved.manifest == before
    assert not extra.exists()
    result = {"passed": True, "snapshot": saved.to_dict(), "before": before,
              "temporarily_changed": changed, "restored": restored,
              "extra_removed": not extra.exists(), "whole_tree_matches": True}
    write_json(EVIDENCE / "snapshot_restore.json", result)
    return result


def snapshot_probe(run_id: str) -> None:
    """同一验收脚本检查支持文件、空目录和损坏快照，使用独立练习技能。"""
    from assembly.contracts import Snapshot

    probe = init_adopted(f"snapshot-probe-{run_id.lower()}")
    (probe / "references/nested").mkdir(parents=True)
    (probe / "empty").mkdir()
    (probe / "references/nested/rule.txt").write_text("成功与处理中分别汇总", encoding="utf-8")
    (probe / "helper.bin").write_bytes(bytes(range(256)))
    saved = snapshot(probe, f"probe-{run_id}")
    (probe / "references/nested/rule.txt").unlink()
    (probe / "helper.bin").write_bytes(b"changed")
    (probe / "extra.txt").write_text("额外文件", encoding="utf-8")
    manifest = restore(saved, probe)
    assert manifest == saved.manifest and (probe / "empty").is_dir() and not (probe / "extra.txt").exists()
    source = Path(saved.snapshot_dir) / "SKILL.md"
    original = source.read_bytes()
    source.write_bytes(b"corrupted")
    try:
        try:
            restore(saved, probe)
            raise AssertionError("未拒绝损坏快照")
        except ValueError:
            assert tree_hashes(probe) == manifest
    finally:
        source.write_bytes(original)
    for operation in (
        lambda: snapshot(probe, saved.label),
        lambda: snapshot(config.OUTPUT_DIR / "adopted/..", "invalid-parent"),
        lambda: restore(Snapshot("../../escape", saved.snapshot_dir, saved.manifest), probe),
    ):
        try:
            operation()
            raise AssertionError("未拒绝重复标签或越界路径")
        except (ValueError, FileExistsError):
            pass
    write_json(EVIDENCE / "snapshot_probe.json", {
        "passed": True, "snapshot": saved.to_dict(), "restored_manifest": manifest,
        "checks": {"nested_text": True, "binary_file": True, "empty_directory": True,
                   "extra_file_removed": True, "corrupt_snapshot_rejected_before_write": True,
                   "duplicate_label_rejected": True, "path_traversal_rejected": True}})


def _summary(report: dict[str, Any]) -> None:
    statuses = report.get("acceptance", {})
    rows = "\n".join(f"| {name} | {'通过' if item.get('passed') else '未通过'} | [{filename}]({filename}) |"
                     for name, filename in (("candidate-isolation", "candidate_isolation.json"),
                                            ("curator", "curator_result.json"),
                                            ("snapshot-restore", "snapshot_restore.json"),
                                            ("gepa", "gepa_result.json"))
                     if (item := statuses.get(name)) is not None)
    gepa = report.get("gepa", {})
    holdout = gepa.get("holdout_result", {})
    scores = gepa.get("offline_scores", {})
    curator = report.get("curator", {})
    score_rows = "\n".join(f"| {'基线' if row['candidate_id'] == scores.get('baseline_id') else '候选'} `{row['candidate_id']}` | {row['score']:.2f} |"
                           for row in scores.get("rows", []))
    failed_curator = []
    for path in sorted((EVIDENCE / "curator").glob("*.json")):
        attempt = _load(path)
        if not attempt.get("passed"):
            failed_curator.append(f"[{path.stem}]({path.relative_to(EVIDENCE).as_posix()})")
    earlier_failure = "、".join(failed_curator) or "无此前失败记录"
    text = f"""# 技能生命周期与离线优化验收

本次目标：技能创建、修订、真实 Hermes Curator 整合和 GEPA 优化都产出同一候选池中的 `CandidateBundle`，由评测模块与集成层决定采用。本模块不执行 ADOPT。

运行编号：`{report['run_id']}`。整体结果：**{'通过' if report.get('passed') else '未通过'}**。

| 验收 | 实际结果 | 证据 |
|---|---|---|
{rows}

## 统一入口与候选目录

- [完整运行报告](run_report.json)：四项结果、事件、基线哈希、只读文件核验和失败记录。
- [候选索引](candidate_index.json)：各来源的完整 `CandidateBundle`，可直接反序列化后交给评测模块。
- [候选逐项核验](candidate_validation.json)：完整技能文件、主文件哈希及整树清单。
- [最终核查](final_verification.json)：候选引用可达性、只读文件哈希与密钥扫描。
- 候选位于 `../candidates/<candidate_id>/`；正式技能位于 `../adopted/payment-status-investigation/`。
- 元数据写在本目录的 `candidates/`，不会成为技能目录的一部分。`base_skill_hash` 与 `candidate_hash` 是 `SKILL.md` 的 SHA-256；整目录校验另存清单。

## Hermes Curator 的真实运行

调用 `agent.curator.run_curator_review(synchronous=True, dry_run=False, consolidate=True)`；内部执行自动状态迁移与原生模型整合。显式开启 `curator.consolidate`，使用 `glm-5.2`。自动入口的 enabled、paused、首次运行、idle、interval 门控与手动运行的区别见 [Curator 记录](curator_result.json)。

首次原生运行曾将页面设计与支付调查一同吸收，未通过异任务保护检查，未导出候选。失败记录保留在 {earlier_failure}。随后在本地调用适配层追加用户要求的工作边界：按输入、业务判断和输出决定整合，页面设计独立保留，不凑归档数量。原始提示、追加约束、最终提示与原生返回状态都保存在对应 home。成功结果是 **Hermes Curator 加本工程范围约束** 的实测，不能据此声称原版已能避免误并；`.deps` 源码未改。

隔离 home：`{curator.get('home_dir', '见 Curator 记录')}`。整合结果复制成候选，归档只发生于隔离 home。实际整合、stale、归档以及 pinned 与相近名字异任务检查以 actions 和前后清单为准。

本次还确认了上游读取状态问题：并行 `skill_view` 的线程上下文标记没有回传，随后重读被去重，导致 3 次 `patch` 和 1 次 `edit` 被拒。原生模型随后使用真实 `terminal` 合并内容，再以 `skill_manage.delete(absorbed_into=...)` 归档。因此本次证明整合与归档完成，**不表示 patch/edit 路径通过**。源码位置和执行记录见 [上游问题说明](upstream_issues.json)；未修改上游，terminal 写入也受隔离 home 的操作系统限制。

## GEPA 的实现与结果边界

这里采用轻量反馈式精炼：真实模型执行训练任务，确定性核对和 LLM judge 产生反馈，再由 `glm-5.2` 基于上一轮的回复与扣分理由改写完整 `SKILL.md`。始终保留基线；候选通过统一创建入口落盘。实现参考 [GEPA 论文](https://arxiv.org/abs/2507.19457) 与 [DSPy GEPA](https://dspy.ai/api/optimizers/GEPA/overview/)，并非完整算法复现。

演示预先固定有偏的历史训练标签：把 processing 误记为付款成功；保留集按 success 才计入已付金额。它检验“优化了错误的离线目标，保留集会否退步”，不是声称真实业务优化有效。保留集不进入精炼，不根据其结果再搜索。

搜索停止原因：`{scores.get('stop_reason', '见原始记录')}`。保留基线结论：`{holdout.get('retain_baseline', '见原始记录')}`。轮次、确定性评分、judge 原始输出、训练与保留集的分数均见 [GEPA 记录](gepa_result.json)。预算与收敛条件是硬停止条件，分数接近阈值也不追加调用。

| 版本 | 训练分数 |
|---|---|
{score_rows}

保留集：基线 `{holdout.get('baseline', {}).get('score')}`，训练胜者 `{holdout.get('candidate', {}).get('score')}`；真实模型调用总数 `{scores.get('model_calls')}`。实测错误集中在“将渠道受理成功的 processing 计入成功金额”，逐题回复与反思保留在原始记录中。

## 恢复与验证

快照复制整目录，manifest 存在目录外；恢复先核验快照，再用准备好的完整目录替换正式目录。恢复后的文件清单必须与快照相同，多余文件被移除。[补充恢复检查](snapshot_probe.json) 验证嵌套文件、二进制、空目录、损坏快照拒绝和同名标签拒绝。

恢复必须由集成层在该技能无并行写入时调用；两次目录重命名不是断电事务。正常异常会回退，进程突然终止时应检查 `.restore-backup-*`。演示中仅快照恢复环节暂时修改正式目录，候选创建与模型评测期间保持其哈希不变。

## 重跑与范围

从工程根目录执行：

```bash
set -a
source ~/.hermes/.env
set +a
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
PYTHONDONTWRITEBYTECODE=1 .deps/hermes-agent/.venv/bin/python examples/assembly/run_lifecycle_demo.py
```

`--resume` 核查源码、正式目录与候选哈希后复用已完成模型记录。GEPA 实跑后若仅增强候选输入校验，可通过 [兼容复核](evidence_revalidation.json) 绑定原始来源指纹、现行指纹和结果文件哈希；不覆盖原始运行指纹，也不重复使用 holdout 调整模型。默认从头真实调用，不使用 mock。

本次只修改用户指定的生命周期、GEPA、验收脚本及 output 子目录；config、contracts、scenarios 和上游源码保持只读。未改飞书、未执行采用。集成层的真实业务评测与并发切换仍由对应模块负责。

遗留限制：Curator 需要本工程的任务范围约束，patch/edit 上游问题未修复；当前操作系统隔离依赖 macOS `sandbox-exec`。GEPA 是轻量教学实现；真正的业务评测、采用与并发切换由集成层负责。恢复不是断电事务。

未通过项：{report.get('error') or '最终四项验收均通过；首次原生 Curator 失败及上游工具拒绝已如实保留。'}
"""
    (EVIDENCE / "SUMMARY.md").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="校验哈希后复用本版本真实模型记录")
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    report: dict[str, Any] = {"run_id": run_id, "passed": False, "acceptance": {}, "events": [],
                              "scope": "候选创建、Curator、快照恢复、GEPA；不执行 ADOPT"}
    bundles: list[CandidateBundle] = []

    def event(kind: str, **detail: Any) -> None:
        report["events"].append(Event(kind=kind, detail=detail).to_dict())
        write_json(EVIDENCE / "run_report.json", report)
        print(kind, flush=True)

    try:
        config.clear_proxy_for_model()
        if not (os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")):
            raise RuntimeError("请先由 shell 加载 ~/.hermes/.env；脚本仅从环境读取密钥")
        adopted = init_adopted(config.SKILL_NAME)
        before = tree_hashes(adopted)
        report["adopted_before"] = before
        event("candidate-isolation.start")
        isolation, created = candidate_isolation(adopted)
        bundles.extend(created)
        report["acceptance"]["candidate-isolation"] = isolation
        event("candidate-isolation.done")
        snapshot_probe(run_id)
        report["acceptance"]["snapshot-restore"] = snapshot_restore(adopted, run_id)
        event("snapshot-restore.done")

        event("curator.start")
        curator = _resume_record("curator", before) if args.resume else None
        report["curator_reused"] = curator is not None
        if curator is None:
            curator = run_curator(config.OUTPUT_DIR / "homes" / f"lifecycle-{run_id}")
        report["curator"] = curator
        write_json(EVIDENCE / "curator_result.json", curator)
        assert curator["passed"], "Curator 验收未通过，参见真实 actions"
        assert tree_hashes(adopted) == before
        report["acceptance"]["curator"] = {"passed": True}
        bundles.extend(CandidateBundle(**raw) for raw in curator["candidate_bundles"])
        event("curator.done")

        event("gepa.start")
        gepa = _resume_record("gepa", before) if args.resume else None
        report["gepa_reused"] = gepa is not None
        if gepa is None:
            generated, offline, holdout = gepa_search(adopted, None, 2, {"min_improvement": 0.05, "patience": 1})
            gepa = {"candidates": [b.to_dict() for b in generated], "offline_scores": offline,
                    "holdout_result": holdout, "adopted_manifest_before": before,
                    "source_sha256": {str(config.ASSEMBLY_DIR / p): _sha(config.ASSEMBLY_DIR / p)
                                      for p in ("assembly/gepa.py", "assembly/lifecycle/skills.py")}}
            write_json(EVIDENCE / "gepa_result.json", gepa)
        report["gepa"] = gepa
        generated = [CandidateBundle(**raw) for raw in gepa["candidates"]]
        offline, holdout = gepa["offline_scores"], gepa["holdout_result"]
        assert len(generated) >= 3, "需要基线及至少两个真实生成候选"
        assert len({b.candidate_hash for b in generated}) >= 3, "基线与两个精炼候选必须是不同版本"
        assert holdout["offline_better_holdout_worse"], "尚未证明训练提高而 holdout 退步"
        assert holdout["offline_gain"] > 0 and holdout["holdout_delta"] < 0
        assert holdout["retain_baseline"], "本次未复现 holdout 输给基线；不能宣称通过"
        assert tree_hashes(adopted) == before
        bundles.extend(generated)
        report["acceptance"]["gepa"] = {"passed": True, "candidate_count": len(generated)}
        event("gepa.done")

        validated = [_bundle_check(b) for b in bundles]
        write_json(EVIDENCE / "candidate_index.json", [b.to_dict() for b in bundles])
        write_json(EVIDENCE / "candidate_validation.json", validated)
        report["adopted_after"] = tree_hashes(adopted)
        assert report["adopted_after"] == before
        preflight = EVIDENCE / "preflight.json"
        if preflight.exists():
            protected = _load(preflight)["protected_files"]
            report["protected_files_unchanged"] = {p: _sha(Path(p)) == h for p, h in protected.items()}
            assert all(report["protected_files_unchanged"].values())
        report["passed"] = True
        event("lifecycle.done")
    except Exception as exc:
        report["error"] = _safe_error(exc)
        event("lifecycle.failed", error=report["error"])
    finally:
        write_json(EVIDENCE / "run_report.json", report)
        _summary(report)
    print(f"结果：{'通过' if report['passed'] else '未通过'}；入口：{EVIDENCE / 'SUMMARY.md'}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
