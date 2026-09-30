"""评分器：确定性断言 + LLM-as-judge 双轨，附自检逻辑。

设计原则：
  - 确定性断言（关键词命中/不命中/长度/无列表）便宜、可复现，但会误判：
    纯关键词无法识别"关键词全命中但业务结论自相矛盾"。
  - LLM-as-judge 能判断语义一致性和业务结论正确性，但有随机性。
  - 两者结合：确定性断言当硬门槛，LLM-judge 当语义复核。
  - self_check() 使用正反两个已知样本检验评分器；失败时必须禁止采用。
"""

import json
import re
from typing import Callable, Dict, Any


# ── 确定性断言 ──────────────────────────────────────────────────────────

def deterministic_score(answer, assertions, execution=None):
    """从真实回执核对对象、窗口、查询与转换，不靠答案关键词代替执行。"""
    execution = execution or {}
    turns = execution.get('session',{}).get('turns',[])
    state = execution.get('state',{})
    reads = [r for t in turns for r in t.get('observation_reads',[])]
    successful = {r['tool'] + (':'+r['arguments']['scope'] if r['tool']=='read_metrics' else '') for r in reads}
    final = turns[-1].get('assessment',{}) if turns else {}
    transitions = state.get('transitions',[])
    pairs = [(r['from'],r['to']) for r in transitions]
    required = [('PATROLLING','INVESTIGATING'),('INVESTIGATING','VERIFYING')] if assertions['state_transitions'] else []
    checks = {
        'object_scope': (bool(reads) and all(r['arguments']['service']==assertions['object_scope']
                            and r['observation']['service']==assertions['object_scope'] for r in reads), '核对实际查询服务'),
        'time_window': (bool(turns) and all(t.get('observation_reads') and all(
            r['arguments']['logical_time']==t['phase_context']['logical_time']==r['observation']['window']['end']
            and r['observation']['window']['start'] < r['observation']['window']['end']
            for r in t['observation_reads']) for t in turns), '查询只能读取本轮窗口；过期时间戳仍如实保留'),
        'actual_queries': (set(assertions['actual_queries']) <= successful and all(
            any(r['tool']=='read_metrics' for r in t.get('observation_reads',[])) for t in turns),
            '实际成功查询：'+str(sorted(successful))),
        'report_schema': (bool(turns) and all(t.get('assessment') and t['stop_reason']=='final_answer'
                            and not t.get('tool_errors') for t in turns), '每轮须有完整报告与无错误的实际执行'),
        'assessment': (final.get('assessment') in assertions['assessment'], '实际最终判断：'+str(final.get('assessment'))),
        'state_transitions': (bool(turns) and all(p in pairs for p in required)
            and ('VERIFYING','PATROLLING') not in pairs
            and not any(t.get('state_record',{}).get('transition_denied') for t in turns)
            and (('PATROLLING','INVESTIGATING') not in pairs if not required else True),
            '实际转换：'+str(pairs)),
    }
    return {key: (bool(value[0]),value[1]) for key,value in checks.items()}


def deterministic_pass(checks: Dict[str, Any]) -> bool:
    return all(p for p, _ in checks.values())


# ── LLM-as-judge ────────────────────────────────────────────────────────

