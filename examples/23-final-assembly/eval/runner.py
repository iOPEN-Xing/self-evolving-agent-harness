"""评测运行器：在隔离环境中跑新旧版本 skill，输出结构化对比报告。

隔离方式：run_case 为每个版本、每题创建独立工具环境和会话；
前台与评测共用 run_shift 和 GlmToolLoopAgent，执行模型看不到评分要求。
"""

import time
import hashlib
import json
from typing import Callable, Dict, Any, List

from .cases import EVAL_CASES, SKILL_V0
from .judge import judge_case


def run_eval(skill_text: str, version_label: str,
             llm_call: Callable[[list, float, int], str],
             cases: List[Dict[str, Any]] = None,
             max_tokens: int = 4000, temperature: float = 0.1, *, run_case=None) -> Dict[str, Any]:
    """用给定 skill 跑一组用例，返回逐用例结果。"""
    if run_case is None:
        raise ValueError("On-call 评测必须传入执行真实工具循环的 run_case")
    cases = EVAL_CASES if cases is None else cases
    results = []
    for tc in cases:
        execution = run_case(skill_text, version_label, tc)
        answer = "\n".join(t['response_text'] for t in execution['session']['turns'])
        scored = judge_case(tc, answer, llm_call, execution=execution)
        scored['execution'] = execution
        results.append(scored)
        mark = "MISSING" if scored.get("llm_judge_status") != "ok" or scored.get("llm_judge_score") is None else "PASS" if scored["passed"] else "FAIL"
        print(f"    [{version_label}] {tc['id']} {mark} "
              f"(det={'Y' if scored['deterministic_pass'] else 'N'}, "
              f"judge={scored['llm_judge_score']})")
        time.sleep(0.4)  # 避免限流
    return {
        "version": version_label,
        "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
        "case_set_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True,
            ensure_ascii=False).encode("utf-8")).hexdigest(),
        "results": results,
    }


def summarize(eval_out: Dict[str, Any]) -> Dict[str, Any]:
    """按用例集聚合通过率。"""
    by_set = {}
    for r in eval_out["results"]:
        s = r["set"]
        by_set.setdefault(s, {"total": 0, "passed": 0, "missing_scores": 0, "valid_failed": 0})
        by_set[s]["total"] += 1
        if r.get("llm_judge_status") != "ok" or r.get("llm_judge_score") is None:
            by_set[s]["missing_scores"] += 1
        elif not r["passed"]:
            by_set[s]["valid_failed"] += 1
        if r["passed"]:
            by_set[s]["passed"] += 1
    out = {}
    for s, agg in by_set.items():
        out[s] = {
            "passed": agg["passed"],
            "total": agg["total"],
            "missing_scores": agg["missing_scores"],
            "valid_failed": agg["valid_failed"],
            "valid_scores": agg["total"]-agg["missing_scores"],
            "pass_rate": round(agg["passed"] / agg["total"], 3) if agg["total"] else 0.0,
        }
    out["overall"] = {
        "passed": sum(1 for r in eval_out["results"] if r["passed"]),
        "total": len(eval_out["results"]),
        "missing_scores": sum(a["missing_scores"] for a in by_set.values()),
        "valid_failed": sum(a["valid_failed"] for a in by_set.values()),
        "valid_scores": sum(a["total"]-a["missing_scores"] for a in by_set.values()),
    }
    return out


def compare_versions(v0_out: Dict[str, Any], v1_out: Dict[str, Any]) -> Dict[str, Any]:
    """对比 v0 / v1，输出逐用例新旧对比 + 各集合通过率。"""
    s0 = summarize(v0_out)
    s1 = summarize(v1_out)
    per_case = []
    index = {r["case_id"]: r for r in v1_out["results"]}
    for a in v0_out["results"]:
        b = index.get(a["case_id"])
        if b is None:
            per_case.append({"case_id":a["case_id"], "status":"候选缺失记录"})
            continue
        per_case.append({
            "case_id": a["case_id"],
            "set": a["set"],
            "v0_status": "missing_score" if a.get("llm_judge_status") != "ok" else "passed" if a["passed"] else "valid_failed",
            "v1_status": "missing_score" if b.get("llm_judge_status") != "ok" else "passed" if b["passed"] else "valid_failed",
            "v0_passed": a["passed"],
            "v1_passed": b["passed"],
            "v0_judge": a["llm_judge_score"],
            "v1_judge": b["llm_judge_score"],
            "changed": a["passed"] != b["passed"],
        })
    return {
        "v0_summary": s0,
        "v1_summary": s1,
        "per_case": per_case,
        "ability_improvement_established": False,
        "missing_score_items": [{"version":v,"case_id":r["case_id"]} for v,o in [("v0",v0_out),("v1",v1_out)] for r in o["results"] if r.get("llm_judge_status") != "ok"],
    }
