#!/usr/bin/env python3
"""用 Hermes venv 独立运行三个真实联网验收；结果保存在 output/runtime。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

# Hermes 及只读公共模块不能因本演示生成 __pycache__。
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from assembly import config, contracts

OUTPUT = config.OUTPUT_DIR / "runtime"
CASES = ("smoke", "nonblocking", "failure-isolation")


def check_payment(task: contracts.TaskRun, order: str, amount: int) -> dict:
    """核对真实调用、原文件回执及最终回答；不用另一个模型充当裁判。"""
    from assembly.runtime.payment_tool import query_order_payments

    calls = [c for c in task.tool_calls if c.name == "query_order_payments" and c.arguments.get("order_id") == order]
    assert task.stop_reason == "final_answer", f"前台未正常完成：{task.stop_reason}"
    assert calls and all(not c.has_error for c in calls), f"缺少 {order} 成功工具调用"
    expected = query_order_payments(order)
    assert any(json.loads(c.result) == expected for c in calls), "工具回执与订单文件不一致"
    paid = sum(t["amount"] for t in expected["transactions"] if t["status"] == "success")
    assert paid == amount == expected["amount_due"], "验收订单的成功金额异常"
    assert str(amount) in task.answer and any(word in task.answer for word in (
        "已付清", "已结清", "已缴清", "全额覆盖", "全额付清", "全额到账",
    )), task.answer
    if order == "ORD-B2":
        assert "通知" in task.answer and any(w in task.answer for w in ("补发", "重发", "对账")), task.answer
    return {"passed": True, "order_id": order, "paid_success": paid,
            "tool_call_ids": [c.id for c in calls], "answer": task.answer}


def worker(case: str, run_id: str) -> None:
    from assembly.runtime import make_agent
    from assembly.runtime.background import measure_nonblocking, isolate_background_failure

    evidence = OUTPUT / run_id / case
    evidence.mkdir(parents=True, exist_ok=True)
    instance = f"runtime-{run_id}-{case}"
    agent = make_agent(instance, config.OUTPUT_DIR / "homes" / instance)
    prompt = "查一下 ORD-B1 结清了没有。请列出应付、成功付款合计和结论。"
    if case == "nonblocking":
        prompt += (
            " 我的长期工作偏好是：支付核查先给成功付款合计，再区分处理中流水和通知是否送达，"
            "结论使用简短中文。这次只需回答这个订单的付款情况。"
        )
    try:
        if case == "failure-isolation":
            review = isolate_background_failure(
                agent, instance, prompt,
                "请实际查询另一个订单 ORD-B2。商户没收到付款通知，款项到底到没到？给出金额、付款结论和下一步。",
                evidence_dir=evidence,
            )
        else:
            review = measure_nonblocking(agent, instance, prompt, evidence_dir=evidence)
        contracts.write_json(evidence / "background.json", review)
        # 回读落盘契约，使验收与读者看到的证据一致。
        data = json.loads((evidence / "foreground.json").read_text(encoding="utf-8"))
        data["tool_calls"] = [contracts.ToolCallRecord(**c) for c in data["tool_calls"]]
        first = contracts.TaskRun(**data)
        checks = [check_payment(first, "ORD-B1", 900)]
        if case == "failure-isolation":
            second_data = json.loads((evidence / "foreground-second.json").read_text(encoding="utf-8"))
            second_data["tool_calls"] = [contracts.ToolCallRecord(**c) for c in second_data["tool_calls"]]
            second = contracts.TaskRun(**second_data)
            assert first.session_id == second.session_id, "连续任务没有复用同一个 session"
            assert [m["content"] for m in agent._session_messages if m.get("role") == "user"] == [first.prompt, second.prompt], \
                "前台会话中出现了额外用户指令，需排查后台复盘污染"
            checks.append(check_payment(second, "ORD-B2", 500))
            assert review.failed and review.error, "缺少原生后台失败记录"
        else:
            assert not review.failed, review.error
            assert review.finished_after_sec > first.foreground_elapsed_sec
            if case == "nonblocking":
                assert review.actions, "没有观察到实际保存动作，不能据此声称后台学习完成"
        assert review.triggered and not review.blocked_foreground
        contracts.write_json(evidence / "acceptance.json", {
            "passed": True, "checks": checks, "home": str(agent._runtime_home),
            "model": agent.model, "provider": agent.provider, "base_url": agent.base_url,
            "session_id": agent.session_id, "valid_tool_names": sorted(agent.valid_tool_names),
            "memory_nudge_interval": agent._memory_nudge_interval,
            "skill_nudge_interval": agent._skill_nudge_interval,
        })
    finally:
        agent.close()
        agent._runtime_db.close()


def manifest() -> dict[str, str]:
    paths = [config.ASSEMBLY_DIR / "assembly" / name for name in ("config.py", "contracts.py")]
    paths += list(config.SCENARIOS_DIR.glob("*.json"))
    paths += [config.HERMES_SRC / path for path in (
        "run_agent.py", "agent/agent_init.py", "agent/conversation_loop.py",
        "agent/background_review.py", "agent/turn_finalizer.py",
    )]
    return {str(p.relative_to(config.REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def implementation_manifest() -> dict[str, str]:
    paths = list((config.ASSEMBLY_DIR / "assembly" / "runtime").glob("*.py")) + [Path(__file__).resolve()]
    return {str(p.relative_to(config.REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def summary(run_id: str, results: list[dict], before: dict, after: dict) -> None:
    lines = ["# 运行时真实验收", "", f"运行编号：`{run_id}`。模型 `{config.MODEL}`，provider `deepseek`，地址 `{config.BASE_URL}`。",
             "", "## 目标与范围", "", "真实 Hermes 前台查询订单；轮末由上游启动原生 daemon 复盘；注入一次后台异常后连续查询另一订单。仅新增 runtime 模块、独立演示脚本及指定 output 文件，不改公共配置、契约、场景或上游源码。",
             "", "## 实际结果", "", "| 验收 | 结果 | 前台耗时 | 后台 target 退出（相对首个前台开始） | 证据 |", "|---|---|---:|---:|---|"]
    for item in results:
        case = item["case"]
        directory = OUTPUT / run_id / case
        task_path, bg_path = directory / "foreground.json", directory / "background.json"
        task = json.loads(task_path.read_text()) if task_path.exists() else {}
        bg = json.loads(bg_path.read_text()) if bg_path.exists() else {}
        elapsed = task.get("foreground_elapsed_sec")
        finished = bg.get("finished_after_sec")
        lines.append(f"| {case} | {'通过' if item['passed'] else '失败'} | {f'{elapsed:.3f} 秒' if elapsed is not None else '未知'} | {f'{finished:.3f} 秒' if finished is not None else '未知'} | [目录]({run_id}/{case}/)、[前台]({run_id}/{case}/foreground.json)、[后台]({run_id}/{case}/background.json)、[观测]({run_id}/{case}/observation.json) |")
    for item in results:
        case = item["case"]
        directory = OUTPUT / run_id / case
        lines += ["", f"### {case}"]
        for filename in ("foreground.json", "foreground-second.json"):
            path = directory / filename
            if path.exists():
                task = json.loads(path.read_text())
                lines += ["", f"`{filename}`：session `{task['session_id']}`；{task['foreground_elapsed_sec']:.3f} 秒；stop_reason=`{task['stop_reason']}`。", "", task["answer"]]
        bg_path = directory / "background.json"
        if bg_path.exists():
            bg = json.loads(bg_path.read_text())
            lines += ["", f"triggered=`{bg['triggered']}`，blocked_foreground=`{bg['blocked_foreground']}`，failed=`{bg['failed']}`。", "", "后台动作：" + ("；".join(bg["actions"]) or "未观察到保存动作（不等于复盘未运行）。")]
            if bg["error"]:
                lines += ["", "后台错误：`" + bg["error"] + "`。"]
        if not item["passed"]:
            lines += ["", f"详见 [错误]({run_id}/{case}/error.json) 和 [进程日志]({run_id}/{case}/console.log)。"]
    lines += ["", "## 确切配置与运行方式", "", "每个实例在全新子进程中，先把 HERMES_HOME 设为 output/homes/<instance>，再导入 run_agent.AIAgent。实例 config.yaml 包含：", "", "```yaml", "memory:", "  memory_enabled: true", "  user_profile_enabled: true", "  nudge_interval: 1", "skills:", "  creation_nudge_interval: 1", "  external_dirs: []", "auxiliary:", "  background_review:", "    provider: auto", "```", "", "隔离配置还设 tools.tool_search.enabled: off，使自定义支付函数直接出现在工具列表。构造参数 enabled_toolsets=['assembly_payments', 'memory', 'skills']；后台沿用前台真实 DeepSeek 模型。没有单独的 background_review.enabled。记忆按用户轮数、技能按工具迭代数触发；上游默认间隔均为 10。支付函数经 ToolRegistry.register 注册，不替换 Hermes dispatch。", "", "```bash", "read -r -s DEEPSEEK_API_KEY", "export DEEPSEEK_API_KEY", "unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY", ".deps/hermes-agent/.venv/bin/python -B examples/assembly/run_runtime_demo.py", "```", "", "## 验证方法与边界", "", "观测器保留原生 target 和 daemon 线程，只记录开始、退出、前台返回及 join 后的终止观测。没有人为 sleep 拉长后台，也没有 mock 模型或工具回执。后台返回正文、工具动作和警告均来自真实执行；finished_after_sec 使用 target 退出时刻，作为临近线程终止的下界；join 观测是确认线程终止的上界，两者均保留。正常复盘要求前台返回时后台仍存活且随后结束；故障场景依据原生 daemon、两轮前台正常返回且之后才 join 验证不等待后台，允许异常在第一轮返回前迅速发生。", "", "故障仅在该隔离 agent 的后台线程读取 fork 运行参数时抛一次 RuntimeError；前台模型仍真实联网。上游通过 _emit_auxiliary_failure → _emit_warning → status_callback('warn', ...) 记录失败。此验收证明这条异常路径，不代表所有网络故障都已覆盖。", "", "源码审查发现：background_review.py 当前忽略 review_agent.run_conversation() 返回的 failed 字段；如果后台模型错误被转成失败字典而非抛出异常，原生辅助失败警告可能缺失。另有相同 provider/model 时忽略 auxiliary base_url 的行为。因此本次不以无效 endpoint 作为确定性注入手段，未修改上游。", "", "新 home 没有指定的 payment-status-investigation/SKILL.md 时，skill_hash 如实为 null；存在时记录任务开始前文件哈希，不捏造已加载技能。该轮末学习不等于下一轮已经采用新 Skill。", "", f"只读文件哈希前后{'一致' if before == after else '不一致，需检查'}，详见 [来源与校验]({run_id}/manifest.json)。任务子进程均使用独立 home；不访问飞书。", "", "后台是 daemon：交互进程退出可能终止未完成复盘。演示只在前台返回之后等待后台，以便保存完整证据；生产进程若需要可靠完成，应另外设计关闭时的等待或持久任务队列。"]
    (OUTPUT / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", choices=("all", *CASES), default="all")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            worker(args.case, args.run_id)
            return 0
        except Exception as exc:
            directory = OUTPUT / args.run_id / args.case
            contracts.write_json(directory / "error.json", {"error": str(exc), "traceback": traceback.format_exc()})
            print(f"{args.case} 失败：{exc}", flush=True)
            return 1
    config.api_key()
    config.clear_proxy_for_model()
    python = config.HERMES_SRC / ".venv" / "bin" / "python"
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    cases = CASES if args.case == "all" else (args.case,)
    before = manifest()
    implementation_before = implementation_manifest()
    results = []
    for case in cases:
        directory = OUTPUT / run_id / case
        directory.mkdir(parents=True, exist_ok=True)
        print(f"开始 {case}：真实 {config.MODEL}，证据 {directory}", flush=True)
        with (directory / "console.log").open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run(
                    [str(python), "-B", str(Path(__file__).resolve()), case, "--worker", "--run-id", run_id],
                    env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT, timeout=600,
                )
                passed = result.returncode == 0
            except subprocess.TimeoutExpired:
                contracts.write_json(directory / "error.json", {"error": "子进程超过 600 秒，已终止；本项未通过"})
                passed = False
        results.append({"case": case, "passed": passed})
        print(f"{case}：{'通过' if passed else '失败'}", flush=True)
    after = manifest()
    contracts.write_json(OUTPUT / run_id / "manifest.json", {"before": before, "after": after, "unchanged": before == after, "implementation_before": implementation_before, "implementation_after": implementation_manifest(), "python": sys.version, "hermes_commit": subprocess.check_output(["git", "-C", str(config.HERMES_SRC), "rev-parse", "HEAD"], text=True).strip()})
    contracts.write_json(OUTPUT / run_id / "results.json", results)
    summary(run_id, results, before, after)
    print(f"统一入口：{OUTPUT / 'SUMMARY.md'}", flush=True)
    return 0 if all(r["passed"] for r in results) and before == after and implementation_before == implementation_manifest() else 1


if __name__ == "__main__":
    raise SystemExit(main())
