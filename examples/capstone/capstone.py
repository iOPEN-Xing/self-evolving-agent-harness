"""Skill 层递归闭环演示：真实模型工具循环、同步复盘、评测与跨实例回流。

从仓库根运行：.deps/hermes-agent/.venv/bin/python examples/capstone/capstone.py
密钥从环境变量读取；运行会重建本目录下的 output，不操作个人 Hermes 目录。
"""

import os

for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)

import hashlib
import importlib
import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
sys.path.insert(0, str(HERMES_SRC))
sys.path.insert(0, str(REPO_ROOT / "examples" / "23-final-assembly"))

from openai import OpenAI
from hermes_state import SessionDB
from tools.memory_tool import MemoryStore
from eval.cases import EVAL_CASES, SKILL_V0
from eval.runner import run_eval, compare_versions
from eval.judge import self_check
from versioning import manifest as vm
from versioning import snapshot as vsnap
from versioning import source
from versioning import policy

BASE_URL = "https://api.deepseek.com"
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""
MODEL = "deepseek-flash"
MAX_ROUNDS = 1
WALL_CLOCK_BUDGET_SEC = 900
MAX_JUDGE_RETRIES = 3
STOP_AFTER_CONSECUTIVE_NO_IMPROVEMENT = 2
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
HUB = OUTPUT_DIR / "shared_hub"
SKILL_NAME = "oncall-service-investigation"
_client = None
_started = None

OLD1 = "每轮先看部署状态及整体超时率。部署显示成功时，可按配置文件中的值理解运行配置；"
NEW1 = "每轮先看部署状态及整体超时率。部署显示成功时，仍须核对实例实际生效配置；处置请求受理成功不能证明配置已生效或服务已恢复；"
OLD2 = "整体指标正常时可以按服务正常处理，不必逐个检查实例。"
NEW2 = "整体指标正常时仍须逐个检查实例，结合实例指标与日志核对异常；缺少生效配置或恢复观测时保持未决，不凭整体指标下恢复结论。"

ONCALL_CASES = {
    "INC-A1": {"request_count": 1000, "instances": [
        {"instance_id": "pod-a", "request_count": 600, "status": "healthy"},
        {"instance_id": "pod-b", "request_count": 400, "status": "unresolved"}]},
    "INC-A2": {"request_count": 800, "instances": [
        {"instance_id": "pod-x", "request_count": 800, "status": "unresolved",
         "reload_acceptance": "success", "scenario": "accepted_then_verifying"}]},
    "INC-B1": {"request_count": 900, "instances": [
        {"instance_id": "pod-y", "request_count": 450, "status": "healthy"},
        {"instance_id": "pod-z", "request_count": 450, "status": "healthy"}]},
    "INC-B2": {"request_count": 500, "instances": [
        {"instance_id": "pod-w", "request_count": 500, "status": "healthy"}],
        "recovery_observation": "recovery_observation_missing"},
}


def section(title):
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}", flush=True)


def sub(title):
    print(f"\n  {title}", flush=True)


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(value), encoding="utf-8")


def append_jsonl(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BudgetExceeded(RuntimeError):
    pass


def remaining_budget():
    remaining = WALL_CLOCK_BUDGET_SEC - (time.monotonic() - _started)
    if remaining <= 0:
        raise BudgetExceeded("本轮已达到 wall_clock 预算")
    return remaining


def completion(**kwargs):
    # Disable SDK retries so each network request has an explicit remaining budget.
    timeout = min(120.0, remaining_budget())
    try:
        result = _client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
            extra_body={"thinking": {"type": "disabled"}},
            model=MODEL, **kwargs)
    except Exception:
        remaining_budget()
        raise
    remaining_budget()
    return result.choices[0].message


def call_llm(messages, temperature=0.2, max_tokens=1500):
    message = completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
    return (message.content or "").strip()


def extract_json(raw):
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("未找到 JSON 对象")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("输出不是 JSON 对象")
    return value


def call_llm_retry(messages, temperature, max_tokens, retries=MAX_JUDGE_RETRIES,
                   expect_json=False):
    # The shared runner uses one callback for answers and judges. Only its exact
    # judge prompt requests structured output; ordinary task answers stay free text.
    is_judge = (len(messages) == 1 and messages[0].get("role") == "user"
                and messages[0].get("content", "").startswith("你是严格的评测裁判。"))
    if is_judge:
        # 裁判输出由共享评分器严格解析；首次格式失败就结束该评分项。
        return call_llm(messages, temperature, max_tokens)
    expect_json = expect_json or is_judge
    for attempt in range(retries + 1):
        raw = call_llm(messages, temperature, max_tokens)
        if not expect_json:
            return raw
        try:
            value = extract_json(raw)
            if is_judge and (type(value.get("score")) is not int
                             or not 1 <= value["score"] <= 5
                             or not isinstance(value.get("reason"), str)):
                raise ValueError("裁判 score/reason 字段无效")
            return json.dumps(value, ensure_ascii=False)
        except ValueError as exc:
            print(f"结构化输出解析失败: {exc}; 已重试 {attempt}/{retries}", flush=True)
            if attempt == retries:
                raise ValueError("结构化输出重试耗尽，不使用裁判数字猜测兜底") from exc


