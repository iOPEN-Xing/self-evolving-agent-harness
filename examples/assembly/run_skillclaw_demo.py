#!/usr/bin/env python3
"""真实运行 SkillClaw 共享演化；所有结果保存在 output/skillclaw。

从仓库根运行：
  .deps/hermes-agent/.venv/bin/python -B examples/assembly/run_skillclaw_demo.py

使用上游 workflow、session judge、skill verifier 和 ValidationWorker。
本演示的支付数据和内部队列名称是演示夹具，模型回答、评判、技能和发布均为真实运行。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True

from assembly import config
from assembly.contracts import SharedRevision, tree_hashes, write_json
from assembly.skillclaw.server import runtime_paths, start_server, stop_server
from assembly.skillclaw.client import (
    publish, pull_and_load, run_task, trigger_evolution, upload_sessions,
)


POLICY = (
    "这是总装支付演示系统的值班任务。内部规程：只累计status=success金额；"
    "processing且channel_acceptance=success是已受理未到账，route=payops-confirm-17，"
    "等待或查询最终状态，不退款、不重复扣款；足额success但"
    "merchant_notification=merchant_notification_missing时，route=merchant-notify-23，"
    "只补发通知或对账，不重扣。"
)
OUTPUT_FORMAT = (
    "返回一个JSON对象，字段state、paid_amount、remaining、route、action。"
    "route必须依据本系统资料或已加载技能；未知写unknown，不猜测。"
)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def answer_json(answer: str) -> dict:
    """兼容模型在 JSON 外添加代码围栏，但不修补模型内容。"""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回JSON对象；保留原始回答供核查")
    result = json.loads(answer[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("模型回答不是对象")
    return result


def protected_fingerprints() -> dict:
    paths = [config.ASSEMBLY_DIR / "assembly/config.py",
             config.ASSEMBLY_DIR / "assembly/contracts.py"]
    paths.extend(sorted(config.SCENARIOS_DIR.rglob("*")))
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=config.SKILLCLAW_SRC,
    ).decode().split("\0")
    paths.extend(config.SKILLCLAW_SRC / rel for rel in tracked if rel)
    return {str(p.relative_to(config.REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def progress(message: str) -> None:
    print(time.strftime("%H:%M:%S") + " " + message, flush=True)


def assert_check(report: dict, name: str, passed: bool, detail: object) -> None:
    report["checks"][name] = {"passed": bool(passed), "detail": detail}
    write_json(Path(report["run_root"]) / "acceptance.json", report)
    if not passed:
        raise RuntimeError(f"验收未通过：{name}")


def render_summary(report: dict, revision: SharedRevision) -> None:
    output = config.OUTPUT_DIR / "skillclaw"
    root = Path(report["run_root"])
    rel = root.relative_to(output).as_posix()
    status = "全部通过" if report.get("passed") else "未全部通过"
    lines = [
        "# SkillClaw 跨实例共享实跑记录", "",
        f"本次验收：**{status}**。运行目录：[{rel}]({rel}/acceptance.json)。",
        "",
        "```mermaid", "flowchart LR",
        'A["实例 A / DeepSeek 会话"] --> U["SkillClaw 原生会话上传"]',
        'B["实例 B / DeepSeek 会话"] --> U',
        'U --> E["HTTP trigger：摘要、评判、聚合、演化"]',
        'E --> V["skill_verifier"] --> D["独立 validator-D 重放与评分"]',
        'D --> P["再次 trigger：发布版本"] --> C["实例 C 拉取、读取技能、处理新订单"]',
        "```", "",
        "## 目标和授权范围", "",
        "真实启动上游服务，用真实模型会话完成共享演化、验证、发布和第三实例使用。"
        "只创建 assembly/skillclaw/、run_skillclaw_demo.py、output/skillclaw/，"
        "第三实例位于 output/homes/C/；安装缺少的服务端依赖。未授权其他模块、上游源码或飞书修改。", "",
        "## 四项验收", "",
        "| 项目 | 实际结果 | 核查材料 |", "|---|---|---|",
    ]
    entries = [
        ("1. 服务启动和健康检查", "server", "startup.json"),
        ("2. A/B 真实会话上传", "upload", "uploaded_sessions.json"),
        ("3. 演化、第三方验证、发布", "publication", "publication.json"),
        ("4. C 加载和行为改变", "consumption", "comparison.json"),
    ]
    for label, key, artifact in entries:
        check = report["checks"].get(key)
        state = ("通过" if check["passed"] else "未通过") if check else "尚未完成"
        lines.append(f"| {label} | {state} | [{artifact}]({rel}/{artifact}) |")
    lines += ["", f"真实会话 ID：`{', '.join(revision.uploaded_sessions) or '尚未上传'}`。",
              f"发布版本：`{revision.published_version}`；C 加载的 SKILL.md SHA-256：`{revision.loaded_skill_hash}`。", ""]
    comparison = report.get("comparison", {})
    if comparison:
        lines += [f"同一新订单的 route：加载前 `{comparison.get('before_route')}`，"
                  f"加载后 `{comparison.get('after_route')}`；加载后支付金额 "
                  f"`{comparison.get('after_paid_amount')}`，差额 `{comparison.get('after_remaining')}`。", ""]
    verifications = report.get("verifications", [])
    for item in verifications:
        result = item.get("verification", {})
        lines += [f"skill_verifier：`{item.get('skill_name')}`，"
                  f"decision=`{result.get('decision')}`，score=`{result.get('score')}`，"
                  f"threshold=`{result.get('threshold')}`。原始结论及各项分数见"
                  f"[演化响应]({rel}/evolution.json)。", ""]
    publication_path = root / "publication.json"
    if publication_path.exists():
        publication = json.loads(publication_path.read_text())
        for results in publication.get("results", {}).values():
            for result in results:
                replay = result.get("replay_summary", {})
                lines += [f"独立验证客户端 `{result.get('user_alias')}`："
                          f"重放 `{replay.get('case_count')}` 个源会话任务，"
                          f"基线平均分 `{replay.get('baseline_mean_score')}`，"
                          f"候选平均分 `{replay.get('candidate_mean_score')}`，"
                          f"结论 `{result.get('decision')}`。", ""]
    for note in report.get("scope_notes", []):
        lines += [note, ""]
    lines += [
        "## 真实接口与存储差异", "",
        "服务以指定 Hermes venv 的 `python -B -m evolve_server --port ...` 启动，"
        "cwd 为 `.deps/SkillClaw`。上游实际绑定 `0.0.0.0`，本演示通过配置的本机地址访问。"
        "就绪检查为 `GET /health` 和 `GET /status`；演化及验证后的发布均为 `POST /trigger`。", "",
        "会话上传调用上游 `SkillClawAPIServer._upload_session_data`，"
        "候选和验证结果由 `ValidationStore` 管理，拉取用 `SkillHub.pull_skills`，"
        "发现技能用 `SkillManager`。没有新增 sessions.jsonl 或自行设计传输协议。"
        "原生存储中仍有单会话 JSON 对象，它们由上游接口写入。", "",
        "此版本 `--mock` 无论是否指定端口都会单次运行后退出，所以常驻服务采用上游"
        " `--storage-backend local`。实际类为 `skillclaw.object_store.LocalObjectStore`，"
        "不是 `storage/mock_bucket.py` 的 `LocalBucket`；二者均用本地文件模拟对象存储。"
        "仅存储替代了远程 OSS；模型、演化、验证、发布与客户端拉取均实际执行。"
        "真实 OSS 还涉及凭据、网络访问、远程共享和并发条件，本演示未覆盖这些能力。", "",
        "## 证据、限制和复跑", "",
        f"- [统一结构化记录]({rel}/acceptance.json)、[SharedRevision]({rel}/shared_revision.json)、"
        f"[服务日志]({rel}/server.log)、[停止记录]({rel}/shutdown.json)。",
        f"- [源会话]({rel}/source_sessions.json)、[演化响应]({rel}/evolution.json)、"
        f"[发布与验证]({rel}/publication.json)、[加载前后对照]({rel}/comparison.json)。",
        f"- [原生上传回执]({rel}/upload_receipts.json)、"
        f"[执行时脚本副本]({rel}/source/run_skillclaw_demo.py)。",
        "- A/B 和 C 使用最小 OpenAI-compatible Agent；C 根据 SkillManager 提供的目录，"
        "通过只读工具读取实际拉取的 SKILL.md 后回答。此次未声称验证 Hermes 完整运行时集成。",
        "- 第三方验证指独立的验证客户端和模型调用；演化与验证都用 deepseek-flash，"
        "不是不同厂商模型，也不是独立人工审查。ValidationWorker 重放的是源会话，"
        "C 则使用不同订单号及金额的新演示订单；单例行为变化不能证明总体泛化能力。",
        "- 内部队列 payops-confirm-17 / merchant-notify-23 是演示规程；仅 A/B 会话提供规程，"
        "C 前后两次问题相同且不含这些队列名。未人工编写或补改模型产生的候选技能。",
        "- 原生上传目前只有内部 Python 方法，因此适配器对该上游版本的接口有依赖。",
        "- 每次复跑使用新的存储分组和实例目录，保留先前证据，不删除历史运行。",
        "", "本机此次使用现有配置助手读取 ~/.hermes/.env 中的 DEEPSEEK_API_KEY。"
        "外部环境原有 DEEPSEEK_API_KEY 返回401，因此只在本次子进程中移除这个旧变量：",
        "", "```bash", "env -u DEEPSEEK_API_KEY .deps/hermes-agent/.venv/bin/python -B examples/assembly/run_skillclaw_demo.py", "```", "",
        f"上游版本：`{report.get('upstream_commit', '未知')}`。",
        f"只读文件指纹核对：`{report.get('protected_unchanged', '未完成')}`。",
        f"密钥落盘检查：`{report.get('secret_scan', '未完成')}`。", "",
    ]
    if report.get("error"):
        lines += ["本次失败：" + report["error"], "没有把失败或未执行步骤计为通过。", ""]
    log_path = root / "server.log"
    if log_path.exists():
        fragments = [line for line in log_path.read_text().splitlines() if any(
            token in line for token in ("Uvicorn running", "GET /health", "GET /status",
                                       "drained 2 session", "uploaded skill", "POST /trigger")
        )]
        lines += ["## 实际服务日志片段", "", "```text", *fragments[:8], "```", ""]
    shutdown_path = root / "shutdown.json"
    if shutdown_path.exists():
        shutdown = json.loads(shutdown_path.read_text())
        stop_note = ("上游周期任务收到 SIGTERM 后未在5秒内结束，已按约定强制终止并回收进程。"
                     if shutdown.get("forced_kill") else "服务已优雅终止并回收进程。")
        lines += [stop_note, ""]
    earlier = []
    for attempt in sorted((output / "runs").glob("*/acceptance.json")):
        if attempt.parent == root:
            continue
        prior = json.loads(attempt.read_text())
        if prior.get("started_at", 0) >= report.get("started_at", 0):
            continue
        if prior.get("error"):
            link = attempt.relative_to(output).as_posix()
            earlier.append(f"- [{attempt.parent.name}]({link})：{prior['error']}")
    if earlier:
        lines += ["## 保留的失败尝试", "", *earlier, "",
                  "首次401未产生真实会话；第二次完成A/B会话，但C在空技能目录中反复尝试读取。"
                  "已修正空目录时的工具暴露条件，之后完整重跑通过；"
                  "同时补充失败轨迹保存，第二次C失败时尚未保存完整工具轨迹，不能补造。", ""]
    lines += ["共享秘书状态只读；当前工具未提供跨会话列表，未刷新其他会话，也未推断用户持续工作时间。", ""]
    local_text = "\n".join(lines).replace(f"]({rel}/", "](./").replace("](runs/", "](../../runs/")
    (root / "SUMMARY.md").write_text(local_text, encoding="utf-8")
    (output / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(output / "latest.json", {"run_root": str(root), "passed": report.get("passed", False)})


def main() -> int:
    config.clear_proxy_for_model()
    config.api_key()  # 配置助手只把凭据载入进程环境。
    paths = runtime_paths()
    root = Path(paths["root"])
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=root / "client.log", level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    endpoint = config.EVOLVE_SERVER_ENDPOINT
    report = {"run_root": str(root), "endpoint": endpoint, "model": config.MODEL,
              "checks": {}, "started_at": time.time(), "passed": False}
    # 保留实际运行时的四个文件，便于区分后续修订与本次执行。
    source_paths = [Path(__file__)] + list((config.ASSEMBLY_DIR / "assembly/skillclaw").glob("*.py"))
    for source in source_paths:
        snapshot = root / "source" / source.relative_to(config.ASSEMBLY_DIR)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source.read_bytes())
    revision = SharedRevision(["A", "B"], [], False, False, None, None, None, False)
    before_files = protected_fingerprints()
    report["upstream_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=config.SKILLCLAW_SRC, text=True,
    ).strip()
    proc = None
    try:
        progress("启动真实 evolve_server")
        proc = start_server()
        startup = json.loads((root / "startup.json").read_text())
        assert_check(report, "server", proc.poll() is None, startup)

        orders = json.loads((config.SCENARIOS_DIR / "orders.json").read_text())
        sessions = []
        for alias, order_id in [("A", "ORD-A2"), ("B", "ORD-B2")]:
            progress(f"实例 {alias} 用 {config.MODEL} 处理 {order_id}")
            prompt = POLICY + "订单" + order_id + "：" + compact(orders[order_id]) + OUTPUT_FORMAT
            behavior = run_task(root / "instances" / alias, prompt, load_skills=False, instance=alias)
            answer_json(behavior["answer"])
            turns = behavior.get("turns") or [{
                "turn_num": 1, "raw_turn_kind": "assistant_response",
                "prompt_text": prompt, "response_text": behavior["answer"],
                "tool_calls": [], "tool_results": [], "read_skills": [],
                "modified_skills": [], "injected_skills": [], "prm_score": None,
            }]
            sessions.append({"session_id": behavior["session_id"],
                             "instance": alias, "turns": turns, "behavior": behavior})
        write_json(root / "source_sessions.json", sessions)

        c_home = config.OUTPUT_DIR / "homes" / "C" / root.name
        if c_home.exists():
            raise RuntimeError("C 实例目录已存在，拒绝混用之前的技能")
        held_out = {"amount_due": 1300, "transactions": [
            {"txn_id": "TXN-C1", "amount": 500, "status": "success"},
            {"txn_id": "TXN-C2", "amount": 800, "status": "success"}],
            "merchant_notification": "merchant_notification_missing"}
        c_prompt = "总装支付演示系统：核实订单ORD-C3付款状态并选择处理队列。数据：" + compact(held_out) + OUTPUT_FORMAT
        before_hashes = tree_hashes(c_home / "skills")
        progress("记录未加载共享技能的 C 基线")
        baseline = run_task(c_home, c_prompt, load_skills=True)
        write_json(root / "c_before.json", baseline)
        assert_check(report, "c_fresh", not before_hashes and not baseline.get("read_skills"), baseline)

        progress("通过 SkillClaw 原生客户端上传 A/B 会话")
        uploaded = upload_sessions(endpoint, sessions)
        revision.uploaded_sessions = uploaded
        assert_check(report, "upload", len(uploaded) == 2 and len(set(uploaded)) == 2, uploaded)

        progress("触发真实摘要、会话评判、聚合、演化和 skill_verifier")
        evolution = trigger_evolution(endpoint)
        write_json(root / "evolution.json", evolution)
        candidates = [r for r in evolution.get("evolutions", []) if r.get("action") == "queued_for_validation"]
        report["verifications"] = candidates
        revision.evolved = bool(candidates)
        verifier_ok = bool(candidates) and all(r.get("verification", {}).get("accepted") is True for r in candidates)
        assert_check(report, "evolution", verifier_ok and evolution.get("sessions") == 2
                     and evolution.get("session_judge", {}).get("scored_sessions") == 2
                     and not evolution.get("had_processing_error"), evolution)

        progress("独立 validator-D 重放验证，随后请求服务端发布")
        published = publish(endpoint, evolution)
        write_json(root / "publication.json", published)
        manifest = published.get("manifest", {})
        records = [r for r in published.get("publication", {}).get("evolutions", [])
                   if r.get("action") == "published_after_validation" and r.get("uploaded")]
        replay_results = [result for results in published.get("results", {}).values() for result in results]
        replay_ok = bool(replay_results) and all(
            result.get("accepted") is True and result.get("validator_mode") == "replay"
            and result.get("score", 0) >= 0.75
            and bool(result.get("replay_summary", {}).get("cases"))
            and all(case.get(branch, {}).get("response_text", "").strip()
                    and case.get(branch, {}).get("prm_votes")
                    and "fail" not in case[branch]["prm_votes"]
                    for case in result["replay_summary"]["cases"] for branch in ("baseline", "candidate"))
            for result in replay_results
        )
        candidate_jobs = {row["validation_job_id"] for row in candidates}
        lineage_ok = bool(records) and all(
            set(row.get("session_ids", [])) == set(uploaded)
            and row.get("validation_job_id") in candidate_jobs
            and published.get("decisions", {}).get(row["validation_job_id"], {}).get("status") == "published"
            and manifest.get(row["skill_name"], {}).get("version") == row["version"]
            for row in records
        )
        revision.verified = verifier_ok and replay_ok
        revision.published_version = ",".join(f"{r['skill_name']}@v{r['version']}" for r in records) or None
        assert_check(report, "publication", lineage_ok and replay_ok and bool(manifest), published)

        progress("C 用 SkillHub 拉取版本，真实读取技能并再次处理相同新订单")
        loaded_hash, behavior = pull_and_load(endpoint, c_home, c_prompt)
        write_json(root / "c_after.json", behavior)
        before_answer, after_answer = answer_json(baseline["answer"]), answer_json(behavior["answer"])
        expected_hashes = {item.get("sha256") for item in manifest.values()}
        comparison = {
            "prompt": c_prompt, "held_out_order": held_out,
            "before_skill_hashes": before_hashes,
            "after_skill_hashes": tree_hashes(c_home / "skills"),
            "loaded_skill_hash": loaded_hash, "published_skill_hashes": sorted(expected_hashes),
            "before_route": before_answer.get("route"), "after_route": after_answer.get("route"),
            "after_paid_amount": after_answer.get("paid_amount"), "after_remaining": after_answer.get("remaining"),
            "before": baseline, "after": behavior,
        }
        write_json(root / "comparison.json", comparison)
        report["comparison"] = {k: v for k, v in comparison.items() if k not in {"before", "after"}}
        reads = behavior.get("read_skills", [])
        read_hashes_match = bool(reads) and all(
            item.get("sha256") == manifest.get(item.get("skill_name"), {}).get("sha256") for item in reads
        )
        changed = (not before_hashes and bool(behavior.get("skill_hashes"))
                   and read_hashes_match
                   and before_answer.get("route") == "unknown"
                   and after_answer.get("route") == "merchant-notify-23"
                   and after_answer.get("paid_amount") == 1300 and after_answer.get("remaining") == 0)
        revision.loaded_by_instance = "C"
        revision.loaded_skill_hash = loaded_hash
        revision.consumed_in_next_round = changed
        assert_check(report, "consumption", changed, report["comparison"])
        report["passed"] = True
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            if os.environ.get(name):
                message = message.replace(os.environ[name], "[已隐藏密钥]")
        report["error"] = message
        progress(message)
    finally:
        if proc is not None:
            stop_server(proc)
        report["protected_unchanged"] = before_files == protected_fingerprints()
        if not report["protected_unchanged"]:
            report["passed"] = False
            report["error"] = report.get("error", "") + " 只读文件指纹发生变化，需核查并发写入。"
        # 只报告命中文件，不输出敏感值；检测范围包括整个本次输出和 C 实例。
        secrets = [os.environ[k].encode() for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
                   if len(os.environ.get(k, "")) > 8]
        targets = list(root.rglob("*"))
        if "c_home" in locals():
            targets.extend(c_home.rglob("*"))
        hits = [str(p) for p in targets if p.is_file() and any(secret in p.read_bytes() for secret in secrets)]
        report["secret_scan"] = {"passed": not hits, "matched_files": hits}
        if hits:
            report["passed"] = False
        report["finished_at"] = time.time()
        write_json(root / "shared_revision.json", revision)
        write_json(root / "acceptance.json", report)
        render_summary(report, revision)
    progress(f"验收{'通过' if report['passed'] else '未全部通过'}；入口：{config.OUTPUT_DIR / 'skillclaw/SUMMARY.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
