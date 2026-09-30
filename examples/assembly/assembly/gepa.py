"""GEPA 式反馈精炼：真实模型生成、回放和评分，候选统一交给业务评测。

这是教学用轻量实现，不是 GEPA/DSPy 全算法复现。保留自然语言反馈、
逐轮精炼、候选池、基线、预算和独立保留集；未实现遗传交叉与 Pareto 选择。

默认材料故意保留一组错误的历史训练标签：把 processing 计入已付金额。
此设置在任何模型请求前落盘，展示“优化错误指标”的风险；保留集按真实
success 口径核算。分数全部来自实际回复，绝不为预期演示结果改分或补跑。
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import requests
import yaml

from . import config
from .contracts import CandidateBundle, sha256_text, tree_hashes, write_json
from .lifecycle.skills import create_candidate


FIELDS = ("paid_success", "processing", "remaining", "paid_off")
SCORE_POLICY = {
    "version": "lifecycle-gepa-v1",
    "deterministic_weight": 0.8,
    "judge_weight": 0.2,
    "fields": list(FIELDS),
    "description": "四个结构化字段逐项等值核对；LLM 对照本任务给定标签评判结论是否一致、清楚。",
    "caveat": "离线评分只反映给定标签；默认训练标签故意有偏，不能代表真实付款正确率。",
}


def demo_task_feed() -> dict[str, Any]:
    """返回固定的有偏训练教学材料；不改只读 scenarios 文件。"""
    orders = json.loads((config.SCENARIOS_DIR / "orders.json").read_text(encoding="utf-8"))
    train = []
    for case_id in ("ORD-A1", "ORD-A2"):
        order = copy.deepcopy(orders[case_id])
        gross = sum(t["amount"] for t in order["transactions"]
                    if t["status"] in {"success", "processing"})
        processing = sum(t["amount"] for t in order["transactions"] if t["status"] == "processing")
        train.append({
            "case_id": case_id, "prompt": f"核实订单 {case_id} 是否付清，汇总金额并给结论。",
            "order": order,
            "expected": {"paid_success": gross, "processing": processing,
                         "remaining": max(0, order["amount_due"] - gross),
                         "paid_off": gross >= order["amount_due"]},
        })
    return {
        "description": "受控教学：训练来自 scenarios 的订单快照，但故意使用把 processing 计入付款的错误历史标签；独立保留集使用正确标签。不是自然发生的泛化结论。",
        "train": train,
        "holdout": [
            {"case_id": "HOLDOUT-MIXED-17", "prompt": "核实订单 HOLDOUT-MIXED-17 是否付清，汇总金额并给结论。",
             "order": {"amount_due": 1200, "transactions": [
                 {"txn_id": "H-S17", "amount": 700, "status": "success"},
                 {"txn_id": "H-P17", "amount": 500, "status": "processing"}]},
             "expected": {"paid_success": 700, "processing": 500, "remaining": 500, "paid_off": False}},
            {"case_id": "HOLDOUT-ACCEPTED-23", "prompt": "核实订单 HOLDOUT-ACCEPTED-23 是否付清，汇总金额并给结论。",
             "order": {"amount_due": 650, "transactions": [
                 {"txn_id": "H-P23", "amount": 650, "status": "processing", "channel_acceptance": "success"}]},
             "expected": {"paid_success": 0, "processing": 650, "remaining": 650, "paid_off": False}},
        ],
    }


def _json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("模型没有返回完整 JSON 对象；本次不补造回复")


class _Model:
    def __init__(self, run_dir: Path):
        # 不调用 config.api_key() 的文件回退；调用方须先 source 环境文件。
        self.key = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")
        if not self.key:
            raise RuntimeError("请先把 GLM_API_KEY（或 BIGMODEL_API_KEY）加载到环境变量")
        self.run_dir, self.calls = run_dir, []
        self.session = requests.Session()
        self.session.trust_env = False

    def call(self, role: str, messages: list[dict[str, str]], max_tokens: int = 2400) -> dict[str, Any]:
        record: dict[str, Any] = {"call": len(self.calls) + 1, "role": role,
                                  "model": "glm-5.2", "messages": messages, "started_at": time.time()}
        self.calls.append(record)
        path = self.run_dir / "calls" / f"{record['call']:03d}-{role}.json"
        write_json(path, record)
        started = time.monotonic()
        try:
            response = self.session.post(
                f"{config.BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json={"model": "glm-5.2", "messages": messages, "temperature": 0.0,
                      "max_tokens": max_tokens, "thinking": {"type": "disabled"}},
                timeout=(10, 180),
            )
            response.raise_for_status()
            body = response.json()
            record["request_id"] = body.get("id")
            record["response_model"] = body.get("model")
            record["usage"] = body.get("usage")
            record["finish_reason"] = body["choices"][0].get("finish_reason")
            raw = body["choices"][0]["message"].get("content") or ""
            record["response"] = raw.replace(self.key, "[密钥已隐藏]")
            parsed = _json_object(record["response"])
            record["status"] = "ok"
            return parsed
        except Exception as exc:
            # 不记录异常全文，以免 HTTP 对象携带鉴权内容。
            record["status"], record["error_type"] = "failed", type(exc).__name__
            raise
        finally:
            record["elapsed_sec"] = round(time.monotonic() - started, 3)
            write_json(path, record)


def _validate_feed(task_feed: Any) -> dict[str, Any]:
    feed = copy.deepcopy(demo_task_feed() if task_feed is None else task_feed)
    if not isinstance(feed, dict):
        raise ValueError("task_feed 须为含 train、holdout 的字典，或 None 使用教学材料")
    seen = set()
    for split in ("train", "holdout"):
        tasks = feed.get(split)
        if not isinstance(tasks, list) or not tasks:
            raise ValueError(f"{split} 必须是非空任务列表")
        for task in tasks:
            case_id = task.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in seen:
                raise ValueError("训练与保留集 case_id 必须非空且互不重复")
            seen.add(case_id)
            if not isinstance(task.get("prompt"), str) or not isinstance(task.get("order"), dict):
                raise ValueError("每个任务必须含 prompt 和 order")
            expected = task.get("expected", {})
            if set(expected) != set(FIELDS) or not isinstance(expected["paid_off"], bool):
                raise ValueError("expected 必须含 paid_success/processing/remaining 数值和 paid_off 布尔值")
            for field in FIELDS[:3]:
                if isinstance(expected[field], bool) or not isinstance(expected[field], (float, int)) or not math.isfinite(expected[field]):
                    raise ValueError(f"expected.{field} 必须是有限数值")
    return feed


def _convergence(value: float | dict[str, Any] | None) -> dict[str, Any]:
    settings = {"min_improvement": 0.05, "patience": 1, "target_score": None}
    if isinstance(value, dict):
        if set(value) - set(settings):
            raise ValueError("convergence 只支持 min_improvement、patience、target_score")
        settings.update(value)
    elif value is not None:
        settings["min_improvement"] = value
    delta, patience, target = (settings[k] for k in ("min_improvement", "patience", "target_score"))
    if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(delta) or not 0 <= delta <= 1:
        raise ValueError("min_improvement 必须在 [0, 1]")
    if isinstance(patience, bool) or not isinstance(patience, int) or patience < 1:
        raise ValueError("patience 必须为正整数")
    if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not 0 <= target <= 1):
        raise ValueError("target_score 必须为空或位于 [0, 1]")
    return settings


def _index_results(value: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    results = value.get("results")
    if not isinstance(results, list) or len(results) != len(tasks) or any(not isinstance(r, dict) for r in results):
        raise ValueError("模型结果数量或结构与任务组不一致")
    indexed = {r.get("case_id"): r for r in results}
    if set(indexed) != {t["case_id"] for t in tasks}:
        raise ValueError("模型返回的 case_id 缺失、重复或越界")
    return indexed


def _evaluate(model: _Model, candidate: CandidateBundle, tasks: list[dict[str, Any]], split: str) -> dict[str, Any]:
    skill = (Path(candidate.candidate_dir) / "SKILL.md").read_text(encoding="utf-8")
    visible_tasks = [{k: t[k] for k in ("case_id", "prompt", "order")} for t in tasks]
    rollout = model.call(f"{split}-rollout", [
        {"role": "system", "content": "按以下技能指令处理用户提供的查询快照。严格执行技能中的金额口径。\n" + skill},
        {"role": "user", "content": "订单号查询已经完成，以下 order 是只读查询回执，请分别汇总并给结论。仅返回 JSON："
         '{"results":[{"case_id":"...","paid_success":数值,"processing":数值,"remaining":数值,"paid_off":布尔值,"answer":"中文结论"}]}。\n'
         + json.dumps(visible_tasks, ensure_ascii=False)},
    ])
    answers = _index_results(rollout, tasks)
    judge = model.call(f"{split}-judge", [
        {"role": "system", "content": "你是离线标签一致性评分员。只比较 supplied expected 与模型输出，判断结论是否符合这组既定标签、是否明确。"
         "不擅自替换给定标签；标签偏差由独立保留集检验。输出 score 在0到1之间，完全一致为1，完全矛盾为0。"
         '仅返回 JSON：{"results":[{"case_id":"...","score":0.0,"feedback":"具体中文解释"}]}。'},
        {"role": "user", "content": json.dumps([{"case_id": t["case_id"], "expected": t["expected"],
                                                  "response": answers[t["case_id"]]} for t in tasks], ensure_ascii=False)},
    ])
    judges = _index_results(judge, tasks)
    cases = []
    for task in tasks:
        answer, judged = answers[task["case_id"]], judges[task["case_id"]]
        checks = {}
        for field in FIELDS:
            actual, expected = answer.get(field), task["expected"][field]
            valid_type = isinstance(actual, bool) if field == "paid_off" else isinstance(actual, (float, int)) and not isinstance(actual, bool)
            checks[field] = bool(valid_type and actual == expected)
        judge_score = judged.get("score")
        if isinstance(judge_score, bool) or not isinstance(judge_score, (int, float)) or not math.isfinite(judge_score) or not 0 <= judge_score <= 1:
            raise ValueError("judge 分数越界；不截断、不填默认值")
        if not isinstance(judged.get("feedback"), str) or not isinstance(answer.get("answer"), str):
            raise ValueError("模型缺少自然语言结论或评分反馈")
        deterministic = sum(checks.values()) / len(FIELDS)
        cases.append({"case_id": task["case_id"], "expected": task["expected"], "actual": answer,
                      "checks": checks, "deterministic_score": deterministic, "judge_score": judge_score,
                      "judge_feedback": judged["feedback"], "score": 0.8 * deterministic + 0.2 * judge_score})
    return {"candidate_id": candidate.candidate_id, "split": split, "cases": cases,
            "score": sum(c["score"] for c in cases) / len(cases),
            "deterministic_score": sum(c["deterministic_score"] for c in cases) / len(cases),
            "judge_score": sum(c["judge_score"] for c in cases) / len(cases)}


def _propose(model: _Model, parent: CandidateBundle, train: list[dict[str, Any]], feedback: dict[str, Any], round_no: int) -> tuple[str, str]:
    previous = (Path(parent.candidate_dir) / "SKILL.md").read_text(encoding="utf-8")
    result = model.call("reflection", [
        {"role": "system", "content": "你负责根据任务标签、实际回复和评分反馈精炼技能系统指令。"
         "目标是提高给定训练集标签一致性分数，抽取通用金额计算规则，不硬编码订单号或答案。"
         "每轮必须依据上一版的实际反馈修订；已正确的部分保留，消除含混或冲突。"
         "返回完整可执行 SKILL.md，保留原 name；含 YAML frontmatter 的 name、中文 description，以及明确的操作步骤。"
         '仅返回 JSON：{"reflection":"根据本轮反馈说明具体改进","skill_md":"完整文件文本"}。'},
        {"role": "user", "content": json.dumps({"round": round_no, "previous_skill": previous,
                                                  "train_tasks": train, "actual_feedback": feedback}, ensure_ascii=False)},
    ], max_tokens=3200)
    skill, reflection = result.get("skill_md"), result.get("reflection")
    if not isinstance(skill, str) or not isinstance(reflection, str) or not reflection.strip():
        raise ValueError("精炼器必须返回完整技能和具体反思")
    parts = skill.strip().split("---", 2)
    if len(parts) != 3 or parts[0] or len(parts[2].strip()) < 80:
        raise ValueError("候选不是带完整 frontmatter 与正文的 SKILL.md")
    header = yaml.safe_load(parts[1])
    if not isinstance(header, dict) or header.get("name") != parent.skill_name or not isinstance(header.get("description"), str):
        raise ValueError("候选 frontmatter 缺失或擅自改名")
    return skill.strip() + "\n", reflection


def gepa_search(adopted_dir: Path, task_feed: dict[str, Any] | None = None,
                budget_rounds: int = 3, convergence: float | dict[str, Any] | None = None
                ) -> tuple[list[CandidateBundle], dict[str, Any], dict[str, Any]]:
    """按真实训练反馈逐轮精炼，返回候选、离线评分和搜索结束后的保留集结果。

    task_feed 含 train/holdout 两个非空列表；每题含 case_id、prompt、order 和
    expected={paid_success,processing,remaining,paid_off}。None 使用有偏教学材料。
    convergence 是最小增益数值，或含 min_improvement/patience/target_score 的字典。
    不足增益累计到 patience、达到 target_score 或预算用尽即停，没有最少轮数豁免。
    budget_rounds=0 仍保留并评分基线，不生成候选。任何失败均留痕并抛出。
    """
    if isinstance(budget_rounds, bool) or not isinstance(budget_rounds, int) or budget_rounds < 0:
        raise ValueError("budget_rounds 必须是非负整数")
    feed, stop = _validate_feed(task_feed), _convergence(convergence)
    adopted_dir = Path(adopted_dir).resolve()
    before = tree_hashes(adopted_dir)
    if "SKILL.md" not in before:
        raise ValueError("正式目录缺少 SKILL.md")
    run_dir = config.OUTPUT_DIR / "lifecycle" / "gepa" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    source_hashes = {"gepa.py": sha256_text(Path(__file__).read_text(encoding="utf-8")),
                     "skills.py": sha256_text((Path(__file__).parent / "lifecycle" / "skills.py").read_text(encoding="utf-8"))}
    # 在调用模型前固定全部材料、评分标准、停止条件；保留集从不传入反思器。
    frozen = {"task_feed": feed, "scoring": SCORE_POLICY, "budget_rounds": budget_rounds,
              "convergence": stop, "adopted_before": before, "source_sha256": source_hashes,
              "fixed_at": time.time()}
    write_json(run_dir / "fixed_inputs.json", frozen)
    manifest_hash = sha256_text(json.dumps(frozen, ensure_ascii=False, sort_keys=True))
    candidates: list[CandidateBundle] = []
    offline: dict[str, Any] = {"run_dir": str(run_dir), "fixed_inputs_hash": manifest_hash,
                               "rows": [], "rounds": [], "budget_rounds": budget_rounds,
                               "convergence": stop, "model": "glm-5.2", "scoring": SCORE_POLICY,
                               "method": "基于逐轮实际反馈的 current-best 精炼；不实现交叉与 Pareto。",
                               "references": ["https://arxiv.org/abs/2507.19457", "https://dspy.ai/current/api/optimizers/GEPA/overview/"]}
    model = None
    try:
        model = _Model(run_dir)
        baseline = create_candidate(adopted_dir, "gepa", {})
        candidates.append(baseline)
        offline["baseline_id"] = baseline.candidate_id
        best, best_report = baseline, _evaluate(model, baseline, feed["train"], "train")
        offline["rows"].append(best_report)
        no_gain, reasons = 0, []
        while len(offline["rounds"]) < budget_rounds:
            if stop["target_score"] is not None and best_report["score"] >= stop["target_score"]:
                reasons.append("target_score")
                break
            round_no = len(offline["rounds"]) + 1
            parent = best
            skill, reflection = _propose(model, parent, feed["train"], best_report, round_no)
            candidate = create_candidate(adopted_dir, "gepa", {"SKILL.md": skill})
            candidates.append(candidate)
            report = _evaluate(model, candidate, feed["train"], "train")
            gain = report["score"] - best_report["score"]
            offline["rows"].append(report)
            offline["rounds"].append({"round": round_no, "parent_id": parent.candidate_id,
                                      "candidate_id": candidate.candidate_id, "reflection": reflection,
                                      "feedback_candidate_id": best_report["candidate_id"], "gain": gain,
                                      "meets_min_improvement": gain > 0 and gain >= stop["min_improvement"]})
            no_gain = 0 if gain > 0 and gain >= stop["min_improvement"] else no_gain + 1
            if gain > 0:  # 排名与停止条件分开；小幅改善不获追加预算。
                best, best_report = candidate, report
            write_json(run_dir / "offline_progress.json", offline)
            if no_gain >= stop["patience"]:
                reasons.append("convergence")
                break
        if len(offline["rounds"]) >= budget_rounds:
            reasons.append("budget_rounds")
        if stop["target_score"] is not None and best_report["score"] >= stop["target_score"] and "target_score" not in reasons:
            reasons.append("target_score")
        offline.update(best_candidate_id=best.candidate_id, stop_reason="+".join(reasons),
                       generated_candidates=len(candidates) - 1,
                       search_closed_at=time.time(), search_model_calls=len(model.calls))
        # 搜索已经结束；固定只评基线与训练胜者，不按 holdout 选择其他候选或再精炼。
        baseline_holdout = _evaluate(model, baseline, feed["holdout"], "holdout")
        best_holdout = (baseline_holdout if best.candidate_id == baseline.candidate_id else
                        _evaluate(model, best, feed["holdout"], "holdout"))
        train_gain = best_report["score"] - offline["rows"][0]["score"]
        holdout_delta = best_holdout["score"] - baseline_holdout["score"]
        retain = best.candidate_id == baseline.candidate_id or train_gain <= 0 or holdout_delta < 0
        holdout = {"baseline": baseline_holdout, "candidate": best_holdout,
                   "offline_gain": train_gain, "holdout_delta": holdout_delta,
                   "offline_better_holdout_worse": train_gain > 0 and holdout_delta < 0,
                   "retain_baseline": retain, "recommendation": "KEEP_BASELINE" if retain else "AWAIT_BUSINESS_EVALUATION",
                   "adoption_performed": False, "feedback_to_optimizer": False,
                   "note": "保留集是搜索结束后的单次检查。无论得分如何，本模块都不切换正式目录；采用权属于业务评测与集成层。",
                   "dataset_description": feed.get("description", "调用者提供的数据")}
        offline["model_calls"] = len(model.calls)
        offline["adopted_unchanged"] = tree_hashes(adopted_dir) == before
        if not offline["adopted_unchanged"]:
            raise RuntimeError("GEPA 期间正式目录哈希发生变化")
        result = {"candidates": [c.to_dict() for c in candidates], "offline_scores": offline,
                  "holdout_result": holdout, "adopted_before": before,
                  "source_sha256": source_hashes, "adopted_after": tree_hashes(adopted_dir)}
        write_json(run_dir / "result.json", result)
        write_json(config.OUTPUT_DIR / "lifecycle" / "gepa_result.json", result)
        return candidates, offline, holdout
    except Exception as exc:
        write_json(run_dir / "failure.json", {"error_type": type(exc).__name__,
                    "candidates": [c.to_dict() for c in candidates], "offline_scores": offline,
                    "adopted_unchanged": tree_hashes(adopted_dir) == before,
                    "model_calls": len(model.calls) if model else 0})
        raise
    finally:
        if model is not None:
            model.session.close()
