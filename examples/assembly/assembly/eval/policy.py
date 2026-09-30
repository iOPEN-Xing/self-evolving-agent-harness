"""根据可核查的成对评测作决定；本模块不修改正式技能目录。

当前只有四个业务用例，因此所有用例都按关键用例处理，候选必须全部
通过。较大的用例集可以另外约定容差，但不能在本模块中悄悄放宽。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from .. import config
from ..contracts import AdoptionDecision, CaseResult, EvalReport, JudgeSelfCheck, sha256_text
from .judge import EVALUATOR_VERSION, judge_answer, judge_self_check


@dataclass(frozen=True)
class HoldoutComparison:
    """同一组留出题上，基线与候选各自独立运行得到的报告。"""

    baseline: EvalReport
    candidate: EvalReport

    def to_dict(self) -> dict[str, Any]:
        return {"baseline": self.baseline.to_dict(), "candidate": self.candidate.to_dict()}


def _check(checks: list[dict[str, Any]], name: str, passed: bool, **facts: Any) -> bool:
    checks.append({"name": name, "passed": bool(passed), **facts})
    return bool(passed)


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _case_errors(case: CaseResult) -> list[str]:
    errors: list[str] = []
    if not isinstance(case.case_id, str) or not case.case_id:
        errors.append("用例编号为空")
    if type(case.passed) is not bool or type(case.business_correct) is not bool:
        errors.append("业务结论或通过字段不是布尔值")
    points = case.key_points
    if not isinstance(points, dict) or not points or any(
        not isinstance(key, str) or not key or type(value) is not bool
        for key, value in points.items()
    ):
        errors.append("关键点必须是非空的名称到布尔值映射")
    elif case.passed != (case.business_correct and all(points.values())):
        errors.append("通过字段与业务结论、关键点不一致")
    if not isinstance(case.answer, str) or not case.answer.strip():
        errors.append("缺少实际答案")
    return errors


def _context(report: EvalReport) -> dict[str, Any]:
    value = getattr(report, "evaluation_context", None)
    return value if isinstance(value, dict) else {}


def _prompt_map(report: EvalReport) -> dict[str, str]:
    cases = _context(report).get("cases", [])
    if not isinstance(cases, list):
        return {}
    return {
        item["case_id"]: item["prompt_hash"]
        for item in cases
        if isinstance(item, dict)
        and isinstance(item.get("case_id"), str)
        and isinstance(item.get("prompt_hash"), str)
    }


def _regrade_errors(report: EvalReport) -> list[str]:
    """从完整评分要求重算，不能只相信报告里相互一致的布尔值。"""
    context = _context(report)
    specs = context.get("case_specs")
    if not isinstance(specs, list) or not specs or any(not isinstance(item, dict) for item in specs):
        return ["缺少完整用例及评分要求，无法独立重算"]
    errors: list[str] = []
    try:
        expected_hash = sha256_text(json.dumps(specs, ensure_ascii=False, sort_keys=True))
        if expected_hash != context.get("case_set_hash"):
            errors.append("完整用例内容与运行记录的 case_set_hash 不一致")
        canonical = {
            item["case_id"]: item
            for item in json.loads((config.SCENARIOS_DIR / "cases.json").read_text(encoding="utf-8"))
        }
        current_data_hash = hashlib.sha256((config.SCENARIOS_DIR / "orders.json").read_bytes()).hexdigest()
        if context.get("data_hash") != current_data_hash:
            errors.append("当前评分器所读订单流水与实际运行的数据哈希不一致")
        by_id = {item.case_id: item for item in report.cases}
        spec_ids = [item.get("case_id") for item in specs]
        if any(not isinstance(key, str) or not key for key in spec_ids):
            return errors + ["完整用例中的编号格式错误"]
        if len(set(spec_ids)) != len(spec_ids) or set(spec_ids) != set(by_id):
            return errors + ["完整用例与逐项评分不能一一对应"]
        prompts = _prompt_map(report)
        fixed_fields = ("business_correct", "conclusion", "key_points", "must_not_say")
        for spec in specs:
            case_id = spec["case_id"]
            authoritative = canonical.get(spec.get("order_id", case_id))
            if authoritative is None:
                errors.append(f"{case_id}: 没有对应的权威评分要求")
                continue
            if any(spec.get(field) != authoritative.get(field) for field in fixed_fields):
                errors.append(f"{case_id}: 评分要求被改变，不能弱化关键点或禁止断言")
                continue
            prompt = spec.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip() or prompts.get(case_id) != sha256_text(prompt):
                errors.append(f"{case_id}: 完整用例的提示词与运行记录不一致")
                continue
            recorded = by_id[case_id]
            recomputed = judge_answer(spec, recorded.answer)
            if any(getattr(recorded, field) != getattr(recomputed, field)
                   for field in ("passed", "business_correct", "key_points")):
                errors.append(f"{case_id}: 保存的判定与当前评分器独立重算不一致")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(f"评分依据无法读取或重算：{type(exc).__name__}")
    return errors


def _validate_report(report: EvalReport, name: str, checks: list[dict[str, Any]]) -> bool:
    if not isinstance(report, EvalReport):
        return _check(checks, name + ".report_integrity", False, errors=["不是 EvalReport"])
    errors: list[str] = []
    valid_cases = isinstance(report.cases, list) and bool(report.cases) and all(
        isinstance(item, CaseResult) for item in report.cases
    )
    if not valid_cases:
        errors.append("逐用例结果为空或类型错误")
    else:
        ids = [item.case_id for item in report.cases]
        if len({str(item) for item in ids}) != len(ids):
            errors.append("用例编号重复")
        for case in report.cases:
            errors.extend(f"{case.case_id}: {error}" for error in _case_errors(case))
        if type(report.total) is not int or report.total != len(report.cases):
            errors.append("total 与逐用例数量不一致")
        if type(report.passed) is not int or report.passed != sum(
            item.passed is True for item in report.cases
        ):
            errors.append("passed 与逐用例结果不一致")
    if not _is_hash(report.skill_hash):
        errors.append("技能哈希不是 SHA-256")
    if report.evaluator_version != EVALUATOR_VERSION:
        errors.append("评分器版本不是本次自检所用版本")
    if type(report.ran_at) not in (int, float) or not math.isfinite(report.ran_at) or report.ran_at <= 0:
        errors.append("评测时间无效")
    integrity_ok = _check(
        checks, name + ".report_integrity", not errors,
        label=report.label, skill_hash=report.skill_hash,
        evaluator_version=report.evaluator_version, errors=errors,
    )
    context = _context(report)
    context_errors: list[str] = []
    for field in ("model", "base_url", "execution_protocol", "run_id"):
        if not isinstance(context.get(field), str) or not context[field].strip():
            context_errors.append(f"缺少运行字段 {field}")
    for field in ("data_hash", "case_set_hash"):
        if not _is_hash(context.get(field)):
            context_errors.append(f"缺少有效的 {field}")
    if context.get("skill_hash") != report.skill_hash:
        context_errors.append("运行记录中的技能哈希与报告不一致")
    for field in ("tool_grounded", "isolated", "skill_unchanged", "data_unchanged"):
        if context.get(field) is not True:
            context_errors.append(f"没有确认 {field}")
    if context.get("response_model_confirmed") is not True or context.get("response_models") != [context.get("model")]:
        context_errors.append("实际返回模型与指定模型不一致或尚未确认")
    if not isinstance(context.get("model_parameters"), dict) or not context["model_parameters"]:
        context_errors.append("缺少模型采样参数")
    answer_hashes = context.get("answer_hashes")
    if not isinstance(answer_hashes, dict):
        context_errors.append("缺少实际答案哈希")
    elif valid_cases and any(
        not isinstance(item.case_id, str) or not isinstance(item.answer, str)
        or answer_hashes.get(item.case_id) != sha256_text(item.answer)
        for item in report.cases
    ):
        context_errors.append("评分报告答案与运行记录中的哈希不一致")
    metadata = context.get("cases")
    if not isinstance(metadata, list) or not metadata:
        context_errors.append("缺少逐用例提示词哈希")
    elif any(
        not isinstance(item, dict)
        or not isinstance(item.get("case_id"), str)
        or not _is_hash(item.get("prompt_hash"))
        for item in metadata
    ):
        context_errors.append("逐用例提示词记录格式错误")
    elif valid_cases:
        mapped = _prompt_map(report)
        expected_ids = {item for item in ids if isinstance(item, str)}
        if len(mapped) != len(metadata) or set(mapped) != expected_ids:
            context_errors.append("提示词记录与报告用例不能一一对应")
    context_ok = _check(
        checks, name + ".execution_evidence", not context_errors,
        context=context, errors=context_errors,
    )
    regrade_errors = _regrade_errors(report) if integrity_ok else ["报告结构错误，不能独立重算"]
    regrade_ok = _check(checks, name + ".independent_regrade", not regrade_errors, errors=regrade_errors)
    return integrity_ok and context_ok and regrade_ok


def _selfcheck(check: JudgeSelfCheck, checks: list[dict[str, Any]]) -> bool:
    if not isinstance(check, JudgeSelfCheck):
        return _check(checks, "judge_self_check", False, reason="没有可信的评分器自检记录")
    details = check.details
    valid_details = isinstance(details, list) and bool(details) and all(
        isinstance(item, CaseResult) and not _case_errors(item) for item in details
    )
    bad = [item for item in details if isinstance(item, CaseResult)
           and isinstance(item.case_id, str) and item.case_id.startswith("known_bad:")] if isinstance(details, list) else []
    good = [item for item in details if isinstance(item, CaseResult)
            and isinstance(item.case_id, str) and item.case_id.startswith("valid_paraphrase:")] if isinstance(details, list) else []
    unique = valid_details and len({item.case_id for item in details}) == len(details)
    required_bad = {"known_bad:contradiction", "known_bad:wrong_order",
                    "known_bad:fabrication", "known_bad:processing_as_success"}
    covered_bad = {item.case_id for item in bad}
    try:
        live_check = judge_self_check()
        reproduced = check.to_dict() == live_check.to_dict() and live_check.passed
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        reproduced = False
    passed = (
        check.known_bad_rejected is True
        and check.valid_paraphrase_accepted is True
        and unique and required_bad <= covered_bad and bool(good) and reproduced
        and all(not item.passed and not item.business_correct for item in bad)
        and all(item.passed for item in good)
    )
    return _check(
        checks, "judge_self_check", passed,
        evaluator_version=EVALUATOR_VERSION,
        known_bad_rejected=check.known_bad_rejected,
        valid_paraphrase_accepted=check.valid_paraphrase_accepted,
        known_bad_count=len(bad), valid_paraphrase_count=len(good),
        missing_bad_categories=sorted(required_bad - covered_bad),
        details_consistent=bool(unique),
        reproduced_by_current_judge=bool(reproduced),
        reason="自检必须逐条拒绝四类已知错误，并接受合法同义答案",
    )


def _rates(report: EvalReport) -> dict[str, Any]:
    return {
        "passed": report.passed, "total": report.total,
        "pass_rate": report.pass_rate,
        "business_correct": sum(item.business_correct for item in report.cases),
        "business_pass_rate": sum(item.business_correct for item in report.cases) / report.total,
        "skill_hash": report.skill_hash,
    }


def _paired(baseline: EvalReport, candidate: EvalReport,
            name: str, checks: list[dict[str, Any]]) -> bool:
    left, right = _context(baseline), _context(candidate)
    fields = ("model", "base_url", "model_parameters", "data_hash", "case_set_hash", "execution_protocol")
    same_fields = {field: left.get(field) == right.get(field) for field in fields}
    left_ids = {item.case_id: item for item in baseline.cases}
    right_ids = {item.case_id: item for item in candidate.cases}
    same_cases = set(left_ids) == set(right_ids) and _prompt_map(baseline) == _prompt_map(candidate)
    same_points = same_cases and all(
        left_ids[key].key_points.keys() == right_ids[key].key_points.keys()
        for key in left_ids
    )
    return _check(
        checks, name + ".paired_comparison",
        all(same_fields.values()) and same_cases and same_points
        and baseline.evaluator_version == candidate.evaluator_version
        and left.get("run_id") != right.get("run_id"),
        same_configuration=same_fields, same_case_prompts=same_cases,
        same_required_key_points=same_points,
        baseline_run_id=left.get("run_id"), candidate_run_id=right.get("run_id"),
    )


def _noninferior(baseline: EvalReport, candidate: EvalReport,
                 name: str, checks: list[dict[str, Any]]) -> None:
    base, cand = _rates(baseline), _rates(candidate)
    _check(
        checks, name + ".rates_noninferior",
        cand["pass_rate"] >= base["pass_rate"]
        and cand["business_pass_rate"] >= base["business_pass_rate"],
        baseline=base, candidate=cand,
    )
    base_cases = {item.case_id: item for item in baseline.cases}
    candidate_cases = {item.case_id: item for item in candidate.cases}
    regressions = [
        key for key, item in base_cases.items()
        if key not in candidate_cases
        or (item.passed and not candidate_cases[key].passed)
        or (item.business_correct and not candidate_cases[key].business_correct)
    ]
    _check(checks, name + ".critical_cases_noninferior", not regressions,
           critical_case_ids=sorted(base_cases), regressed_case_ids=regressions)
    _check(checks, name + ".candidate_all_checks_pass", all(item.passed for item in candidate.cases),
           failed_case_ids=[item.case_id for item in candidate.cases if not item.passed],
           reason="当前小用例集全部视为关键用例，候选必须全部通过")


def _decision(checks: list[dict[str, Any]], *, success: str, label: str | None,
              old_hash: str | None, target_hash: str | None) -> AdoptionDecision:
    failed = [item["name"] for item in checks if not item["passed"]]
    accepted = not failed
    if accepted:
        reason = (
            "评分器自检可信；候选业务结果、关键用例与留出评测全部通过且不劣于基线。"
            "允许集成层采用候选。"
            if success == "ADOPT" else
            "已采用版在后续任务的关键用例上退化；相同任务、数据、模型与评分器下，"
            "已验证快照独立重跑通过，且评分器自检通过。允许集成层恢复该快照。"
        )
    else:
        reason = "未通过检查：" + "、".join(failed) + "。正式目录保持现版，保留候选与原因。"
        if "judge_self_check" in failed:
            reason = "评分器自检不可信，禁止自动采用或恢复。" + reason
    return AdoptionDecision(
        decision=success if accepted else "REJECT", candidate_id=label,
        checks=checks, reason=reason,
        previous_skill_hash=old_hash,
        new_skill_hash=target_hash if accepted else old_hash,
    )


def decide(candidate_report: EvalReport, baseline_report: EvalReport,
           selfcheck: JudgeSelfCheck,
           holdout_report: HoldoutComparison | EvalReport | None = None) -> AdoptionDecision:
    """全部证据齐备才 ADOPT；单份留出报告不能代替同题基线对照。"""
    checks: list[dict[str, Any]] = []
    _selfcheck(selfcheck, checks)
    base_ok = _validate_report(baseline_report, "baseline", checks)
    candidate_ok = _validate_report(candidate_report, "candidate", checks)
    if base_ok and candidate_ok:
        _paired(baseline_report, candidate_report, "business", checks)
        _noninferior(baseline_report, candidate_report, "business", checks)
    has_holdout = isinstance(holdout_report, HoldoutComparison)
    _check(checks, "holdout_paired_reports_present", has_holdout,
           reason="留出题必须同时提供基线与候选独立运行报告；单份 EvalReport 缺少对照")
    if has_holdout:
        hold_base_ok = _validate_report(holdout_report.baseline, "holdout_baseline", checks)
        hold_candidate_ok = _validate_report(holdout_report.candidate, "holdout_candidate", checks)
        if hold_base_ok and hold_candidate_ok:
            _paired(holdout_report.baseline, holdout_report.candidate, "holdout", checks)
            _noninferior(holdout_report.baseline, holdout_report.candidate, "holdout", checks)
        if base_ok and candidate_ok and hold_base_ok and hold_candidate_ok:
            reports = (baseline_report, candidate_report, holdout_report.baseline, holdout_report.candidate)
            run_ids = [_context(report)["run_id"] for report in reports]
            development_prompts = set(_prompt_map(baseline_report).values()) | set(_prompt_map(candidate_report).values())
            holdout_prompts = set(_prompt_map(holdout_report.baseline).values()) | set(_prompt_map(holdout_report.candidate).values())
            _check(checks, "holdout_independent", len(set(run_ids)) == 4
                   and not development_prompts.intersection(holdout_prompts),
                   run_ids=run_ids, overlapping_prompt_hashes=sorted(development_prompts.intersection(holdout_prompts)))
            _check(checks, "holdout_same_versions",
                   holdout_report.baseline.skill_hash == baseline_report.skill_hash
                   and holdout_report.candidate.skill_hash == candidate_report.skill_hash,
                   baseline_skill_hash=baseline_report.skill_hash,
                   candidate_skill_hash=candidate_report.skill_hash,
                   holdout_baseline_skill_hash=holdout_report.baseline.skill_hash,
                   holdout_candidate_skill_hash=holdout_report.candidate.skill_hash)
            shared_fields = ("model", "base_url", "model_parameters", "data_hash", "execution_protocol")
            same_conditions = {
                field: all(_context(report)[field] == _context(reports[0])[field] for report in reports)
                for field in shared_fields
            }
            _check(checks, "holdout_same_model_data_protocol", all(same_conditions.values()),
                   same_configuration=same_conditions)
    return _decision(checks, success="ADOPT", label=getattr(candidate_report, "label", None),
                     old_hash=getattr(baseline_report, "skill_hash", None),
                     target_hash=getattr(candidate_report, "skill_hash", None))


def decide_restore(current_report: EvalReport, verified_snapshot_report: EvalReport,
                   selfcheck: JudgeSelfCheck, *, adopted_skill_hash: str,
                   verified_snapshot_hash: str, snapshot_verified: bool,
                   data_control_report: EvalReport,
                   adopted_at: float | None = None) -> AdoptionDecision:
    """仅判定是否恢复；实际取回快照由集成层 lifecycle 执行。

    verified_snapshot_report 是快照先前通过的记录；data_control_report 是
    快照在本次后续任务上新跑的对照，二者不能用同一次运行充数。
    adopted_at 必须来自采用决定；两次新任务运行均须实际开始于采用之后。
    """
    checks: list[dict[str, Any]] = []
    _selfcheck(selfcheck, checks)
    current_ok = _validate_report(current_report, "current", checks)
    snapshot_ok = _validate_report(verified_snapshot_report, "verified_snapshot", checks)
    control_ok = _validate_report(data_control_report, "snapshot_control", checks)
    _check(checks, "restore_hash_identity",
           _is_hash(adopted_skill_hash) and _is_hash(verified_snapshot_hash)
           and adopted_skill_hash != verified_snapshot_hash
           and getattr(current_report, "skill_hash", None) == adopted_skill_hash
           and getattr(verified_snapshot_report, "skill_hash", None) == verified_snapshot_hash
           and getattr(data_control_report, "skill_hash", None) == verified_snapshot_hash,
           adopted_skill_hash=adopted_skill_hash, verified_snapshot_hash=verified_snapshot_hash)
    _check(checks, "snapshot_previously_verified", snapshot_verified is True and snapshot_ok
           and all(item.passed for item in verified_snapshot_report.cases),
           snapshot_verified=snapshot_verified,
           reason="须由生命周期层确认这是最近的已验证快照；旧报告本身必须全部通过")
    if current_ok and control_ok:
        _paired(data_control_report, current_report, "restore_control", checks)
        failed_ids = [item.case_id for item in current_report.cases if not item.passed]
        _check(checks, "adopted_version_critical_failure", bool(failed_ids),
               critical_case_ids=[item.case_id for item in current_report.cases],
               failed_case_ids=failed_ids, current=_rates(current_report))
        _check(checks, "same_task_snapshot_control_passed", all(item.passed for item in data_control_report.cases),
               control=_rates(data_control_report),
               reason="同题同数据的快照重跑必须全部通过，才能排除数据和评分条件变化")
    if current_ok and snapshot_ok and control_ok:
        run_ids = [_context(report)["run_id"] for report in
                   (current_report, verified_snapshot_report, data_control_report)]
        _check(checks, "subsequent_independent_runs", len(set(run_ids)) == 3
               and current_report.ran_at > verified_snapshot_report.ran_at
               and data_control_report.ran_at > verified_snapshot_report.ran_at,
               run_ids=run_ids,
               snapshot_verified_at=verified_snapshot_report.ran_at,
               current_ran_at=current_report.ran_at,
               control_ran_at=data_control_report.ran_at)
    current_context, control_context = _context(current_report), _context(data_control_report)
    timing = {
        "adopted_at": adopted_at,
        "snapshot_verified_at": getattr(verified_snapshot_report, "ran_at", None),
        "current_run_started_at": current_context.get("run_started_at"),
        "current_run_finished_at": current_context.get("run_finished_at"),
        "current_report_at": getattr(current_report, "ran_at", None),
        "control_run_started_at": control_context.get("run_started_at"),
        "control_run_finished_at": control_context.get("run_finished_at"),
        "control_report_at": getattr(data_control_report, "ran_at", None),
    }
    valid_timing = all(type(value) in (int, float) and math.isfinite(value) and value > 0
                       for value in timing.values())
    chronology_ok = bool(valid_timing and current_ok and snapshot_ok and control_ok
                         and adopted_at >= verified_snapshot_report.ran_at
                         and current_context["run_started_at"] > adopted_at
                         and control_context["run_started_at"] > adopted_at
                         and current_context["run_started_at"] <= current_context["run_finished_at"] <= current_report.ran_at
                         and control_context["run_started_at"] <= control_context["run_finished_at"] <= data_control_report.ran_at)
    _check(checks, "restore_runs_after_adoption", chronology_ok, **timing,
           reason="快照先通过验证，再采用当前版；当前版与快照对照均在采用之后实际运行，结束后才形成报告")
    return _decision(checks, success="RESTORE", label=getattr(current_report, "label", None),
                     old_hash=adopted_skill_hash, target_hash=verified_snapshot_hash)
