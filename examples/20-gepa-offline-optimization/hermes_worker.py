#!/usr/bin/env python3
"""每题独立进程：预载完整 Skill，通过真实 Hermes 工具循环访问课程记录。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    root = Path(request["hermes_root"])
    home = Path(request["hermes_home"])
    os.environ["HERMES_HOME"] = str(home)
    sys.path.insert(0, str(root))
    # 不读取项目或用户 .env。只允许父进程显式传来的 DEEPSEEK_API_KEY。
    # 这是环境隔离适配，不改 Hermes 的模型调用或工具循环。
    import hermes_cli.env_loader as env_loader
    original_loader = env_loader.load_hermes_dotenv
    def isolated_loader(*, hermes_home=None, project_env=None):
        return original_loader(hermes_home=home, project_env=None)
    env_loader.load_hermes_dotenv = isolated_loader
    from run_agent import AIAgent
    from agent.skill_commands import build_preloaded_skills_prompt
    from tools.skills_tool import skill_view
    from tools.registry import registry
    skill_path = home / "skills" / "payment-check" / "SKILL.md"
    raw = skill_path.read_bytes()
    payload = json.loads(skill_view("payment-check", preprocess=False))
    loaded = payload.get("raw_content", payload.get("content", ""))
    preloaded, names, missing = build_preloaded_skills_prompt(["payment-check"])
    assert not missing and names == ["payment-check"], (names, missing)
    assert digest(raw) == request["skill_sha256"]
    assert digest(loaded.encode()) == digest(raw), "实际加载文本与文件字节不一致"
    assert loaded.strip() in preloaded, "预载消息没有包含实际 Skill"
    reference_path = skill_path.parent / "references/schema.md"
    reference = json.loads(skill_view("payment-check", file_path="references/schema.md", preprocess=False))
    assert reference.get("success"), reference
    reference_text = reference.get("content", "")
    assert digest(reference_text.encode()) == request["reference_sha256"], "参考文件加载字节不一致"
    preloaded += "\n\n[固定参考文件 references/schema.md，按真实 skill_view 结果预载]\n" + reference_text
    (home / "preloaded-prompt.txt").write_text(preloaded)
    record = {
        "skill_sha256": digest(raw), "loaded_sha256": digest(loaded.encode()),
        "preloaded_prompt_sha256": digest(preloaded.encode()),
        "loaded_names": names, "missing_names": missing,
        "reference_sha256": digest(reference_text.encode()),
        "hermes_entry": "AIAgent.run_conversation",
        "model": "deepseek-flash", "key_source": "DEEPSEEK_API_KEY",
        "llm_called": False, "tool_trace": [],
    }
    tool_trace = record["tool_trace"]
    def query_payment(arguments, **kwargs):
        # 课程记录只含当题材料，既不返回答案，也不读取其他分片。
        records = [r for r in request["records"] if r["order_id"] == arguments.get("order_id")]
        if arguments.get("merchant_id"):
            records = [r for r in records if r["merchant_id"] == arguments["merchant_id"]]
        reply = {"records": records}
        tool_trace.append({"tool": "query_payment", "arguments": arguments, "result": reply})
        return json.dumps(reply, ensure_ascii=False)
    registry.register(
        name="query_payment", toolset="course_payment",
        schema={"name": "query_payment", "description": "只读查询课程订单记录；订单号在不同商户可能重复。", "parameters": {
            "type": "object", "properties": {"order_id": {"type": "string"}, "merchant_id": {"type": "string"}},
            "required": ["order_id"]}},
        handler=query_payment,
    )
    if args.probe:
        from model_tools import get_tool_definitions
        definitions = get_tool_definitions(enabled_toolsets=["course_payment"], quiet_mode=True)
        record["registered_tools"] = [d.get("function", d).get("name") for d in definitions]
        assert set(record["registered_tools"]) == {"query_payment"}, record["registered_tools"]
        record["probe_only"] = True
    else:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY；没有发出模型请求")
        agent = AIAgent(
            model="deepseek-flash", api_key=key, base_url="https://api.deepseek.com",
            provider="custom", api_mode="chat_completions", max_iterations=5,
            enabled_toolsets=["course_payment"], skip_context_files=True,
            skip_memory=True, load_soul_identity=False, quiet_mode=True,
            save_trajectories=False, max_tokens=1800,
            ephemeral_system_prompt=preloaded,
            request_overrides={"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}},
        )
        record["registered_tools"] = [d.get("function", d).get("name") for d in agent.tools]
        assert set(record["registered_tools"]) == {"query_payment"}, "运行前工具白名单检查失败"
        start = time.monotonic()
        record.update(llm_called=True, completed=False, usage=None)
        record.update(api_requests_attempted=0, api_responses_received=0, request_shapes=[], response_statuses=[])
        def flush_record():
            Path(request["response_path"]).write_text(json.dumps(record, ensure_ascii=False, indent=2))
        def request_hook(http_request):
            record["api_requests_attempted"] += 1
            # 只保存需要核验的字段，不保存请求头、凭据或完整请求。
            body = json.loads(http_request.content)
            shape = {"tools": [d.get("function", d).get("name") for d in body.get("tools", [])],
                     "thinking": body.get("thinking"), "temperature": body.get("temperature")}
            record["request_shapes"].append(shape)
            assert set(shape["tools"]) == {"query_payment"}, "HTTP 请求工具白名单检查失败"
            assert shape["thinking"] == {"type": "disabled"}, "thinking 未正确合并入 HTTP 请求"
            flush_record()
        def response_hook(http_response):
            record["api_responses_received"] += 1
            record["response_statuses"].append(http_response.status_code)
            flush_record()
        def observe_client(client):
            for event, hook in (("request", request_hook), ("response", response_hook)):
                hooks = client._client.event_hooks.setdefault(event, [])
                if hook not in hooks:
                    hooks.append(hook)
            return client
        # 本版 Hermes 为请求创建独立客户端，不能只观察 agent.client。
        # 包装当前实例的工厂只添加 httpx 钩子，不改请求、返回值或重试策略。
        create_request_client = agent._create_request_openai_client
        def observed_request_client(*, reason, api_kwargs=None):
            return observe_client(create_request_client(reason=reason, api_kwargs=api_kwargs))
        agent._create_request_openai_client = observed_request_client
        observe_client(agent.client)
        Path(request["response_path"]).write_text(json.dumps(record, ensure_ascii=False, indent=2))
        result = agent.run_conversation(request["prompt"], task_id=request["case_id"])
        record.update({"llm_called": True, "completed": bool(result.get("completed")) and not result.get("failed"), "latency_seconds": time.monotonic() - start,
                       "result": result,
                       "usage": {k: getattr(agent, k, None) for k in ["session_prompt_tokens", "session_completion_tokens", "session_total_tokens", "session_api_calls", "session_estimated_cost_usd", "session_cost_status", "session_cost_source"]}})
    assert digest(skill_path.read_bytes()) == request["skill_sha256"], "执行后 Skill 文件发生变化"
    Path(request["response_path"]).write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
