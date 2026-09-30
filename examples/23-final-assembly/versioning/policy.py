"""版本采用 / 回退决策策略。

自检失败、检查不完整或评分不合法时 REJECT，等待人工检查。
通过上述检查后，采用标准（全部满足才 ADOPT，否则 ROLLBACK）：
  1. 保留集（holdout）：v1 通过率不低于 v0（不退化）。
  2. 新事故集（new_incident）：v1 通过率高于 v0（真的有提升）。
  3. 回归集（regression）：v1 全部通过（不引入错误结论）。

任一不满足即回退：放弃 v1，回到上一个被采用的版本。
"""

from typing import Dict, Any

from eval.cases import EVAL_CASES
from eval.judge import SELF_CHECK_EXPECTED, parse_judge_response, deterministic_score
from eval.runner import compare_versions


def valid_score(row):
    """同时核对原始裁判输出，拒绝经过补分或类型转换的结果。"""
    if not isinstance(row, dict):
        return False
    parsed = parse_judge_response(row.get("llm_judge_raw"))
    det = row.get("deterministic")
    return (parsed["status"] == "ok" and row.get("llm_judge_status") == "ok"
            and type(row.get("llm_judge_score")) is int
            and row["llm_judge_score"] == parsed["score"]
            and row.get("llm_judge_reason") == parsed["reason"]
            and isinstance(det, dict) and bool(det)
            and all(isinstance(c, dict) and type(c.get("pass")) is bool for c in det.values())
            and type(row.get("deterministic_pass")) is bool
            and row["deterministic_pass"] == all(c["pass"] for c in det.values())
            and type(row.get("passed")) is bool
            and row["passed"] == (row["deterministic_pass"] and parsed["score"] >= 3))


def rows_of(value):
    rows = value.get("results") if isinstance(value, dict) else None
    return rows if isinstance(rows, list) else []


def complete_eval(value, label):
    rows = rows_of(value)
    expected = {c["id"]: c for c in EVAL_CASES}
    return (isinstance(value, dict) and value.get("version") == label
            and len(rows) == len(expected)
            and all(isinstance(r, dict) and isinstance(r.get("case_id"), str) for r in rows)
            and {r["case_id"] for r in rows} == set(expected)
            and all(r.get("set") == expected[r["case_id"]]["set"]
                    and isinstance(r.get("answer"), str) and bool(r["answer"].strip())
                    and isinstance(r.get("deterministic"), dict)
                    and set(r["deterministic"]) == set(expected[r["case_id"]]["assertions"])
                    and isinstance(r.get("execution"), dict)
                    and r["execution"].get("fixture") is not True
                    and len(r["execution"].get("session",{}).get("turns",[])) == 2
                    and r["deterministic"] == {k:{"pass":p,"detail":d} for k,(p,d) in
                         deterministic_score(r["answer"],expected[r["case_id"]]["assertions"],r["execution"]).items()}
                    for r in rows))


def evaluation_checks(check, v0, v1):
    rows = rows_of(check)
    self_complete = (isinstance(check, dict) and check.get("complete") is True
                     and len(rows) == len(SELF_CHECK_EXPECTED)
                     and all(isinstance(r, dict) and isinstance(r.get("case_id"), str) for r in rows)
                     and {r["case_id"] for r in rows} == set(SELF_CHECK_EXPECTED))
    valid = bool(rows) and all(valid_score(r) for r in rows + rows_of(v0) + rows_of(v1))
    self_ok = (self_complete and check.get("passed") is True
               and all(valid_score(r) and r.get("deterministic_pass") is True
                       and r.get("expected_passed") is SELF_CHECK_EXPECTED[r["case_id"]]
                       and r.get("passed") is SELF_CHECK_EXPECTED[r["case_id"]]
                       and r.get("matched") is True for r in rows))
    complete = self_complete and complete_eval(v0, "v0") and complete_eval(v1, "v1")
    return [
        {"check": "self_check_passed", "pass": bool(self_ok),
         "detail": "评分器须正确拒绝已知错误答案，并接受正确答案"},
        {"check": "evaluation_complete", "pass": bool(complete),
         "detail": "两项自检与新旧版本全部六个用例必须完整且无重复、缺漏或错组"},
        {"check": "all_scores_valid", "pass": bool(valid),
         "detail": "全部评分必须解析成功、字段合法，逐项判定与原始评分一致"},
    ]


def decide(comparison: Dict[str, Any], *, self_check=None, v0_eval=None, v1_eval=None) -> Dict[str, Any]:
    """核对原始评测和自检后给出 ADOPT / ROLLBACK / REJECT 决策。"""
    gates = evaluation_checks(self_check, v0_eval, v1_eval)
    if not all(c["pass"] for c in gates):
        return {"decision": "REJECT", "status": "WAIT_FOR_MANUAL_REVIEW", "checks": gates,
                "reason_summary": "禁止自动采用，保留 v0，等待人工检查：" +
                "；".join(c["detail"] for c in gates if not c["pass"])}
    # 采用率从已核验逐项记录重算，不能信任外部传来的汇总数字。
    comparison = compare_versions(v0_eval, v1_eval)
    s0 = comparison["v0_summary"]
    s1 = comparison["v1_summary"]
    reasons = list(gates)

    # 1. 保留集不退化
    h0 = s0.get("holdout", {}).get("pass_rate", 0.0)
    h1 = s1.get("holdout", {}).get("pass_rate", 0.0)
    holdout_ok = h1 >= h0
    reasons.append({
        "check": "holdout_no_regression",
        "v0": h0, "v1": h1, "pass": holdout_ok,
        "detail": f"保留集 v0={h0:.0%} -> v1={h1:.0%}（不退化需 v1>=v0）",
    })

    # 2. 新事故集有提升
    n0 = s0.get("new_incident", {}).get("pass_rate", 0.0)
    n1 = s1.get("new_incident", {}).get("pass_rate", 0.0)
    new_incident_ok = n1 > n0
    reasons.append({
        "check": "new_incident_improvement",
        "v0": n0, "v1": n1, "pass": new_incident_ok,
        "detail": f"新事故集 v0={n0:.0%} -> v1={n1:.0%}（提升需 v1>v0）",
    })

    # 3. 回归集全过
    r1 = s1.get("regression", {}).get("pass_rate", 0.0)
    regression_ok = r1 >= 1.0
    reasons.append({
        "check": "regression_all_pass",
        "v1": r1, "pass": regression_ok,
        "detail": f"回归集 v1={r1:.0%}（全过需 100%）",
    })

    adopt = holdout_ok and new_incident_ok and regression_ok
    decision = "ADOPT" if adopt else "ROLLBACK"

    return {
        "decision": decision,
        "checks": reasons,
        "reason_summary": (
            "采用新版本" if adopt else
            "回退到旧版本：" +
            "；".join(c["detail"] for c in reasons if not c["pass"])
        ),
    }
