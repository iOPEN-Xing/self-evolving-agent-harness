#!/usr/bin/env python3
"""官方 GEPA 搜索，课程只负责 Hermes 执行、评分、预算和采用规则。"""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
HERMES = Path(os.environ.get("HERMES_SRC", str(PROJECT / ".deps/hermes-agent"))).resolve()
DATA_VERSION = "demo-v2-20260926"
DATA_DIR = HERE / "data" / DATA_VERSION
OUTPUT_ROOT = HERE / "output/native-gepa"
WORKER_PYTHON = Path(os.environ.get("HERMES_PYTHON", str(HERMES / ".venv/bin/python")))
FRONT = '---\nname: payment-check\ndescription: 查询指定商户的订单，报告到账判断和可核对依据。\n---\n'
BASELINE = '''# 订单核查

用户提供订单号和商户号。你的任务是查询真实记录，再简洁报告当前情况。只使用 query_payment 读取当题材料，不对外写数据。先提取用户明确给出的标识，再调用工具获取订单记录；信息不足时报告无法确定，不根据客户措辞猜测。

仔细阅读返回字段，结合状态与记录说明判断处理结果。有多条记录时比较它们，报告与你的判断有关的证据。不要把不同记录里的金额、商户和状态拼接成一条不存在的记录。遇到描述含糊的字段时，保留不确定性，不作无法证实的承诺。

最终只输出一个约定格式的纯 JSON 对象，不写分析、说明前后缀或 Markdown 代码围栏；reason 中不嵌入未转义的双引号。字段为 order_id、merchant_id、paid（布尔值）、evidence_ids（记录标识数组）、reason（中文理由）。订单号和商户号应使用用户提供的原值。reason 应说明判断依据，避免重复用户问题。证据标识必须来自实际工具返回，不自行编写。只核查当前任务，不创建 Memory，不新增 Skill，不修改文件。

保持回答紧凑；不需要展示内部推理。若记录明确显示失败或仍在处理中，说明目前不能确认到账。业务字段说明保存在 references/schema.md，与本文件共同构成固定目录，优化只能修改本正文。
'''
BAD = '''# 固定故障候选：遗漏商户过滤
这是公开教学故障测试，按下列有缺陷的算法执行，以便验证拦截器；不要自行修正算法。
1. 调用 query_payment 时只传 order_id，实参中禁止包含 merchant_id，即使用户提供了商户号也不得传入。
2. 直接选返回 records 数组的第一条记录，不按商户筛选，不再次查询；仅当该行 status 为 SETTLED 时 paid=true，否则为 false。
3. order_id、merchant_id 填用户原值；evidence_ids 仅填第一条记录的 record_id，reason 说明该行状态。只输出这些字段组成的纯 JSON，不加前后缀或代码围栏，不嵌未转义引号。
这是故意保留的跨商户错误，检查器负责拒绝，执行时必须显露错误。'''

REFERENCE = '''# 字段说明
order_id 是商户内部订单号，须与 merchant_id 共同定位业务实体。record_id 是记录唯一标识。SETTLED 表示已到账；ACCEPTED 只表示受理，未证明到账；REJECTED 表示未到账。amount_cents 单位为分。课程工具只读，返回的数据是课程构造的业务记录，不代表真实客户订单。'''
BUDGET = {"search_metric_calls": 30, "max_new_proposals": 2, "search_seconds": 600,
          "audit_validation_executions": 3, "holdout_executions": 6, "fault_executions": 2,
          "probe_executions": 2, "max_case_seconds": 120,
          "holdout_mean_tokens_limit": 12000, "holdout_case_seconds_limit": 120,
          "min_holdout_gain": 0.02, "max_full_chars": 15000, "max_growth": 0.2}
REFLECTION_CONTRACT = """你在为演示做精炼改写，必须沿用官方提示要求的返回标记。
仅改 skill_body 正文，不加文件头；目标300—420个字符，绝不可比原正文长。
用精简步骤明确订单与商户共同匹配、SETTLED 才确认到账、ACCEPTED 不能确认，以及纯 JSON 输出。
保留必要业务和工具约束，删去重复说明，不新增示例、长解释或章节。禁止输出多个备选正文。
这轮的目标是产生简洁、可真实执行的候选；是否采用仍由真实验证和独立留出决定。"""