def activate_home(home):
    os.environ["HERMES_HOME"] = str(home)
    import hermes_constants
    importlib.reload(hermes_constants)


def make_home(alias):
    home = OUTPUT_DIR / f"HERMES_HOME_{alias}"
    (home / "skills").mkdir(parents=True)
    (home / "memories").mkdir()
    (home / "config.yaml").write_text(
        'model:\n  default: "deepseek-flash"\n  provider: "deepseek"\n'
        'terminal:\n  backend: local\nskills:\n  disabled: []\n', encoding="utf-8")
    print(f"实例 {alias}: {home}")
    return home


def manage_skill(**kwargs):
    from tools.skill_manager_tool import skill_manage
    result = skill_manage(name=SKILL_NAME, **kwargs)
    result = json.loads(result) if isinstance(result, str) else result
    print(f"skill_manage({kwargs['action']}): {dump(result)}")
    if not result.get("success"):
        raise RuntimeError(f"Skill 修改失败: {result}")
    return result


def query_oncall_observations(incident_id: str, *, cases=None):
    cases = ONCALL_CASES if cases is None else cases
    if incident_id not in cases:
        raise ValueError(f"未知告警: {incident_id}")
    return {"incident_id": incident_id, **cases[incident_id]}


class OncallToolLoopAgent:
    """每项任务从空历史开始，保留模型调用、工具回执和最终回答。"""

    def __init__(self, *, skill_md, alias, task_prompt, max_turns=6,
                 tool_schemas=None, tool_dispatch=None, max_tokens=2000):
        self.skill_view = skill_md
        self.alias = alias
        self.task_prompt = task_prompt
        self.max_turns = max_turns
        self.tool_schemas = tool_schemas
        self.tool_dispatch = tool_dispatch
        self.max_tokens = max_tokens

    def run(self):
        messages = [
            {"role": "system", "content": (
                "你是 search-api 的 On-call 值守助手。依据实际工具回执给中文结论，不得编造监控观测。"
                + ("可使用 query_oncall_observations 获取告警与巡检观测。"
                   if self.tool_schemas is None else "按本轮提供的只读工具查询当前窗口观测。") +
                "以下是你持有的完整技能，有技能时按技能核查，无技能时自行判断。\n"
                f"<skill_view>\n{self.skill_view}\n</skill_view>")},
            {"role": "user", "content": self.task_prompt},
        ]
        tools = [{"type": "function", "function": {
            "name": "query_oncall_observations",
            "description": "只读查询 search-api 的告警与巡检观测，返回请求样本数、各实例状态和恢复确认信息。",
            "parameters": {"type": "object", "properties": {"incident_id": {"type": "string"}},
                           "required": ["incident_id"], "additionalProperties": False}}}]
        if self.tool_schemas is not None:
            tools = self.tool_schemas
        turn = {"instance": self.alias, "skill_view": self.skill_view,
                "skill_view_sha256": sha256(self.skill_view), "prompt": self.task_prompt,
                "tool_calls": [], "tool_results": [], "rounds": [],
                "response_text": "", "answer": "", "stop_reason": "max_turns"}
        for number in range(1, self.max_turns + 1):
            print(f"实例 {self.alias}: 工具循环 {number}/{self.max_turns}", flush=True)
            # Keep tool availability and selection policy identical for C's two
            # probes. A missing real query in a loaded task fails validation below.
            message = completion(messages=messages, tools=tools, tool_choice="auto",
                                 temperature=0.2, max_tokens=self.max_tokens)
            calls = [c.model_dump(exclude_none=True) for c in (message.tool_calls or [])]
            record = {"round": number, "response_text": message.content or "",
                      "tool_calls": calls, "tool_results": []}
            turn["rounds"].append(record)
            if not calls:
                turn["answer"] = turn["response_text"] = message.content or ""
                turn["stop_reason"] = "final_answer" if turn["answer"] else "empty_response"
                break
            wire = {"role": "assistant", "content": message.content, "tool_calls": calls}
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning is not None:
                wire["reasoning_content"] = reasoning
            messages.append(wire)
            for call in calls:
                turn["tool_calls"].append(call)
                try:
                    fn = call["function"]
                    args = json.loads(fn["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("未知工具或参数格式错误")
                    if self.tool_dispatch is not None:
                        observation = self.tool_dispatch(fn["name"], args)
                    else:
                        if fn["name"] != "query_oncall_observations":
                            raise ValueError("未知工具或参数格式错误")
                        if set(args) != {"incident_id"} or not isinstance(args["incident_id"], str):
                            raise ValueError("必须提供字符串 incident_id")
                        observation = query_oncall_observations(args["incident_id"])
                    content, error = dump(observation), False
                except (ValueError, KeyError, TypeError) as exc:
                    content, error = dump({"error": str(exc)}), True
                result = {"tool_call_id": call["id"], "content": content, "has_error": error}
                record["tool_results"].append(result)
                turn["tool_results"].append(result)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                print(f"工具调用: {dump(call)}\n工具回执: {content}", flush=True)
        turn["messages"] = messages + ([{"role": "assistant", "content": turn["answer"]}]
                                      if turn["answer"] else [])
        print(f"实例={self.alias} skill_view sha256={turn['skill_view_sha256']}\n"
              f"answer={turn['answer']}\nstop_reason={turn['stop_reason']}", flush=True)
        return turn


def eval_case_observations(case_id):
    """在 capstone 内构造评测观测；每次返回独立数据，不读取评分要求。"""
    if case_id not in {"NI-01", "NI-02", "HO-01", "HO-02", "RG-01", "RG-02"}:
        raise ValueError(f"未知评测用例: {case_id}")
    snapshots = {}
    for tick in (5, 10):
        healthy = dict(timeout_rate=0.002, pool_limit=80, pool_active=12,
                       pool_waiting=0, upstream_timeout_rate=0.001)
        observation = dict(service="search-api", window=dict(start=tick - 5, end=tick),
                           observed_at=tick, available=True,
                           deployment=dict(status="success", revision="r17", action_receipt=None),
                           config=dict(file={"pool_limit": 80}, effective={
                               pod: {"pool_limit": 80} for pod in ("pod-a", "pod-b")}),
                           metrics=dict(overall=dict(healthy), instances={
                               pod: dict(healthy) for pod in ("pod-a", "pod-b")}), logs=[])
        if case_id == "NI-01" or (case_id == "RG-02" and tick == 5):
            for config in observation["config"]["effective"].values():
                config["pool_limit"] = 20
            for metric in [observation["metrics"]["overall"], *observation["metrics"]["instances"].values()]:
                metric.update(timeout_rate=0.12, pool_limit=20, pool_active=20, pool_waiting=35)
            observation["logs"] = [dict(instance=pod, message="connection acquisition wait timeout 2000ms")
                                   for pod in ("pod-a", "pod-b")]
        elif case_id == "NI-02":
            observation["metrics"]["instances"]["pod-b"].update(
                timeout_rate=0.16, pool_active=80, pool_waiting=22)
            observation["logs"] = [dict(instance="pod-b", message="connection acquisition wait timeout 2000ms")]
        elif case_id == "HO-02":
            for metric in [observation["metrics"]["overall"], *observation["metrics"]["instances"].values()]:
                metric.update(timeout_rate=0.11, upstream_timeout_rate=0.11)
            observation["logs"] = [dict(instance="pod-a", message="upstream request timeout; local connection acquired in 2ms")]
        elif case_id == "RG-01":
            if tick == 5:
                observation["observed_at"] = -25
            else:
                observation["available"] = False
                observation["metrics"]["instances"] = {}
        elif case_id == "RG-02":
            observation["window"]["start"] = 9
            observation["deployment"]["action_receipt"] = "已接收重新加载请求"
        snapshots[tick] = observation
    return snapshots


def run_case(skill_text, version, tc) -> Dict:
    """用自身工具循环完成 2 轮隔离评测，只向裁判返回评分所需轨迹。"""
    case_id = tc["id"]
    snapshots = eval_case_observations(case_id)
    session_id = f"eval-{version}-{case_id}"
    state = dict(state="PATROLLING", transitions=[], records=[])
    session = dict(session_id=session_id, source="real_deepseek_toolloop", expected_turns=2, turns=[])
    execution = dict(session=session, state=state)
    path = OUTPUT_DIR / "eval_cases" / version / f"{case_id}.json"
    fields = dict(check_deployment="deployment", read_config="config", query_logs="logs")
    tool_schemas = [{"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"}, "logical_time": {"type": "integer"},
            **({"scope": {"type": "string", "enum": ["overall", "instances"]}}
               if name == "read_metrics" else {})},
            "required": ["service", "logical_time"] + (["scope"] if name == "read_metrics" else []),
            "additionalProperties": False}}}
        for name, description in (
            ("check_deployment", "只读查询当前服务部署状态与处置回执。"),
            ("read_config", "只读查询配置文件与实例实际生效配置。"),
            ("read_metrics", "只读查询当前窗口整体或逐实例指标。"),
            ("query_logs", "只读查询当前窗口服务日志。"))]
    normal_start = normal_end = None

    def transition(target, reason, tick):
        state["transitions"].append({"from": state["state"], "to": target,
                                     "reason": reason, "logical_time": tick})
        state["state"] = target

    try:
        for tick, observation in snapshots.items():
            reads = []
            fresh = observation["available"] and observation["observed_at"] == tick
            metrics = [observation["metrics"]["overall"], *observation["metrics"]["instances"].values()]
            alarm = fresh and any(m["timeout_rate"] > 0.05 for m in metrics)
            normal = fresh and bool(observation["metrics"]["instances"]) and all(
                m["timeout_rate"] < 0.01 and m["pool_waiting"] == 0 for m in metrics)
            if alarm and state["state"] == "PATROLLING":
                transition("INVESTIGATING", "当前窗口触发超时告警", tick)
            if normal and state["state"] != "PATROLLING":
                start = observation["window"]["start"]
                if normal_end != start:
                    normal_start = start
                normal_end = tick
            else:
                normal_start = normal_end = None
            normal_minutes = tick - normal_start if normal_start is not None else 0
            context = dict(service="search-api", logical_time=tick, window=observation["window"],
                           state=state["state"], fresh=fresh, alarm=bool(alarm),
                           recovery_window_minutes=10, normal_minutes=normal_minutes,
                           prior_records=list(state["records"]))

            def dispatch(name, arguments):
                required = {"service", "logical_time"} | ({"scope"} if name == "read_metrics" else set())
                if name not in {*fields, "read_metrics"} or set(arguments) != required:
                    raise ValueError("未知工具或参数不完整")
                if arguments["service"] != "search-api" or type(arguments["logical_time"]) is not int or arguments["logical_time"] != tick:
                    raise ValueError("只允许查询当前服务与当前窗口")
                current = query_oncall_observations(case_id, cases={case_id: observation})
                payload = {key: current[key] for key in ("service", "window", "observed_at", "available")}
                if name == "read_metrics":
                    scope = arguments["scope"]
                    if scope not in ("overall", "instances"):
                        raise ValueError("指标范围不合法")
                    payload.update(scope=scope, data=current["metrics"][scope])
                else:
                    payload["data"] = current[fields[name]]
                reads.append(dict(tool=name, arguments=arguments, observation=payload))
                return payload

            prompt = (
                "请按技能完成本轮只读值守，实际查询后给出报告。时间单位为模拟推进的逻辑分钟。"
                "最终只输出 JSON，字段为 assessment、report、next_state。"
                "assessment 可取 healthy、suspected、unresolved、recovering、recovered；"
                "report 用中文说明服务、窗口、事实、判断及待复查事项；"
                "next_state 可取 PATROLLING、INVESTIGATING、VERIFYING。"
                "调查后可进入 VERIFYING 持续复查；缺少观测且未触发告警时可保留未决继续巡检。\n"
                + dump(context))
            turn = OncallToolLoopAgent(skill_md=skill_text, alias=f"{session_id}-{tick}",
                                      task_prompt=prompt, tool_schemas=tool_schemas,
                                      tool_dispatch=dispatch, max_tokens=4000).run()
            turn.update(phase_context=context, observation_reads=reads,
                        tool_errors=[r for r in turn["tool_results"] if r["has_error"]])
            report = {}
            try:
                raw_report = turn["response_text"].strip()
                lines = raw_report.splitlines()
                if (len(lines) >= 3 and lines[0].strip().lower() in ("```json", "```")
                        and lines[-1].strip() == "```"):
                    raw_report = "\n".join(lines[1:-1]).strip()
                try:
                    report = json.loads(raw_report)
                except json.JSONDecodeError:
                    # 仅容忍最终报告外的围栏或说明文字；字段校验仍按原规则执行。
                    start, end = raw_report.find("{"), raw_report.rfind("}")
                    report = json.loads(raw_report[start:end + 1])
                if (not isinstance(report, dict) or set(report) != {"assessment", "report", "next_state"}
                        or report["assessment"] not in ("healthy", "suspected", "unresolved", "recovering", "recovered")
                        or report["next_state"] not in ("PATROLLING", "INVESTIGATING", "VERIFYING")
                        or not isinstance(report["report"], str) or not report["report"].strip()):
                    raise ValueError("报告字段不合法")
            except (ValueError, TypeError) as exc:
                report = {}
                turn["assessment_error"] = str(exc)
                if turn["stop_reason"] == "final_answer":
                    turn["stop_reason"] = "invalid_report"
            turn["assessment"] = report
            requested = report.get("next_state")
            denied = None
            queried = any(r["tool"] == "read_metrics" for r in reads)
            if requested and requested != state["state"]:
                if (state["state"] == "INVESTIGATING" and requested == "VERIFYING"
                        and queried and turn["stop_reason"] == "final_answer" and not turn["tool_errors"]):
                    transition(requested, "模型完成当前调查并建议复查", tick)
                elif (state["state"] == "VERIFYING" and requested == "PATROLLING"
                      and queried and normal_minutes >= 10 and report["assessment"] == "recovered"
                      and turn["stop_reason"] == "final_answer" and not turn["tool_errors"]):
                    transition(requested, "连续新鲜观测满足恢复窗口且模型已复核", tick)
                else:
                    denied = f"当前观测与状态不支持 {state['state']} -> {requested}"
            record = dict(logical_time=tick, assessment=report, observations=reads,
                          response_text=turn["response_text"], state_after=state["state"],
                          transition_denied=denied)
            turn["state_record"] = record
            state["records"].append(record)
            session["turns"].append(turn)
            write_json(path, execution)
    finally:
        session["num_turns"] = len(session["turns"])
        session["state_after"] = state["state"]
        write_json(path, execution)
    return execution