def llm_judge(answer: str, rubric: str, case_input: str,
              llm_call: Callable[[list, float, int], str]) -> Dict[str, Any]:
    """用 glm-5.2 当裁判，按 rubric 给 1-5 分，并给出理由。

    llm_call(messages, temperature, max_tokens) -> str。
    返回状态、分数、理由和原始响应；解析失败时分数为 None。
    """
    prompt = (
        "你是严格的评测裁判。下面是一个On-call 值守助手对给定告警的回答。"
        "请按评分标准打分（1-5 的整数），并给一句理由。\n"
        "只输出 JSON：{\"score\": int, \"reason\": \"...\"}。\n\n"
        f"告警输入：{case_input}\n\n"
        f"助手回答：{answer}\n\n"
        f"评分标准：{rubric}"
    )
    raw = llm_call([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=2500)
    return parse_judge_response(raw)


def parse_judge_response(raw: str) -> Dict[str, Any]:
    """严格解析整份 JSON；不截取数字、不补分、不夹取 JSON 片段。"""
    def unique_keys(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError(f"重复字段：{key}")
            data[key] = value
        return data

    try:
        data = json.loads(raw, object_pairs_hook=unique_keys)
        if not isinstance(data, dict) or set(data) != {"score", "reason"}:
            raise ValueError("必须且只能包含 score 和 reason")
        if type(data["score"]) is not int or not 1 <= data["score"] <= 5:
            raise ValueError("score 必须是 1 到 5 的整数")
        if not isinstance(data["reason"], str) or not data["reason"].strip():
            raise ValueError("reason 必须是非空字符串")
    except (ValueError, TypeError) as exc:
        return {"status": "parse_error", "score": None, "reason": str(exc), "raw": raw}
    return {"status": "ok", "score": data["score"], "reason": data["reason"], "raw": raw}


# ── 综合判定 ────────────────────────────────────────────────────────────

def judge_case(case: Dict[str, Any], answer: str,
               llm_call: Callable[[list, float, int], str], *, execution=None) -> Dict[str, Any]:
    """对一个用例的一条回答及实际执行做完整评分。"""
    det = deterministic_score(answer, case["assertions"], execution)
    det_ok = deterministic_pass(det)
    j = llm_judge(answer, case["judge_rubric"], case["input"] + "\n实际查询回执：" + json.dumps([t.get("observation_reads",[]) for t in (execution or {}).get("session",{}).get("turns",[])],ensure_ascii=False), llm_call)
    # 业务判定：确定性断言通过 且 LLM-judge >= 3 才算 PASS。
    # （1-2 分说明语义上业务结论错了；3 分=基本正确，4-5 分=明确完整。参考骨架用 3 分及格。）
    passed = det_ok and j["status"] == "ok" and j["score"] >= 3
    return {
        "case_id": case["id"],
        "set": case["set"],
        "answer": answer,
        "deterministic": {k: {"pass": p, "detail": d} for k, (p, d) in det.items()},
        "deterministic_pass": det_ok,
        "llm_judge_status": j["status"],
        "llm_judge_raw": j["raw"],
        "llm_judge_score": j["score"],
        "llm_judge_reason": j["reason"],
        "passed": passed,
    }


# ── 评分程序自检：演示"纯关键词断言会误判" ───────────────────────────────

SELF_CHECK_EXPECTED = {"known_wrong": False, "known_correct": True}


def self_check(llm_call: Callable[[list, float, int], str]) -> Dict[str, Any]:
    """错误答案须被拒绝，正确答案须被接受；两项都有效才通过自检。"""
    from .cases import EVAL_CASES
    case = next(c for c in EVAL_CASES if c["id"] == "NI-01")
    from shift.runtime import ObservationTools
    # 以下是明确标记的评分器夹具，不计作模型执行或正式评测数据。
    reads = []
    tools = ObservationTools('NI-01',5,reads)
    for spec in case['assertions']['actual_queries']:
        name, _, scope = spec.partition(':')
        args = dict(service='search-api',logical_time=5)
        if scope: args['scope'] = scope
        tools(name,args)
    common = 'search-api 窗口0至5：配置文件80，实例20，池20/20且等待35，日志连接等待超时；配置未按预期生效是主要嫌疑。'
    answers = {
        'known_wrong': common + '因此服务已经恢复健康，可以结案，不需复查。',
        'known_correct': common + '这是待验证的嫌疑，尚未恢复；继续VERIFYING并收集连续新鲜观测。',
    }
    execution = dict(fixture=True,session={'turns':[dict(
        observation_reads=reads,phase_context={'logical_time':5},tool_errors=[],
        assessment={'assessment':'suspected'},stop_reason='final_answer')]},state={'transitions':[
        {'from':'PATROLLING','to':'INVESTIGATING'},{'from':'INVESTIGATING','to':'VERIFYING'}]})
    results = []
    for name, answer in answers.items():
        result = judge_case(case, answer, llm_call, execution=execution)
        result["case_id"] = name
        result["fixture"] = True
        result["expected_passed"] = SELF_CHECK_EXPECTED[name]
        result["matched"] = (result["llm_judge_status"] == "ok"
                             and result["passed"] is SELF_CHECK_EXPECTED[name])
        results.append(result)
    negative = results[0]
    passed = all(r["matched"] for r in results)
    return {
        "complete": len(results) == len(SELF_CHECK_EXPECTED),
        "passed": passed,
        "results": results,
        "demo_answer": answers["known_wrong"],
        "deterministic_only_pass": negative["deterministic_pass"],
        "llm_judge_status": negative["llm_judge_status"],
        "llm_judge_score": negative["llm_judge_score"],
        "llm_judge_reason": negative["llm_judge_reason"],
        "double_track_pass": negative["passed"],
        "mismatch": negative["deterministic_pass"] != negative["passed"],
        "explanation": ("正反两项自检均符合预期。" if passed else
                        "评分器未通过正反样本自检，禁止自动采用，等待人工检查。"),
    }