def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def make_case(split, index, category):
    # 新版本材料不复用上轮已曝光的留出；所有题仍是明确标注的教学构造。
    order = f"ORD-V2-{split.upper()}-{index:02}"
    merchant = f"M-V2-{split.upper()}-{index:02}"
    status = "ACCEPTED" if category == "accepted" else "SETTLED"
    contexts = {
        "train": {"settled": "网店订单核查：银行电子回单与当前支付记录一致，账簿已结算。",
                  "accepted": "预约扣款核查：渠道接受预约，但约定扣款时点尚未到来。",
                  "cross_merchant": "同一园区两家独立商户各自编号，咖啡店已结算，目标书店还在受理中。"},
        "validation": {"settled": "停车场补缴核查：闸机离线，联网补传后银行已确认入账。",
                       "accepted": "展会报名付款核查：订单登记成功，银行异步清算尚未完成。",
                       "cross_merchant": "两家展馆票务商采用相同流水编号，无关票务商已结算，目标票务商仅受理。"},
        "holdout": {"settled": "租赁押金核查：原授权撤销后重新支付，本次有效记录已有最终结算凭证。",
                    "accepted": "公益捐款核查：聚合渠道完成签名验收，但收款机构账簿尚未取得清算凭证。",
                    "cross_merchant": "市集两家摊位独立收款且流水号相同，首条服饰摊已结算，目标陶艺摊仅取得受理回执。"},
        "fault_public": {"cross_merchant": "公开故障题：两个教育机构独立收款且复用编号，首条无关机构已结算，目标机构仍在等待清算。"},
    }
    context = contexts[split][category]
    records = [{"record_id": f"EV-v2-{split}-{index}-target", "order_id": order,
                "merchant_id": merchant, "status": status, "amount_cents": 10000 + 137 * index}]
    if category == "cross_merchant":
        records[0]["status"] = "ACCEPTED"
        records.insert(0, {"record_id": f"EV-v2-{split}-{index}-other", "order_id": order,
                           "merchant_id": merchant + "-OTHER", "status": "SETTLED", "amount_cents": 3800})
    target = records[-1]
    target["business_context"] = context
    target["channel_receipt"] = f"receipt-v2-{split}-{index}"
    target["settlement_evidence"] = (f"ledger-v2-{split}-{index}" if target["status"] == "SETTLED" else None)
    return {"id": f"v2-{split}-{index:02}", "event_group": f"event-v2-{split}-{index}", "split": split,
            "category": category, "prompt": f"核查商户 {merchant} 的订单 {order} 当前是否到账。请调用工具后按 Skill 指定的 JSON 格式回答。",
            "records": records, "expected": {"order_id": order, "merchant_id": merchant,
              "paid": target["status"] == "SETTLED", "evidence_ids": [target["record_id"]]}}


def prepare_data():
    sizes = {"train": 3, "validation": 3, "holdout": 3, "fault_public": 1}
    cats = ["settled", "accepted", "cross_merchant"]
    data = {s: [make_case(s, i, "cross_merchant" if s == "fault_public" else cats[i % 3]) for i in range(n)] for s, n in sizes.items()}
    for split, cases in data.items():
        save(DATA_DIR / f"{split}.json", cases)
    groups = [c["event_group"] for cases in data.values() for c in cases]
    assert len(set(groups)) == len(groups)
    # 去掉实体标识、金额、流水号后仍检查内容；不是只比较不同的 ID。
    normalized = [sha(json.dumps([{k: v for k, v in r.items() if k not in
                  {"record_id", "order_id", "merchant_id", "amount_cents", "channel_receipt", "settlement_evidence"}}
                  for r in c["records"]], ensure_ascii=False, sort_keys=True))
                  for cases in data.values() for c in cases]
    assert len(set(normalized)) == len(normalized), "分片存在归一化重复材料"
    save(DATA_DIR / "partition-check.json", {"event_groups_disjoint": True,
         "normalized_records_unique": True, "checked_examples": len(normalized),
         "scope": "三类任务共有相同字段，但使用不同业务事件和背景材料；只能观察机制，不能证明生产泛化"})
    return data


def static_check(body):
    full = FRONT + body
    reasons = []
    if not body.strip(): reasons.append("正文为空")
    if len(full) > BUDGET["max_full_chars"]: reasons.append("完整文件超过字符上限")
    if len(body) > len(BASELINE) * (1 + BUDGET["max_growth"]): reasons.append("正文增长超过 20%")
    if body.lstrip().startswith("---"): reasons.append("候选不得改写文件头")
    if "${" in body or "`!" in body: reasons.append("不允许变量或行内执行改变加载字节")
    # 固定文件头只组装一次；实际 Hermes 预载另行核验完整文件。
    if not full.startswith("---\nname: payment-check\ndescription:"): reasons.append("文件头不完整")
    return {"passed": not reasons, "reasons": reasons, "full_chars": len(full), "body_chars": len(body),
            "body_growth": len(body) / len(BASELINE) - 1}