def run_task(home, alias, skill, incident_id, label, upload=True):
    activate_home(home)
    prompt = f"请核查 search-api 记录 {incident_id} 对应窗口的服务状态，若有异常或恢复尚未确认，说明下一步。"
    turn = OncallToolLoopAgent(skill_md=skill, alias=alias, task_prompt=prompt).run()
    sid = f"{alias}-{label}-{incident_id}"
    turn["session_id"] = sid
    turn["skill_state"] = label
    db = SessionDB(db_path=home / "state.db")
    try:
        db.create_session(sid, "capstone", model=MODEL, cwd=str(REPO_ROOT))
        db.set_session_title(sid, f"{alias} {label} {incident_id}")
        for message in turn["messages"]:
            db.append_message(sid, message["role"], content=message.get("content"),
                              tool_calls=message.get("tool_calls"),
                              tool_call_id=message.get("tool_call_id"))
        print(f"SessionDB 落库: {sid}, 消息数={len(db.get_messages(sid))}")
    finally:
        db.close()
    write_json(OUTPUT_DIR / "turns" / f"{sid}.json", turn)
    if upload:
        append_jsonl(HUB / "sessions.jsonl", turn)
        print(f"会话上传共享中心: {sid}")
    queried_target = any(not r["has_error"] and json.loads(r["content"]).get("incident_id") == incident_id
                         for r in turn["tool_results"])
    if skill and (turn["stop_reason"] != "final_answer" or not queried_target):
        raise RuntimeError(f"{sid} 未完成真实工具查询后的最终回答，轨迹已保留")
    return turn


