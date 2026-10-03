#!/usr/bin/env python3
"""Session 回查：把模型叙述、工具请求和工具回执逐项对照。"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
sys.path.insert(0, str(HERMES_SRC))
os.chdir(HERMES_SRC)
# 每次运行保留独立记录，避免上一次的 Memory 改变本次观察。
RUN_HOME = HERE / ".hermes-home" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
os.environ["HERMES_HOME"] = str(RUN_HOME)
(RUN_HOME / "memories").mkdir(parents=True, exist_ok=True)

from hermes_state import SessionDB  # noqa: E402
from run_agent import AIAgent  # noqa: E402
from tools.session_search_tool import session_search  # noqa: E402


def banner(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def paired_calls(messages):
    """检查本次真实记录中的请求与回执，不预设模型调用工具的次数。"""
    calls = {}
    receipts = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            require(call["id"] not in calls, "工具调用编号重复")
            calls[call["id"]] = call["function"]["name"]
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            require(call_id in calls, "工具回执找不到此前的调用")
            require(call_id not in receipts, "同一调用出现重复回执")
            receipts[call_id] = message
    require(calls.keys() == receipts.keys(), "工具调用缺少回执")
    require("read_file" in calls.values(), "本轮没有实际读取文件")
    for call_id, name in calls.items():
        result = json.loads(receipts[call_id]["content"])
        require(not result.get("error"), f"工具 {name} 执行失败")
        if name == "memory":
            require(result.get("success") is True, "Memory 写入未成功")
    return calls


def discover_hit(db, sid, since, until):
    """关键词不相关时改词，并依据元数据筛选时间；不虚构日期参数。"""
    for query, lower, upper in [("rollback", None, None), ("Orion", since, until),
                                 ("PostgreSQL", since, until)]:
        print(f"[搜索] query={query!r}；会话时间范围={lower}..{upper}")
        discovery = json.loads(session_search(query=query, db=db, limit=3, sort="newest"))
        require(discovery.get("success"), "历史搜索执行失败，不能当作未命中")
        relevant = []
        for candidate in discovery.get("results", []):
            meta = db.get_session(candidate["session_id"]) or {}
            at = meta.get("started_at")
            print(f"  session_id={candidate['session_id']} "
                  f"match_message_id={candidate.get('match_message_id')} started_at={at}")
            if candidate["session_id"] != sid:
                continue
            if lower is not None and (at is None or not lower <= float(at) <= upper):
                continue
            relevant.append(candidate)
        if relevant:
            hit = relevant[0]
            print("[选中 hit]", json.dumps(hit, ensure_ascii=False))
            return hit
        print("没有相关结果：调整关键词与时间筛选；不据此断言操作没有发生。")
    raise RuntimeError("本次练习记录未在搜索结果中找到，需继续核对搜索范围")


def expand_forward(db, hit, window=2):
    """故意用小窗口；末条消息作下一锚点，去重后核对请求与回执。"""
    anchor = hit["match_message_id"]
    collected = {}
    windows = []
    while True:
        expanded = json.loads(session_search(
            session_id=hit["session_id"], around_message_id=anchor, window=window, db=db,
        ))
        require(expanded.get("success"), "按 around_message_id 展开失败")
        messages = expanded.get("messages", [])
        require(bool(messages), "展开没有消息，应核对会话与锚点")
        previous_count = len(collected)
        collected.update({m["id"]: m for m in messages})
        windows.append({"anchor": anchor, "ids": [m["id"] for m in messages],
                        "messages_after": expanded.get("messages_after", 0)})
        print("[展开窗口]", windows[-1])
        if not expanded.get("messages_after", 0):
            break
        next_anchor = messages[-1]["id"]
        require(next_anchor != anchor and len(collected) > previous_count, "展开没有前进，需核对窗口")
        print(f"窗口后仍有消息，以末条消息编号 {next_anchor} 继续展开。")
        anchor = next_anchor
    return [collected[k] for k in sorted(collected)], windows


def main():
    started = time.monotonic()
    started_at = time.time()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    require(bool(api_key), "先设置 DEEPSEEK_API_KEY 环境变量")
    print("本次持久化目录：", RUN_HOME.relative_to(HERE))
    session_db = SessionDB()
    agent = AIAgent(
        reasoning_config={"enabled": False},
        model="deepseek-flash", provider="deepseek", api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        quiet_mode=True, max_iterations=6, enabled_toolsets=["file", "memory"],
        session_db=session_db,
    )
    agent._memory_nudge_interval = 0
    agent._skill_nudge_interval = 0
    sid = agent.session_id
    print("本次 session_id：", sid)
    try:
        banner("步骤 1：记录项目背景，核对写入回执")
        t1 = agent.run_conversation(
            user_message="我们项目叫 Orion，主数据库是 PostgreSQL，主库部署在东京机房。"
                         "请使用 memory 工具将这条项目事实写入 MEMORY.md，保留中文。"
        )
        require(not t1.get("failed") and bool(t1.get("final_response")), "第 1 轮模型未正常结束")
        print("[第 1 轮结束原因]", t1.get("turn_exit_reason"))
        print("[第 1 轮叙述]", t1["final_response"][:400])

        banner("步骤 2：读取 README，区分请求、回执与最终回答")
        t2 = agent.run_conversation(
            user_message="请使用 read_file 读取当前目录下 README.md 开头的 20 行，"
                         "找出第 1 个 Markdown 一级标题。只回答标题。",
            conversation_history=t1["messages"],
        )
        require(not t2.get("failed") and bool(t2.get("final_response")), "第 2 轮模型未正常结束")
        print("[第 2 轮结束原因]", t2.get("turn_exit_reason"))
        print("[第 2 轮回答]", t2["final_response"][:400])

        banner("步骤 3：回查 state.db，按 tool_call_id 配对")
        messages = session_db.get_messages(sid, include_inactive=True)
        require(sum(m["role"] == "user" for m in messages) == 2, "数据库未保留 2 轮用户输入")
        calls = paired_calls(messages)
        require("memory" in calls.values(), "本轮没有实际调用 Memory 写入工具")
        for index, message in enumerate(messages):
            content = message.get("content") or ""
            if message["role"] == "tool":
                receipt = json.loads(content)
                # 完整回执留在数据库，终端摘要只展示本次需要核对的字段。
                content = json.dumps({
                    key: receipt[key] for key in ("success", "target", "entry_count", "content")
                    if key in receipt
                }, ensure_ascii=False)
            snippet = str(content).replace("\n", " ")[:140]
            print(f'{index:02d} id={message["id"]} role={message["role"]}: {snippet}')
            for call in message.get("tool_calls") or []:
                print(f'   请求：{call["function"]["name"]}，id={call["id"]}')
            if message.get("tool_call_id"):
                print(f'   回执：tool_call_id={message["tool_call_id"]}')
        print(f"消息数：{len(messages)}；完成配对的工具调用数：{len(calls)}")
        memory = RUN_HOME / "memories" / "MEMORY.md"
        require(memory.exists() and "Orion" in memory.read_text(), "Memory 文件未包含项目事实")
        print("[MEMORY.md]", memory.read_text().strip())
    finally:
        agent.close()
        session_db.close()

    banner("步骤 4：重开数据库，搜索历史并展开原始消息")
    reopened = SessionDB()
    try:
        restored = reopened.get_messages(sid, include_inactive=True)
        fields = ("id", "role", "content", "tool_calls", "tool_call_id")
        require(
            [{k: m.get(k) for k in fields} for m in restored]
            == [{k: m.get(k) for k in fields} for m in messages],
            "关闭后重新打开数据库，消息或工具配对发生变化",
        )
        hit = discover_hit(reopened, sid, started_at - 5, time.time() + 5)
        anchor = hit["match_message_id"]
        expanded_messages, windows = expand_forward(reopened, hit)
        restored_by_id = {message["id"]: message for message in restored}
        for message in expanded_messages:
            require(message["id"] in restored_by_id, "展开结果混入其他会话")
            require(message["content"] == restored_by_id[message["id"]]["content"], "展开结果改变了原始内容")
        expanded_pairs = paired_calls(expanded_messages)
        print(f"数据库重开：通过；保留 {len(restored)} 条消息")
        print(f"搜索词：Orion；命中本次会话；match_message_id={anchor}")
        print(f"展开：{len(expanded_messages)} 条原始消息；工具配对 {len(expanded_pairs)} 组")
        summary = {
            "model": "deepseek-flash", "session_id": sid,
            "state_directory": str(RUN_HOME.relative_to(HERE)),
            "message_count": len(restored), "paired_tool_calls": len(calls),
            "tool_names": list(calls.values()), "search_hit": True,
            "match_message_id": anchor, "expanded_message_count": len(expanded_messages),
            "windows": windows,
            "expanded_pairs": len(expanded_pairs), "reopened_identical": True,
            "memory_persisted": True, "final_response": t2["final_response"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        output = HERE / "output"
        output.mkdir(exist_ok=True)
        (output / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print("结果已保存：output/result.json")
        print(f"练习耗时：{summary['elapsed_seconds']} 秒")
    finally:
        reopened.close()
    print("\n模型叙述负责解释；工具回执记录本次观察；Memory 保存以后需要的项目事实。")
    print("这里验证了持久化与历史回查，尚未验证后台学习、技能更新或跨实例共享。")


if __name__ == "__main__":
    main()