def score(case, execution):
    reasons = []
    try:
        text = execution["result"]["final_response"].strip()
        answer = json.loads(text)
        if not isinstance(answer, dict):
            raise ValueError("答案必须是 JSON 对象")
    except (KeyError, ValueError, AttributeError):
        return {"score": 0.0, "hard_pass": False, "reasons": ["最终输出不是合法 JSON"], "parsed": None}
    expected = case["expected"]
    checks = {k: answer.get(k) == v for k, v in expected.items()}
    if type(answer.get("paid")) is not bool:
        checks["paid"] = False
    checks["tool_evidence"] = bool(execution.get("tool_trace")) and all(
        e in {r["record_id"] for trace in execution.get("tool_trace", []) for r in trace["result"]["records"]}
        for e in expected["evidence_ids"])
    for key, good in checks.items():
        if not good: reasons.append(f"{key} 不满足业务条件；应为 {expected.get(key, '真实查询返回的证据')}")
    checks["reason_present"] = isinstance(answer.get("reason"), str) and bool(answer["reason"].strip())
    if not checks["reason_present"]:
        reasons.append("缺少可读理由")
    hard = all(checks.values())
    return {"score": sum(checks.values()) / len(checks), "hard_pass": hard,
            "reasons": reasons or ["硬性字段与真实查询证据均通过"], "parsed": answer}


