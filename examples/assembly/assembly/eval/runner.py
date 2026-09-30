"""隔离技能版本的真实模型评测；只读订单文件，不连接真实支付系统。

每个用例新建对话和客户端，工具返回 scenarios/orders.json 中的原始记录。
模型看不到评分字段或参考结论。证据保存在 output/eval/runs/<run_id>。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import config
from ..contracts import EvalReport, sha256_text, tree_hashes, write_json
from .judge import EVALUATOR_VERSION, judge_answer

OUTPUT = config.OUTPUT_DIR / "eval"
MODEL_PARAMETERS = {"temperature": 0, "max_tokens": 2400,
                    "thinking": {"type": "disabled"}}
SYSTEM_PROMPT = (
    "你负责核实订单付款情况。先调用 query_order_payments 读取目标订单的原始流水，"
    "再依据下面的技能回答用户。只使用工具返回的事实，不编造外部查询或已执行的操作。"
    "用简明中文自然语言回答，说明依据和下一步。\n\n当前技能：\n"
)
TOOLS = [{"type": "function", "function": {
    "name": "query_order_payments",
    "description": "只读查询指定订单的应缴金额、逐笔支付流水、渠道状态和商户通知状态。",
    "parameters": {"type": "object", "properties": {
        "order_id": {"type": "string", "description": "用户指定的订单编号"}},
        "required": ["order_id"], "additionalProperties": False}}}]
EXECUTION_PROTOCOL = sha256_text(json.dumps({
    "version": "isolated-payment-agent-v1", "system": SYSTEM_PROMPT,
    "tools": TOOLS, "parameters": MODEL_PARAMETERS,
}, ensure_ascii=False, sort_keys=True))


def _json_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def skill_tree_hash(skill_dir: Path | str) -> str:
    """与 tree_hashes 一起保存；空目录、符号链接不能充当被评技能。"""
    root = Path(skill_dir)
    if not root.is_dir() or root.is_symlink() or not (root / "SKILL.md").is_file():
        raise ValueError("技能必须是含 SKILL.md 的普通目录")
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("被评技能不得包含符号链接")
    return _json_hash(tree_hashes(root))


def load_cases() -> list[dict[str, Any]]:
    cases = json.loads((config.SCENARIOS_DIR / "cases.json").read_text(encoding="utf-8"))
    _validate_cases(cases)
    return cases


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    ids = [case.get("case_id") for case in cases]
    if not cases or any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("用例集不能为空，case_id 必须唯一且非空")
    if any(not isinstance(c.get("prompt"), str) or not c["prompt"].strip() for c in cases):
        raise ValueError("每个用例都需要非空 prompt")


class AnswerBatch(list):
    """保持 list[(case, answer)] 接口，并携带本次真实运行的来源信息。"""

    def __init__(self, rows: list, evaluation_context: dict[str, Any]):
        super().__init__(rows)
        self.evaluation_context = evaluation_context


@dataclass
class EvaluatedReport(EvalReport):
    """保留共享 EvalReport 接口；附加信息供 policy 核对可比性。"""

    evaluation_context: dict[str, Any] = field(default_factory=dict)


def _skill_text(root: Path) -> str:
    parts = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.stat().st_size > 256_000:
            raise ValueError("最小评测 agent 不支持超过 256 KB 的技能文件")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("最小评测 agent 仅支持 UTF-8 文本技能；二进制附件需集成完整运行时") from exc
        parts.append(f"文件 {path.relative_to(root).as_posix()}：\n{text}")
    return "\n\n".join(parts)


def run_answers(skill_dir, cases) -> list[tuple[dict[str, Any], str]]:
    """真实 glm-5.2 工具循环；凭证仅从当前进程环境读取，不自行加载密钥文件。"""
    from openai import OpenAI
    import httpx

    cases = copy.deepcopy(list(cases))
    _validate_cases(cases)
    key = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")
    if not key:
        raise RuntimeError("缺少 GLM_API_KEY 或 BIGMODEL_API_KEY；请先 source ~/.hermes/.env")
    if config.MODEL != "glm-5.2":
        raise ValueError("本次验收固定使用 glm-5.2，请移除 ASSEMBLY_MODEL 覆盖")
    endpoint = urlsplit(config.BASE_URL)
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("模型端点不得内嵌凭证、查询参数或片段")
    config.clear_proxy_for_model()
    root = Path(skill_dir).resolve()
    original_hash = skill_tree_hash(skill_dir)
    data_path = config.SCENARIOS_DIR / "orders.json"
    raw_data = data_path.read_bytes()
    orders = json.loads(raw_data)
    if any(c["case_id"] not in orders for c in cases):
        raise ValueError("用例必须引用当前 orders.json 中存在的订单")
    run_id = uuid.uuid4().hex
    run_dir = OUTPUT / "runs" / run_id
    frozen_skill = run_dir / "skill"
    frozen_skill.mkdir(parents=True)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        target = frozen_skill / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    if skill_tree_hash(frozen_skill) != original_hash:
        raise RuntimeError("复制技能时版本发生变化，停止评测")
    (run_dir / "orders.json").write_bytes(raw_data)
    write_json(run_dir / "cases.json", cases)
    system = SYSTEM_PROMPT + _skill_text(frozen_skill)
    context = {
        "run_started_at": time.time(),
        "run_id": run_id, "model": config.MODEL, "base_url": config.BASE_URL,
        "data_hash": hashlib.sha256(raw_data).hexdigest(),
        "case_set_hash": _json_hash(cases), "execution_protocol": EXECUTION_PROTOCOL,
        "skill_hash": original_hash, "skill_manifest": tree_hashes(frozen_skill),
        "model_parameters": MODEL_PARAMETERS, "isolated": True,
        "isolation": "每用例独立客户端和消息；不加载 Hermes 家目录、历史记忆或其他技能",
        "cases": [{"case_id": c["case_id"], "prompt_hash": sha256_text(c["prompt"])} for c in cases],
        "case_specs": cases,
        "run_directory": str(run_dir), "data_source": "scenarios/orders.json（受控教学订单）",
        "tool_grounded": False, "skill_unchanged": False, "data_unchanged": False,
        "response_models": [], "answer_hashes": {}, "trace_files": [],
    }
    write_json(run_dir / "context.json", context)
    rows, traces = [], []
    for index, case in enumerate(cases):
        started = time.monotonic()
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": case["prompt"]}]
        trace = {"case_id": case["case_id"], "prompt": case["prompt"],
                 "skill_hash": original_hash, "calls": [], "tool_calls": [],
                 "answer": "", "stop_reason": "", "tool_grounded": False}
        try:
            with OpenAI(api_key=key, base_url=config.BASE_URL, max_retries=2,
                        timeout=90, http_client=httpx.Client(trust_env=False, timeout=90)) as client:
                for iteration in range(5):
                    response = client.chat.completions.create(
                        model=config.MODEL, messages=messages, tools=TOOLS,
                        tool_choice={"type": "function", "function": {"name": "query_order_payments"}}
                        if iteration == 0 else "auto", temperature=0, max_tokens=2400,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    if not response.choices:
                        raise RuntimeError("模型未返回 choice")
                    choice = response.choices[0]
                    message = choice.message
                    context["response_models"].append(response.model)
                    call_record = {
                        "response_id": response.id, "model": response.model,
                        "finish_reason": choice.finish_reason, "content": message.content,
                        "tool_calls": [t.model_dump() for t in message.tool_calls or []],
                        "usage": response.usage.model_dump() if response.usage else None,
                    }
                    trace["calls"].append(call_record)
                    if message.tool_calls:
                        assistant_message = {"role": "assistant", "content": message.content,
                                             "tool_calls": call_record["tool_calls"]}
                        messages.append(assistant_message)
                        for tool in message.tool_calls:
                            try:
                                args = json.loads(tool.function.arguments)
                                valid = (tool.function.name == "query_order_payments"
                                         and isinstance(args, dict) and set(args) == {"order_id"}
                                         and isinstance(args["order_id"], str))
                            except (ValueError, TypeError):
                                args, valid = {}, False
                            order_id = args.get("order_id")
                            if valid and order_id == case["case_id"]:
                                result = {"order_id": order_id, **copy.deepcopy(orders[order_id])}
                                trace["tool_grounded"] = True
                            else:
                                result = {"error": "参数错误或订单不属于当前任务，请查询用户指定的订单"}
                            trace["tool_calls"].append({"name": tool.function.name,
                                                        "arguments": args, "result": result})
                            messages.append({"role": "tool", "tool_call_id": tool.id,
                                             "content": json.dumps(result, ensure_ascii=False)})
                    else:
                        trace["answer"] = message.content or ""
                        trace["stop_reason"] = str(choice.finish_reason)
                        break
                else:
                    trace["stop_reason"] = "max_iterations"
        except Exception as exc:
            # 只记异常类型和状态码；SDK 异常正文可能含请求内容或凭证。
            trace["stop_reason"] = "error"
            trace["error"] = {"type": type(exc).__name__,
                              "status_code": getattr(exc, "status_code", None)}
        trace["elapsed_sec"] = round(time.monotonic() - started, 3)
        trace_name = f"{index + 1:02d}-{re.sub(r'[^A-Za-z0-9_-]', '_', case['case_id'])}.json"
        write_json(run_dir / trace_name, trace)
        context["trace_files"].append(trace_name)
        context["answer_hashes"][case["case_id"]] = sha256_text(trace["answer"])
        rows.append((case, trace["answer"]))
        traces.append(trace)
        print(f"{root.name}/{case['case_id']}：工具读取={trace['tool_grounded']}，"
              f"结束={trace['stop_reason']}，{trace['elapsed_sec']}秒", flush=True)
    context["skill_unchanged"] = skill_tree_hash(root) == original_hash
    context["data_unchanged"] = data_path.read_bytes() == raw_data
    context["tool_grounded"] = all(t["tool_grounded"] and t["answer"].strip()
                                  and t["stop_reason"] == "stop" for t in traces)
    context["response_model_confirmed"] = all(m == config.MODEL for m in context["response_models"])
    if not context["response_model_confirmed"]:
        context["tool_grounded"] = False
    context["response_models"] = sorted(set(context["response_models"]))
    context["run_finished_at"] = time.time()
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "answers.json", [{"case_id": c["case_id"], "answer": a} for c, a in rows])
    return AnswerBatch(rows, context)


def evaluate(label, skill_hash, cases, answers) -> EvalReport:
    """完整逐项评分；缺答计失败，重复、额外或错配答案视为输入错误。"""
    cases = list(cases)
    _validate_cases(cases)
    by_id = {}
    for case, answer in answers:
        case_id = case["case_id"]
        if case_id in by_id or case_id not in {c["case_id"] for c in cases}:
            raise ValueError("答案含重复或额外用例")
        expected = next(c for c in cases if c["case_id"] == case_id)
        if case != expected or not isinstance(answer, str):
            raise ValueError("答案对应的用例或答案类型不符")
        by_id[case_id] = answer
    context = copy.deepcopy(getattr(answers, "evaluation_context", {}))
    if context:
        if context.get("data_hash") != hashlib.sha256((config.SCENARIOS_DIR / "orders.json").read_bytes()).hexdigest():
            raise ValueError("订单数据在运行与评分之间发生变化，必须重新运行")
        if context.get("skill_hash") != skill_hash or context.get("case_set_hash") != _json_hash(cases):
            raise ValueError("报告的技能或用例哈希与真实运行不一致")
        if context.get("answer_hashes") != {i: sha256_text(a) for i, a in by_id.items()}:
            raise ValueError("真实模型答案被改动或遗漏，不能沿用来源信息")
    results = [judge_answer(c, by_id.get(c["case_id"], "")) for c in cases]
    return EvaluatedReport(label=label, skill_hash=skill_hash, cases=results,
                           passed=sum(c.passed for c in results), total=len(results),
                           evaluator_version=EVALUATOR_VERSION, evaluation_context=context)