def review_and_patch(home, turns, v0):
    activate_home(home)
    print("单讲 11 已演示真实 daemon 线程异步复盘（turn_finalizer）；"
          "capstone 为端到端演示改成同步调 LLM；复盘的 LLM 推理和 skill_manage(patch) 本身是真实的。")
    target = v0.replace(OLD1, NEW1).replace(OLD2, NEW2)
    if OLD1 not in v0 or OLD2 not in v0:
        raise ValueError("基线与 spec 的 2 处修订目标不一致")
    prompt = (
        '复盘以下 A/B 实际经历。只输出 1 个 patch JSON {"old_string":"...","new_string":"..."}。'
        'old_string 必须在原技能中唯一匹配。将下面 2 句精确替换，保留其余内容。'
        '经历即使没有答错，也不要虚构错误；此次修订的收益由后续评测判断。\n'
        f'{OLD1}\n替换为: {NEW1}\n{OLD2}\n替换为: {NEW2}\n'
        f'原技能:\n{v0}\n实际经历:\n{dump(turns)}')
    patch = None
    try:
        raw = call_llm_retry([{"role": "user", "content": prompt}], 0.2, 2500, expect_json=True)
        write_json(OUTPUT_DIR / "review_patch.json", {"raw": raw})
        candidate = extract_json(raw)
        old, new = candidate.get("old_string"), candidate.get("new_string")
        if (isinstance(old, str) and old and isinstance(new, str)
                and v0.count(old) == 1 and v0.replace(old, new, 1) == target):
            patch = candidate
    except ValueError as exc:
        print(f"复盘 JSON 无效: {exc}")
    if patch:
        manage_skill(action="patch", old_string=patch["old_string"], new_string=patch["new_string"])
        source = "llm"
    else:
        print("LLM patch 未精确覆盖指定修订，使用 spec 的 2 组确定性 patch。")
        for old, new in ((OLD1, NEW1), (OLD2, NEW2)):
            manage_skill(action="patch", old_string=old, new_string=new)
        source = "deterministic_fallback"
    v1 = (home / "skills" / SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
    if v1 != target:
        raise RuntimeError("patch 后磁盘内容与指定修订不一致")
    write_json(OUTPUT_DIR / "patch_result.json", {"source": source, "v0": v0, "v1": v1})
    print(f"patch 来源={source}, v0={sha256(v0)}, v1={sha256(v1)}")
    return v1


def on_no_update(decision, cmp):
    if decision["decision"] == "ADOPT":
        return None
    record = {"decision": decision["decision"], "adopted": "v0",
              "status": ("WAIT_FOR_MANUAL_REVIEW" if decision["decision"] == "REJECT" else "WAIT_FOR_NEXT_BATCH"),
              "failed_checks": [c for c in decision["checks"] if not c["pass"]],
              "reason": decision["reason_summary"], "comparison": cmp}
    append_jsonl(OUTPUT_DIR / "decision_log.jsonl", record)
    print(dump(record))
    print(f"status={record['status']}: 本轮保留 v0，不发布候选 v1。")
    return record["status"]


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def snapshot_demo(home, adopted, content):
    skill_dir = home / "skills" / SKILL_NAME
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    snap_root = OUTPUT_DIR / "skill_tree_snapshots" / adopted
    snap_tree = snap_root / "tree"
    shutil.copytree(skill_dir, snap_tree)
    hashes = tree_hashes(skill_dir)
    write_json(snap_root / "manifest.json", hashes)
    print(f"整目录 copytree 快照: {snap_tree}\n逐文件 sha256: {dump(hashes)}")
    snapshots = OUTPUT_DIR / "snapshots"
    db_path = home / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        before_ids = {s["id"] for s in db.search_sessions(limit=100000)}
        state = vsnap.take_snapshot(db, adopted, content, snapshots)
        print(f"state.db 逻辑快照: {dump(state)}")
        db.create_session("after-snapshot", "capstone", model=MODEL)
        db.set_session_title("after-snapshot", "快照后新增")
        db.append_message("after-snapshot", "user", content="快照后新增，恢复后应消失")
        if "after-snapshot" not in {s["id"] for s in db.search_sessions(limit=100000)}:
            raise RuntimeError("快照后新增会话未落库")
    finally:
        db.close()
    (skill_dir / "SKILL.md").write_text("故意改坏的技能", encoding="utf-8")
    (skill_dir / "after-snapshot.txt").write_text("快照后新增文件", encoding="utf-8")
    expected = json.loads((snap_root / "manifest.json").read_text(encoding="utf-8"))
    if tree_hashes(snap_tree) != expected:
        raise RuntimeError("快照目录校验失败，拒绝恢复")
    print(f"改坏后目录与快照不一致: {tree_hashes(skill_dir) != expected}")
    shutil.rmtree(skill_dir)
    shutil.copytree(snap_tree, skill_dir)
    actual = tree_hashes(skill_dir)
    for name, digest in expected.items():
        print(f"恢复文件 {name}: sha256={actual.get(name)} 一致={actual.get(name) == digest}")
    if actual != expected:
        raise RuntimeError("恢复后文件清单或 sha256 不一致")
    restored = vsnap.restore_snapshot(db_path, adopted, snapshots)
    db = SessionDB(db_path=db_path)
    try:
        after_ids = {s["id"] for s in db.search_sessions(limit=100000)}
    finally:
        db.close()
    if after_ids != before_ids or "after-snapshot" in after_ids:
        raise RuntimeError("state.db 恢复后的会话集合不一致")
    if vsnap.restore_skill(adopted, snapshots) != (skill_dir / "SKILL.md").read_text(encoding="utf-8"):
        raise RuntimeError("2 条快照路径的 Skill 内容不一致")
    print(f"state.db 恢复: {dump(restored)}\n快照后新增会话消失=True; Skill 整目录哈希一致=True")
    return {"skill_tree_verified": True, "extra_session_removed": True, "state": restored}


def has_query(turn):
    return any(c["function"]["name"] == "query_oncall_observations" for c in turn["tool_calls"])


def cross_instance(home_c, adopted, content):
    sub("C 初始 unloaded: skills 目录为空，尚未读取 hub")
    if any((home_c / "skills").iterdir()):
        raise RuntimeError("C 的 skills 目录不是空目录")
    unloaded = run_task(home_c, "C", "", "INC-B1", "unloaded", upload=False)
    sub("发布采用版并由 C 读取、校验、落盘")
    hub_dir = HUB / SKILL_NAME
    hub_dir.mkdir(parents=True)
    (hub_dir / "SKILL.md").write_text(content, encoding="utf-8")
    write_json(hub_dir / "meta.json", {"skill": SKILL_NAME, "version": adopted,
                                      "hash": vm.hash_skill(content)})
    loaded_skill = (hub_dir / "SKILL.md").read_text(encoding="utf-8")
    meta = json.loads((hub_dir / "meta.json").read_text(encoding="utf-8"))
    valid = (meta["skill"] == SKILL_NAME and meta["version"] == adopted
             and vm.hash_skill(loaded_skill) == meta["hash"])
    print(f"C 读取 hub: sha256={sha256(loaded_skill)}, manifest hash={meta['hash']}, 验证={valid}")
    if not valid:
        raise RuntimeError("共享 Skill 的 hash 或元数据验证失败")
    local = home_c / "skills" / SKILL_NAME / "SKILL.md"
    local.parent.mkdir()
    local.write_text(loaded_skill, encoding="utf-8")
    loaded = run_task(home_c, "C", local.read_text(encoding="utf-8"), "INC-B1", "loaded")
    comparison = {"unloaded_answer": unloaded["answer"], "loaded_answer": loaded["answer"],
                  "answers_differ": unloaded["answer"] != loaded["answer"],
                  "unloaded_query_oncall_observations": has_query(unloaded),
                  "loaded_query_oncall_observations": has_query(loaded),
                  "unloaded_tool_calls": unloaded["tool_calls"], "loaded_tool_calls": loaded["tool_calls"]}
    print(f"C unloaded vs loaded 实际轨迹对比:\n{dump(comparison)}")
    print("2 次运行不预设差异；回答文本不同不等于能力提升，无技能也可能正确调用工具。")
    write_json(OUTPUT_DIR / "c_probe_comparison.json", comparison)
    return unloaded, loaded


def hub_counts():
    path = HUB / "sessions.jsonl"
    counts = Counter()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts[json.loads(line)["instance"]] += 1
    return dict(counts)


def main():
    global _client, _started
    if not API_KEY:
        raise SystemExit("请先设置环境变量 DEEPSEEK_API_KEY")
    _started = time.monotonic()
    _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
    previous_home = os.environ.get("HERMES_HOME")
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    source_manifest = source.fingerprint(REPO_ROOT, __file__, (HERMES_SRC,))
    write_json(OUTPUT_DIR / "source_manifest.json", source_manifest)
    summary = {"source_sha256": source_manifest["sha256"], "adopted": "v0", "decision": None, "v0_overall_pass_rate": None,
               "v1_overall_pass_rate": None, "set_pass_rates": {}, "skill_view_hashes": {},
               "hub_sessions_by_instance": {}, "stop_reason": None, "rounds_completed": 0,
               "consecutive_no_improvement": 0, "elapsed": 0,
               "scope": "Skill 层递归闭环演示", "status": "RUNNING"}
    no_improvement = 0
    try:
        section("阶段 0: 建立独立实例与 v0 基线")
        homes = {alias: make_home(alias) for alias in ("A", "B", "C")}
        for alias in ("A", "B"):
            activate_home(homes[alias])
            manage_skill(action="create", content=SKILL_V0)
            path = homes[alias] / "skills" / SKILL_NAME / "SKILL.md"
            if path.read_text(encoding="utf-8") != SKILL_V0:
                raise RuntimeError(f"实例 {alias} 基线写入不一致")
        manifest_path = OUTPUT_DIR / "versions.json"
        baseline = vm.record_version(manifest_path, SKILL_NAME, SKILL_V0, decision="ADOPT",
                                     eval_report="", notes="演示初始基线 v0，不代表本轮评测通过")
        print(f"基线 v0 登记: manifest 内部编号={baseline['version']}")
        for round_number in range(1, MAX_ROUNDS + 1):
            remaining_budget()
            section(f"阶段 1: 第 {round_number} 轮前台真实工具循环，A/B 上传经历")
            turns = []
            for alias, incidents in (("A", ("INC-A1", "INC-A2")), ("B", ("INC-B1", "INC-B2"))):
                skill = (homes[alias] / "skills" / SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
                for incident in incidents:
                    turns.append(run_task(homes[alias], alias, skill, incident, f"r{round_number}-v0"))
                summary["skill_view_hashes"][alias] = sha256(skill)
            section("阶段 2: A/B 复盘与真实 Skill patch")
            v1 = review_and_patch(homes["A"], turns, SKILL_V0)
            (OUTPUT_DIR / "candidate_skill.md").write_text(v1, encoding="utf-8")
            # 隔离评测前先恢复加载目录；候选不得因解析失败或异常而留作采用版。
            (homes["A"] / "skills" / SKILL_NAME / "SKILL.md").write_text(SKILL_V0, encoding="utf-8")
            sub("记忆 add: 保存待确认发现")
            store = MemoryStore()
            store.load_from_disk()
            initial = "初步发现：v0 缺少生效配置核对与逐实例巡检，待评测确认"
            added = store.add("memory", initial)
            print(dump(added))
            if not added.get("success"):
                raise RuntimeError("记忆 add 失败")
            section(f"阶段 3: 隔离评测（每版全部 {len(EVAL_CASES)} 个用例）及评分自检")
            v0_out = run_eval(SKILL_V0, "v0", call_llm_retry, run_case=run_case)
            write_json(OUTPUT_DIR / "eval_v0.json", v0_out)
            v1_out = run_eval(v1, "v1", call_llm_retry, run_case=run_case)
            write_json(OUTPUT_DIR / "eval_v1.json", v1_out)
            check = self_check(call_llm_retry)
            cmp = compare_versions(v0_out, v1_out)
            print(f"self_check: {dump({k: v for k, v in check.items() if k != 'explanation'})}")
            write_json(OUTPUT_DIR / "eval_report.json", {
                "source_sha256": source_manifest["sha256"],
                "v0": v0_out, "v1": v1_out, "comparison": cmp, "self_check": check})
            for version in ("v0", "v1"):
                scores = cmp[f"{version}_summary"]
                overall = scores["overall"]
                summary[f"{version}_overall_pass_rate"] = overall["passed"] / overall["total"]
                summary["set_pass_rates"][version] = {k: v["pass_rate"] for k, v in scores.items()
                                                       if k != "overall"}
            print(dump({"v0": cmp["v0_summary"], "v1": cmp["v1_summary"]}))
            section("阶段 4: policy.decide 版本采用或回退")
            decision = policy.decide(cmp, self_check=check, v0_eval=v0_out, v1_eval=v1_out)
            if not source.unchanged(source_manifest, REPO_ROOT):
                decision = {"decision": "REJECT", "status": "WAIT_FOR_MANUAL_REVIEW", "checks": [],
                            "reason_summary": "运行中源码发生变化，禁止自动采用"}
            decision["source_sha256"] = source_manifest["sha256"]
            write_json(OUTPUT_DIR / "decision.json", decision)
            for item in decision["checks"]:
                print(f"{item['check']}: pass={item['pass']} {item['detail']}")
            print(f"决策={decision['decision']}: {decision['reason_summary']}")
            summary["decision"] = decision["decision"]
            if decision["decision"] != "ADOPT":
                no_improvement += 1
            summary["decision_checks"] = decision["checks"]
            summary["next_review_status"] = on_no_update(decision, cmp)
            adopted = "v1" if decision["decision"] == "ADOPT" else "v0"
            content = v1 if adopted == "v1" else SKILL_V0
            summary["adopted"] = adopted
            revised = store.replace("memory", initial,
                                    f"已确认：候选补齐生效配置核对与逐实例巡检；决策={decision['decision']}；"
                                    f"采用={adopted}；{decision['reason_summary']}")
            print(f"记忆 replace: {dump(revised)}")
            if not revised.get("success"):
                raise RuntimeError("记忆 replace 失败")
            print((homes["A"] / "memories" / "MEMORY.md").read_text(encoding="utf-8"))
            entry = vm.record_version(manifest_path, SKILL_NAME, v1,
                                      decision=decision["decision"], eval_report="eval_report.json",
                                      notes=f"候选 v1；采用 {adopted}；{decision['reason_summary']}")
            latest = vm.latest_adopted(vm.load_manifest(manifest_path))
            if latest["skill_hash"] != vm.hash_skill(content):
                raise RuntimeError("manifest 最近采用版与实际采用内容不一致")
            print(f"候选登记={entry['version']}; 实际采用={adopted}, hash={latest['skill_hash']}")
            section("阶段 5: 整目录 Skill 快照与 state.db 恢复")
            summary["snapshot_verification"] = snapshot_demo(homes["A"], adopted, content)
            remaining_budget()
            section("阶段 6: C 从共享中心加载采用版，同任务对照与经历回流")
            unloaded, loaded = cross_instance(homes["C"], adopted, content)
            summary["skill_view_hashes"]["C_unloaded"] = unloaded["skill_view_sha256"]
            summary["skill_view_hashes"]["C_loaded"] = loaded["skill_view_sha256"]
            counts = hub_counts()
            print(f"下一轮 review 可见性: {dump(counts)}")
            if counts.get("C", 0) < 1:
                raise RuntimeError("共享池中缺少 C 的经历")
            print("C 的 loaded 会话已出现在共享池，下一轮 evolve 可以读取；本轮不再触发 review。")
            summary["rounds_completed"] = round_number
            if decision["decision"] in ("ADOPT", "REJECT"):
                reason = decision["decision"]
            else:
                reason = ("consecutive_no_improvement" if
                          no_improvement >= STOP_AFTER_CONSECUTIVE_NO_IMPROVEMENT else None)
            if reason is None and round_number >= MAX_ROUNDS:
                reason = "max_rounds"
            if time.monotonic() - _started > WALL_CLOCK_BUDGET_SEC:
                reason = "wall_clock"
            if reason:
                summary["stop_reason"] = reason
                break
        summary["status"] = "COMPLETED"
    except BudgetExceeded as exc:
        summary.update(status="INCOMPLETE", stop_reason="wall_clock", error=str(exc))
        print(f"预算停止: {exc}；不将未完成阶段记为成功。")
    except Exception as exc:
        summary.update(status="INCOMPLETE", stop_reason="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if summary["decision"] is None:
            summary["decision"] = "REJECT"
            summary["next_review_status"] = "WAIT_FOR_MANUAL_REVIEW"
            write_json(OUTPUT_DIR / "decision.json", {
                "decision": "REJECT", "status": "WAIT_FOR_MANUAL_REVIEW",
                "checks": [{"check": "evaluation_complete", "pass": False,
                            "detail": "运行异常或超出预算，评测未完成"}],
                "reason_summary": summary.get("error", "评测未完成"),
                "source_sha256": source_manifest["sha256"]})
        summary["hub_sessions_by_instance"] = hub_counts()
        summary["consecutive_no_improvement"] = no_improvement
        # Keep the spec's output spelling as an alias, alongside the canonical key.
        summary["consistent_no_improvement"] = no_improvement
        summary["elapsed"] = round(time.monotonic() - _started, 3)
        write_json(OUTPUT_DIR / "run_summary.json", summary)
        section("运行汇总: output/run_summary.json")
        print(dump(summary))
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        import hermes_constants
        importlib.reload(hermes_constants)
        _client.close()
        print("边界：跨实例用本地共享目录作生产存储的等价后端（仅演示读写语义）；"
              "评测集是参考骨架；后台复盘此处同步调用。")


if __name__ == "__main__":
    main()