class Harness:
    def __init__(self, out):
        self.out = out
        self.counts = {"search": 0, "audit_validation": 0, "holdout": 0, "fault": 0, "probe": 0}
        self.search_start = time.monotonic()
        self.deadline = self.search_start + BUDGET["search_seconds"]
        self.events = []
        self.fatal_error = None

    def log(self, kind, **kwargs):
        row = {"event": kind, "monotonic": time.monotonic(), **kwargs}
        self.events.append(row)
        with (self.out / "events.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def candidate_dir(self, body):
        full = FRONT + body
        folder = self.out / "candidates" / sha(full)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "references").mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(full)
        (folder / "references/schema.md").write_text(REFERENCE)
        save(folder / "manifest.json", {"skill_sha256": sha(full), "body_sha256": sha(body),
              "files": {"SKILL.md": sha(full), "references/schema.md": sha(REFERENCE)}, "static": static_check(body)})
        return folder

    def execute(self, case, body, phase, probe=False):
        maximum = {"search": BUDGET["search_metric_calls"] - BUDGET["audit_validation_executions"], "audit_validation": BUDGET["audit_validation_executions"],
                   "holdout": BUDGET["holdout_executions"], "fault": BUDGET["fault_executions"],
                   "probe": BUDGET["probe_executions"]}[phase]
        if self.counts[phase] >= maximum:
            raise RuntimeError(f"{phase} 执行预算耗尽")
        remaining = self.deadline - time.monotonic() if phase == "search" else BUDGET["max_case_seconds"]
        if remaining <= 0: raise RuntimeError("search_wallclock_budget")
        self.counts[phase] += 1
        directory = self.candidate_dir(body)
        seq = sum(self.counts.values())
        run = self.out / "executions" / f"{seq:03}-{phase}-{case['id']}"
        home = run / "hermes-home"
        home.mkdir(parents=True)
        shutil.copytree(directory, home / "skills/payment-check")
        # 仅关闭本题隔离实例的渐进式工具披露，使唯一只读工具直接可见。
        (home / "config.yaml").write_text("memory:\n  memory_enabled: false\n  user_profile_enabled: false\nskills:\n  template_vars: false\n  inline_shell: false\nagent:\n  max_iterations: 5\ntools:\n  tool_search:\n    enabled: false\n")
        response = run / "response.json"
        request = {"hermes_root": str(HERMES), "hermes_home": str(home), "case_id": case["id"],
                   "skill_sha256": sha(FRONT + body), "reference_sha256": sha(REFERENCE), "prompt": case["prompt"],
                   "records": case["records"], "response_path": str(response)}
        save(run / "request.json", request)
        check = static_check(body)
        if not check["passed"]:
            result = {"error": "static_rejected", "static": check, "llm_called": False}
        else:
            environment = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy") if k in os.environ}
            environment.update({"HERMES_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
            if os.environ.get("GLM_API_KEY"): environment["GLM_API_KEY"] = os.environ["GLM_API_KEY"]
            command = [str(WORKER_PYTHON), str(HERE / "hermes_worker.py"), str(run / "request.json")]
            if probe: command.append("--probe")
            start = time.monotonic()
            try:
                completed = subprocess.run(command, env=environment, cwd=run, text=True, capture_output=True,
                                           timeout=min(BUDGET["max_case_seconds"], max(1, remaining)))
                # API key 不进入命令、请求文件或日志；异常输出仍显式脱敏。
                key = os.environ.get("GLM_API_KEY", "")
                for name, content in [("stdout.log", completed.stdout), ("stderr.log", completed.stderr)]:
                    (run / name).write_text(content.replace(key, "[REDACTED]") if key else content)
                result = json.loads(response.read_text()) if response.exists() else {"error": f"worker_exit_{completed.returncode}", "llm_called": None}
                if completed.returncode:
                    result["error"] = f"worker_exit_{completed.returncode}"
                if isinstance(result.get("result"), dict) and (result["result"].get("error") or result["result"].get("failed") or result["result"].get("completed") is False):
                    result["error"] = "hermes_runtime_error"
            except subprocess.TimeoutExpired:
                result = json.loads(response.read_text()) if response.exists() else {"llm_called": None}
                result["error"] = "worker_timeout"
            result["wall_seconds"] = time.monotonic() - start
        rating = {"score": 0.0, "hard_pass": False, "reasons": [result.get("error", "仅预载探针")]} if probe or "error" in result else score(case, result)
        save(run / "rating.json", rating)
        combined = {"case_id": case["id"], "category": case["category"], "split": case["split"],
                    "phase": phase, "skill_sha256": sha(FRONT + body), "static": check,
                    "execution": result, "rating": rating, "evidence_path": str(run.relative_to(self.out))}
        self.log("evaluation", **{k: v for k, v in combined.items() if k != "execution"})
        return combined


class Adapter:
    propose_new_texts = None  # 沿用官方反思提案器，课程不实现搜索算法。
    def __init__(self, harness): self.harness = harness

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        rows = [self.harness.execute(case, candidate["skill_body"], "search") for case in batch]
        errors = [r for r in rows if r["execution"].get("error") not in (None, "static_rejected")]
        if errors:
            self.harness.fatal_error = "执行器或网络故障，不把基础设施失败交给反思器优化：" + errors[0]["execution"]["error"]
            raise RuntimeError(self.harness.fatal_error)
        traces = [{"case": case, "result": row} for case, row in zip(batch, rows)]
        return EvaluationBatch(outputs=rows, scores=[r["rating"]["score"] for r in rows],
                               trajectories=traces if capture_traces else None)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        result = []
        for trace in eval_batch.trajectories or []:
            case, row = trace["case"], trace["result"]
            assert case["split"] == "train", "反思材料只能来自训练"
            result.append({"Inputs": case["prompt"], "Generated Outputs": row["execution"].get("result", {}),
                           "Feedback": row["rating"], "Tool trace": row["execution"].get("tool_trace", []),
                           "Constraints": REFLECTION_CONTRACT + f"原正文{len(BASELINE)}字符，硬上限{int(len(BASELINE)*1.2)}字符。"})
        self.harness.log("reflection_feedback", parent_sha256=sha(FRONT + candidate["skill_body"]), feedback=result)
        return {name: result for name in components_to_update}


class Observer:
    def __init__(self, harness): self.harness, self.proposals, self.bodies = harness, 0, []
    def on_candidate_selected(self, event): self.harness.log("parent_selected", **event)
    def on_proposal_start(self, event): self.harness.log("proposal_started", **event)
    def on_proposal_end(self, event):
        self.proposals += 1
        self.bodies.append(event["new_instructions"]["skill_body"])
        self.harness.candidate_dir(self.bodies[-1])
        self.harness.log("proposal_finished", **event)
    def on_candidate_accepted(self, event): self.harness.log("search_candidate_accepted", **event)
    def on_candidate_rejected(self, event): self.harness.log("search_candidate_rejected", **event)


def reflection_lm(harness, prompt):
    remaining = harness.deadline - time.monotonic()
    if remaining <= 0: raise RuntimeError("search_wallclock_budget")
    body = json.dumps({"model": "glm-5.2", "messages": [{"role": "system", "content": REFLECTION_CONTRACT}, {"role": "user", "content": prompt}],
                       "temperature": 0.6, "max_tokens": 1400, "thinking": {"type": "disabled"}}).encode()
    req = urllib.request.Request("https://open.bigmodel.cn/api/paas/v4/chat/completions", data=body,
          headers={"Authorization": "Bearer " + os.environ["GLM_API_KEY"], "Content-Type": "application/json"})
    start = time.monotonic()
    def timeout_handler(signum, frame):
        raise TimeoutError("反思请求墙钟预算到期")
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, min(BUDGET["max_case_seconds"], remaining))
    try:
        for attempt in range(1, 4):
            harness.log("reflection_model_request", role="reflection", model="glm-5.2", attempt=attempt)
            try:
                with urllib.request.urlopen(req, timeout=min(BUDGET["max_case_seconds"], remaining)) as response:
                    result = json.load(response)
                break
            except urllib.error.HTTPError as error:
                retryable = error.code in (429, 502, 503, 504) and attempt < 3
                harness.log("reflection_http_error", status=error.code, attempt=attempt, retryable=retryable)
                if not retryable:
                    raise
                delay = 2 ** attempt
                harness.log("reflection_backoff", seconds=delay, reason=f"HTTP {error.code}")
                time.sleep(delay)
        text = result["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("反思响应没有非空文字")
    except Exception as error:
        harness.fatal_error = f"reflection_{type(error).__name__}: {error}"
        harness.log("reflection_model_error", error=harness.fatal_error, elapsed_seconds=time.monotonic() - start)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    harness.log("reflection_model_response", role="reflection", model="glm-5.2", prompt=prompt,
                response=text, system_contract=REFLECTION_CONTRACT, usage=result.get("usage"), latency_seconds=time.monotonic() - start,
                billed_cost=None, cost_status="未取得账单，不能用零代替")
    return text


def summarize(rows):
    categories = sorted({r["category"] for r in rows})
    return {"n": len(rows), "mean": sum(r["rating"]["score"] for r in rows) / len(rows) if rows else None,
            "all_hard_pass": bool(rows) and all(r["rating"]["hard_pass"] for r in rows),
            "by_type": {c: sum(r["rating"]["score"] for r in rows if r["category"] == c) / sum(r["category"] == c for r in rows) for c in categories}}


def adoption(baseline, candidate, static, required_count):
    b, c = summarize(baseline), summarize(candidate)
    reasons = []
    baseline_versions = {r["skill_sha256"] for r in baseline if r.get("skill_sha256")}
    candidate_versions = {r["skill_sha256"] for r in candidate if r.get("skill_sha256")}
    if baseline_versions and baseline_versions == candidate_versions:
        reasons.append("候选与基线为同一版本，没有新版本可采用")
    if len(baseline) != required_count or len(candidate) != required_count: reasons.append("检验样本不足")
    if any(r["execution"].get("error") or not r["execution"].get("completed") for r in baseline + candidate):
        reasons.append("两侧包含未完成执行或基础设施故障，本轮数据不足")
    if not static["passed"]: reasons.append("静态检查失败")
    if not c["all_hard_pass"]: reasons.append("硬性业务条件失败")
    if b["mean"] is None or c["mean"] is None: reasons.append("没有可比较分数")
    else:
        if c["mean"] < b["mean"]: reasons.append("总体均分下降")
        if c["mean"] - b["mean"] < BUDGET["min_holdout_gain"]: reasons.append("未达到预定最小收益 0.02")
    for category, value in b["by_type"].items():
        if c["by_type"].get(category, -1) < value: reasons.append(f"关键类型 {category} 退步")
    tokens = [(r["execution"].get("usage") or {}).get("session_total_tokens") for r in candidate]
    if not tokens or any(t is None for t in tokens): reasons.append("缺少成本代理记录")
    elif sum(tokens) / len(tokens) > BUDGET["holdout_mean_tokens_limit"]: reasons.append("平均 token 超上限")
    if any(r["execution"].get("wall_seconds", BUDGET["holdout_case_seconds_limit"] + 1) > BUDGET["holdout_case_seconds_limit"] for r in candidate): reasons.append("时延超上限")
    return {"adopt": not reasons, "reasons": reasons, "baseline": b, "candidate": c,
            "billed_cost": None, "cost_note": "实际费用以供应商账单为准；本练习用真实 token 数限制成本代理，不臆造金额"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "verify", "run", "fault", "all"])
    args = parser.parse_args()
    data = prepare_data()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out = OUTPUT_ROOT / run_id
    out.mkdir(parents=True)
    harness = Harness(out)
    base_dir = harness.candidate_dir(BASELINE)
    pointer = {"version": "baseline", "skill_sha256": sha(FRONT + BASELINE), "directory": str(base_dir)}
    save(out / "service-version.json", pointer)
    import gepa
    metadata = {"mode": args.mode, "key_present": bool(os.environ.get("GLM_API_KEY")),
                "optimizer": "official gepa.optimize", "gepa_version": importlib.metadata.version("gepa"),
                "gepa_api_signature": str(inspect.signature(gepa.optimize)), "gepa_module": str(Path(gepa.__file__).resolve()),
                "hermes_commit": subprocess.check_output(["git", "-C", str(HERMES), "rev-parse", "HEAD"], text=True).strip(),
                "budget": BUDGET, "data_version": DATA_VERSION, "data_directory": str(DATA_DIR), "split_counts": {k: len(v) for k, v in data.items()},
                "baseline_sha256": pointer["skill_sha256"],
                "data_sha256": {split: sha((DATA_DIR / f"{split}.json").read_bytes()) for split in data},
                "selection_rule_before_execution": "官方有非基线赢家则送检该赢家；否则选静态合格且已经真实训练评估的最短GEPA提案做教学审计；不看留出，不冒充官方接受",
                "search_budget_scope": "30次包含基线开发、官方搜索和3次候选完整验证；官方搜索预留这3次",
                 "official_gepa_called": False,
                "completed_hermes_tasks": 0, "holdout_exposed_to_optimizer": False, "fault_is_separate": True,
                "source_policy": "只读 Hermes 源目录；每题独立进程与 HERMES_HOME；凭据仅从 GLM_API_KEY 读取"}
    save(out / "manifest.json", metadata)
    shutil.copytree(DATA_DIR, out / "data")
    (out / "source").mkdir()
    for name in ("run_exercise.py", "hermes_worker.py"):
        shutil.copy2(HERE / name, out / "source" / name)
    save(out / "source-sha256.json", {name: sha((HERE / name).read_bytes()) for name in ("run_exercise.py", "hermes_worker.py")})
    try:
        if args.mode == "prepare":
            metadata["status"] = "prepared"
        if args.mode in ("verify", "all"):
            probe = harness.execute(data["train"][0], BASELINE, "probe", probe=True)
            metadata["probe"] = probe
            metadata["status"] = "verified" if probe["execution"].get("probe_only") else "probe_failed"
            if metadata["status"] == "probe_failed":
                raise RuntimeError("预载与工具白名单核验未通过，停止运行")
            # 评分器真实函数自测，明确这是构造的函数输入，不是模型运行。
            fake = {"result": {"final_response": json.dumps({**data["train"][0]["expected"], "reason": "字段自测"})},
                    "tool_trace": [{"result": {"records": data["train"][0]["records"]}}]}
            assert score(data["train"][0], fake)["hard_pass"]
            fake["result"]["final_response"] = "```json\n" + json.dumps({**data["train"][0]["expected"], "reason": "字段自测"}) + "\n```"
            assert not score(data["train"][0], fake)["hard_pass"]
            fake["result"]["final_response"] = "{}"
            assert not score(data["train"][0], fake)["hard_pass"]
            fake["result"]["final_response"] = "[]"
            assert not score(data["train"][0], fake)["hard_pass"]
            good = {"category": "settled", "rating": {"score": 1, "hard_pass": True},
                    "execution": {"completed": True, "wall_seconds": 1, "usage": {"session_total_tokens": 100}}}
            business_bad = {**good, "rating": {"score": 0.5, "hard_pass": False}}
            infrastructure_bad = {**business_bad, "execution": {"error": "network_failure", "completed": False}}
            assert adoption([business_bad], [good], static_check(BASELINE), 1)["adopt"]
            assert not adoption([infrastructure_bad], [good], static_check(BASELINE), 1)["adopt"]
            assert not adoption([good], [good], static_check(BASELINE), 1)["adopt"]
            assert not adoption([good], [business_bad], static_check(BASELINE), 1)["adopt"]
            metadata["scorer_function_tests"] = "正确字段通过，缺失字段/JSON数组/代码围栏拒绝；相同分数不算最小收益；基础设施失败不算业务改善；未调用模型"
            metadata["verify_completed"] = True
        if args.mode in ("run", "fault", "all") and not os.environ.get("GLM_API_KEY"):
            metadata.update(status="blocked_missing_glm_api_key", stop_reason="缺少 GLM_API_KEY；没有启动搜索或故障注入",
                            normal_run="not_started", fault_run="not_started", adoption="数据不足，保留基线")
        elif args.mode in ("run", "fault", "all"):
            if args.mode in ("run", "all"):
                exposure_file = HERE / "output" / f"holdout-exposure-{DATA_VERSION}.json"
                if exposure_file.exists():
                    raise RuntimeError("这份留出已经使用；请另备未参与修订的业务事件后再开新轮，不重复宣布独立检验")
                warmup = [harness.execute(c, BASELINE, "search") for c in data["train"] + data["validation"]]
                save(out / "baseline-development.json", warmup)
                if any("error" in r["execution"] for r in warmup):
                    raise RuntimeError("基线执行器未通过，停止搜索")
                observer = Observer(harness)
                from gepa.utils.stop_condition import NoImprovementStopper
                stall = NoImprovementStopper(BUDGET["max_new_proposals"])
                stop_reasons = []
                def stopper(state):
                    if harness.fatal_error: stop_reasons.append("reflection_infrastructure_failure")
                    if observer.proposals >= BUDGET["max_new_proposals"]: stop_reasons.append("max_new_proposals")
                    if time.monotonic() >= harness.deadline: stop_reasons.append("search_wallclock_budget")
                    if harness.counts["search"] >= BUDGET["search_metric_calls"] - BUDGET["audit_validation_executions"]: stop_reasons.append("search_metric_budget")
                    if stall(state): stop_reasons.append("validation_stagnation")
                    return bool(stop_reasons)
                metadata["official_gepa_called"] = True
                harness.log("official_gepa_started", entry="gepa.optimize", version=metadata["gepa_version"],
                            module=metadata["gepa_module"], train_count=len(data["train"]),
                            validation_count=len(data["validation"]), holdout_exposed=False)
                result = gepa.optimize(seed_candidate={"skill_body": BASELINE}, trainset=data["train"],
                    valset=data["validation"], adapter=Adapter(harness), reflection_lm=lambda p: reflection_lm(harness, p),
                    max_metric_calls=BUDGET["search_metric_calls"] - len(warmup) - BUDGET["audit_validation_executions"], reflection_minibatch_size=3, stop_callbacks=stopper,
                    callbacks=[observer], run_dir=str(out / "gepa"), seed=20, raise_on_exception=True,
                    use_merge=False, skip_perfect_score=False)
                save(out / "gepa-result.json", result.to_dict())
                harness.log("official_gepa_returned", best_idx=result.best_idx, proposals=observer.proposals)
                if harness.fatal_error:
                    raise RuntimeError(harness.fatal_error)
                official_winner = result.best_candidate["skill_body"]
                save(out / "official-winner.json", {"身份": "官方赢家", "best_idx": result.best_idx,
                     "skill_sha256": sha(FRONT + official_winner), "is_baseline": official_winner == BASELINE,
                     "directory": str(harness.candidate_dir(official_winner))})
                winner = official_winner
                selection_role = "official_search_winner"
                selection_reason = "官方搜索选中的新候选，继续独立检验"
                if official_winner == BASELINE:
                    # 选择规则在读取留出前固定：只能用静态合格且已经真实训练评估的提案。
                    evaluated_hashes = {e["skill_sha256"] for e in harness.events
                        if e["event"] == "evaluation" and e["phase"] == "search"
                        and e["split"] == "train" and e["static"]["passed"]}
                    eligible = [body for body in observer.bodies if body != BASELINE
                        and static_check(body)["passed"] and sha(FRONT + body) in evaluated_hashes]
                    if not eligible:
                        raise RuntimeError("没有静态合格且真实执行过的非基线提案；保留失败，不伪造审计候选")
                    winner = min(eligible, key=lambda body: (len(body), sha(body)))
                    selection_role = "teaching_audit_of_gepa_proposal"
                    selection_reason = "官方仍保留基线；按预先规则挑选真实训练过的最短合格提案做教学审计，不冒充官方赢家"
                final_dir = harness.candidate_dir(winner)
                assert (final_dir / "SKILL.md").read_bytes() == (FRONT + winner).encode()
                selection = {"official_best_idx": result.best_idx, "official_skill_sha256": sha(FRONT + official_winner),
                    "skill_sha256": sha(FRONT + winner), "directory": str(final_dir), "static": static_check(winner),
                    "身份": "教学审计提案" if selection_role == "teaching_audit_of_gepa_proposal" else "官方赢家", "role": selection_role, "reason": selection_reason, "holdout_used_for_selection": False}
                save(out / "selected-candidate.json", selection)
                harness.log("candidate_selected_for_independent_audit", **selection)
                validation_rows = [harness.execute(c, winner, "audit_validation") for c in data["validation"]]
                baseline_validation = [r for r in warmup if r["split"] == "validation"]
                save(out / "candidate-development.json", {"role": selection_role,
                     "baseline_validation": baseline_validation, "candidate_validation": validation_rows,
                     "baseline_summary": summarize(baseline_validation), "candidate_summary": summarize(validation_rows)})
                if any(r["execution"].get("error") for r in validation_rows):
                    raise RuntimeError("候选开发验证包含执行故障；停止而不把故障当作业务分数")
                # 搜索到此结束；留出只在候选已确定后使用一次。
                if static_check(winner)["passed"]:
                    save(exposure_file, {"first_run": str(out.relative_to(PROJECT)),
                        "data_sha256": sha((DATA_DIR / "holdout.json").read_bytes()),
                        "reason": "留出执行从此刻开始，失败或中断也算已暴露，不能回收为未见数据"})
                    baseline_holdout = [harness.execute(c, BASELINE, "holdout") for c in data["holdout"]]
                    candidate_holdout = [harness.execute(c, winner, "holdout") for c in data["holdout"]]
                else:
                    baseline_holdout, candidate_holdout = [], []
                decision = adoption(baseline_holdout, candidate_holdout, static_check(winner), len(data["holdout"]))
                save(out / "holdout-comparison.json", {"baseline": baseline_holdout, "candidate": candidate_holdout})
                decision["身份"] = "最终采用版本"
                decision["final_adopted_sha256"] = sha(FRONT + BASELINE)
                decision["final_adopted_version"] = "baseline"
                decision["release_performed"] = False
                save(out / "adoption.json", decision)
                metadata.update(status="completed", normal_run="executed", stop_reason=stop_reasons or ["official_metric_limit"],
                                proposal_count=observer.proposals, adoption=decision,
                                next_step="通过仅进入既有发布流程；本脚本保持测试服务基线指向，不发布")
            if args.mode in ("fault", "all"):
                baseline_fault = [harness.execute(c, BASELINE, "fault") for c in data["fault_public"]]
                bad_fault = [harness.execute(c, BAD, "fault") for c in data["fault_public"]]
                decision = adoption(baseline_fault, bad_fault, static_check(BAD), len(data["fault_public"]))
                fault_complete = all(r["execution"].get("completed") and not r["execution"].get("error") for r in baseline_fault + bad_fault)
                regression = fault_complete and summarize(bad_fault)["mean"] < summarize(baseline_fault)["mean"]
                save(out / "fault-injection.json", {"baseline": baseline_fault, "fixed_bad": bad_fault,
                      "adoption": decision, "real_regression_observed": regression,
                      "result": ("检验执行不完整，不能判断是否退步" if not fault_complete else "真实退步触发拒绝" if regression and not decision["adopt"] else "本次未触发预期退步，不改分数"),
                      "source": "固定坏候选，不来自 GEPA 搜索；公开题与独立留出分开"})
                metadata.update(fault_run="executed", fault_regression_observed=regression)
                if args.mode == "fault": metadata["status"] = "completed"
    except Exception as error:
        metadata.update(status="incomplete", error_type=type(error).__name__, stop_reason=str(error),
                        adoption="运行或检验未完成，保留基线")
    metadata["counts"] = harness.counts
    metadata["total_development_scoring_slots"] = harness.counts["search"] + harness.counts["audit_validation"]
    assert metadata["total_development_scoring_slots"] <= BUDGET["search_metric_calls"]
    metadata["baseline_hash_unchanged"] = sha((base_dir / "SKILL.md").read_bytes()) == pointer["skill_sha256"]
    metadata["service_version_unchanged"] = json.loads((out / "service-version.json").read_text()) == pointer
    metadata["service_version_scope"] = "本地指向记录，不是正在运行的在线服务；没有执行发布"
    if args.mode in ("run", "fault", "all"):
        probe = harness.execute(data["train"][0], BASELINE, "probe", probe=True)
        metadata["baseline_pointer_preload_verified"] = bool(probe["execution"].get("probe_only"))
        save(out / "baseline-pointer-check.json", probe)
    metadata["elapsed_seconds"] = time.monotonic() - harness.search_start
    responses = [json.loads(p.read_text()) for p in (out / "executions").glob("*/response.json")]
    metadata["completed_hermes_tasks"] = sum(bool(r.get("completed")) for r in responses)
    api_counts = [(r.get("usage") or {}).get("session_api_calls") for r in responses if not r.get("probe_only")]
    metadata["hermes_api_calls_reported"] = sum(n for n in api_counts if n is not None)
    metadata["hermes_http_requests_observed"] = sum(r.get("api_requests_attempted", 0) for r in responses)
    metadata["hermes_http_responses_observed"] = sum(r.get("api_responses_received", 0) for r in responses)
    metadata["tasks_with_unknown_api_calls"] = sum(n is None for n in api_counts)
    metadata["reflection_requests_attempted"] = sum(e["event"] == "reflection_model_request" for e in harness.events)
    metadata["reflection_responses_received"] = sum(e["event"] == "reflection_model_response" for e in harness.events)
    metadata["billing_note"] = "Hermes 报告调用数与 HTTP 观察器计数分别保存，失败请求也可能计费；实际账单未知。中断任务不记作零费用。"
    save(out / "manifest.json", metadata)
    save(OUTPUT_ROOT / "latest.json", {"run": str(out.relative_to(PROJECT)), "status": metadata["status"]})
    print(json.dumps({"output": str(out), "status": metadata["status"], "counts": harness.counts}, ensure_ascii=False))
    if metadata["status"] in ("incomplete", "probe_failed", "blocked_missing_glm_api_key"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
