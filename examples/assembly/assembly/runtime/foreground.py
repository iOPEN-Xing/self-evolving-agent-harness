"""真实 Hermes 前台；只从会话结果提取记录，不生成模型或工具回执。"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import config, contracts
from .payment_tool import TOOLSET, register_payment_tool

if TYPE_CHECKING:
    from run_agent import AIAgent

_PROCESS_HOME: Path | None = None


def make_agent(instance: str, home_dir: Path) -> AIAgent:
    """在导入 Hermes 前固定独立 home；不同 home 必须另开进程。"""
    global _PROCESS_HOME
    if not re.fullmatch(r"[A-Za-z0-9_-]+", instance):
        raise ValueError("instance 只能包含字母、数字、下划线和连字符")
    home = Path(home_dir).expanduser().resolve()
    allowed = (config.OUTPUT_DIR / "homes").resolve()
    if not home.is_relative_to(allowed) or home == allowed:
        raise ValueError(f"home_dir 必须位于 {allowed} 的实例子目录")
    if home != allowed / instance:
        raise ValueError("home_dir 必须等于 output/homes/<instance>")
    if _PROCESS_HOME is not None and _PROCESS_HOME != home:
        raise RuntimeError("Hermes 缓存部分进程级路径；不同 home 请使用独立进程")
    if _PROCESS_HOME is None and any(
        name in sys.modules for name in ("run_agent", "model_tools", "hermes_state")
    ):
        raise RuntimeError("请在导入任何 Hermes 模块前调用 make_agent，避免路径已被缓存")

    key = config.api_key()
    config.clear_proxy_for_model()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / "cache")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    _PROCESS_HOME = home

    # 只维护本演示拥有的配置项；不保存密钥，也不修改 ~/.hermes/config.yaml。
    import yaml

    cfg_path = home / "config.yaml"
    settings = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    settings = settings or {}
    sections = {
        "model": {"default": config.MODEL, "provider": "deepseek", "base_url": config.BASE_URL},
        "memory": {"memory_enabled": True, "user_profile_enabled": True,
                   "nudge_interval": 1, "provider": ""},
        "skills": {"creation_nudge_interval": 1, "external_dirs": []},
        "agent": {"environment_probe": False},
        "sessions": {"write_json_snapshots": False},
        "curator": {"enabled": False},
    }
    for section, values in sections.items():
        settings.setdefault(section, {}).update(values)
    # 上游默认把自定义工具放到 tool_search/tool_call 桥后；本演示直接
    # 暴露支付函数，使调用记录中的工具名和参数可直接核对。
    settings.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = "off"
    settings.setdefault("auxiliary", {})["background_review"] = {"provider": "auto"}
    cfg_path.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")

    sys.path.insert(0, str(config.HERMES_SRC))
    from run_agent import AIAgent
    from hermes_state import SessionDB

    register_payment_tool()
    db = SessionDB(home / "state.db")
    agent = AIAgent(
        model=config.MODEL, provider="deepseek", api_key=key, base_url=config.BASE_URL,
        api_mode="chat_completions", max_iterations=12, max_tokens=4096,
        enabled_toolsets=[TOOLSET, "memory", "skills"],
        quiet_mode=True, save_trajectories=False, skip_context_files=True,
        session_db=db,
        request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
        ephemeral_system_prompt=(
            "你负责支付核查。每个新订单必须实际调用 query_order_payments。"
            "只把 status=success 的流水计入成功金额，区分渠道受理和最终到账，"
            "区分付款状态和商户通知。status=success 表示该笔支付最终成功到账；"
            "status=processing 尚未最终成功，即使 channel_acceptance=success 也只代表受理。"
            "merchant_notification_missing 只表示通知缺失，不改变成功付款事实，"
            "不能以商户回调是否到达作为付款到账的判据。未返回的字段只表示未提供。"
            "依据工具回执回答，不能编造查询结果。用户可见回答及保存的方法、偏好均使用中文，"
            "保存技能时 YAML description 和正文也使用中文，name 保留英文标识符。"
            "处理用户的订单查询时，只调用支付查询工具，最终答复必须包含订单、成功金额、"
            "是否付清及必要的下一步；不要在该轮保存记忆或技能，也不要汇报复盘是否完成。"
            "只有收到独立的 Review the conversation 复盘提示时，才使用记忆和技能工具保存经验。"
        ),
    )
    agent.memory_notifications = "verbose"
    agent._runtime_home = home
    agent._runtime_instance = instance
    agent._runtime_db = db
    if not {"query_order_payments", "memory", "skill_manage"} <= agent.valid_tool_names:
        agent.close()
        db.close()
        raise RuntimeError(f"Hermes 未实际暴露支付、记忆或技能工具；实际工具：{sorted(agent.valid_tool_names)}")
    if agent._memory_nudge_interval != 1 or agent._skill_nudge_interval != 1:
        raise RuntimeError("后台复盘间隔未从隔离配置正确加载")
    return agent


def tool_records(messages: list[dict[str, Any]]) -> list[contracts.ToolCallRecord]:
    """按 tool_call_id 关联真实 assistant 调用和 tool 回执。"""
    results = {m.get("tool_call_id"): m.get("content", "")
               for m in messages if m.get("role") == "tool"}
    records = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments", "{}")
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(args, dict):
                raise ValueError("真实工具参数不是对象，不能静默重写")
            raw_result = results.get(call["id"])
            result = raw_result if isinstance(raw_result, str) else json.dumps(raw_result, ensure_ascii=False)
            try:
                parsed = json.loads(result)
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            error = raw_result is None or (isinstance(parsed, dict) and bool(
                parsed.get("error") or parsed.get("is_error") or parsed.get("success") is False
            ))
            records.append(contracts.ToolCallRecord(
                id=call["id"], name=function.get("name", ""), arguments=args,
                result=result, has_error=error,
            ))
    return records


def run_task(agent: AIAgent, instance: str, prompt: str) -> contracts.TaskRun:
    """显式传递同一 agent 的历史；每个 TaskRun 只包含当前轮的工具调用。"""
    if instance != agent._runtime_instance:
        raise ValueError("instance 与构造 agent 时不一致")
    if Path(os.environ.get("HERMES_HOME", "")).resolve() != agent._runtime_home:
        raise RuntimeError("HERMES_HOME 在 agent 生命周期中发生变化")
    history = copy.deepcopy(agent._session_messages)
    prior_ids = {c.id for c in tool_records(history)}
    skill_file = agent._runtime_home / "skills" / config.SKILL_NAME / "SKILL.md"
    skill_hash = contracts.sha256_text(skill_file.read_text(encoding="utf-8")) if skill_file.exists() else None
    start_at = time.time()
    start = time.perf_counter()
    result = agent.run_conversation(user_message=prompt, conversation_history=history or None)
    returned = time.perf_counter()
    agent._runtime_last_timing = {
        "started_monotonic": start, "returned_monotonic": returned,
        "started_at": start_at, "returned_at": time.time(),
    }
    agent._runtime_last_result = result
    reason = str(result.get("turn_exit_reason") or "")
    if result.get("interrupted"):
        stop = "interrupted"
    elif result.get("failed") or result.get("error"):
        stop = "error"
    elif "max_iterations" in reason or "budget" in reason:
        stop = "max_iterations"
    elif result.get("completed") and result.get("final_response"):
        stop = "final_answer"
    else:
        stop = reason or "error"
    return contracts.TaskRun(
        session_id=str(result.get("session_id") or agent.session_id), instance=instance,
        prompt=prompt, skill_hash=skill_hash, answer=result.get("final_response") or "",
        stop_reason=stop, tool_calls=[c for c in tool_records(agent._session_messages) if c.id not in prior_ids],
        elapsed_sec=returned - start, foreground_elapsed_sec=returned - start,
    )
