#!/usr/bin/env python3
"""第 14 讲练习：外部记忆 Provider。

立场：记忆可外置、可移植，但要清楚 Provider 的边界。
用真实 Hermes AIAgent + deepseek-flash：
  1. 自定义一个 MemoryProvider 子类（文件后端），注册进 MemoryManager。
  2. 演示 prefetch / sync_turn 生命周期：一轮结束 sync_turn 存，下一轮 prefetch 召回。
  3. 演示边界：外置记忆存在自己的文件里，内置 MEMORY.md 不动。
  4. 演示故障隔离：Provider 自己抛错时，主对话照常进行，不被阻塞。
  5. 顺带验证"只允许一个 external provider"：再注册第二个会被拒绝。

运行环境由 run.sh 负责（unset 代理、export key、独立 HERMES_HOME）。
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
sys.path.insert(0, str(HERMES_SRC))
os.chdir(HERMES_SRC)
OUTPUT = Path(os.environ.get("RUN_OUTPUT_DIR", HERE / "output")).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
if not os.environ.get("HERMES_HOME"):
    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="home-", dir=OUTPUT)
HERMES_HOME = Path(os.environ["HERMES_HOME"]).resolve()
if HERMES_HOME.exists() and any(HERMES_HOME.iterdir()):
    raise RuntimeError("本次练习要求全新、空的 HERMES_HOME，不能复用旧记忆。")
(HERMES_HOME / "memories").mkdir(parents=True, exist_ok=True)

from run_agent import AIAgent  # noqa: E402
from agent.memory_provider import MemoryProvider  # noqa: E402


def banner(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


class FileBackedMemory(MemoryProvider):
    """一个极简的文件后端外部记忆：sync_turn 存用户这轮的事实，prefetch 关键词召回。"""

    def __init__(self, path: Path, *, fail_on_sync: bool = False):
        self.path = Path(path)
        self.facts = []
        self.fail_on_sync = fail_on_sync
        self.sync_finished = threading.Event()
        self.sync_successes = 0
        self.sync_failures = 0
        self.prefetch_calls = []

    @property
    def name(self) -> str:
        return "filemem"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kw) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.facts = json.loads(self.path.read_text(encoding="utf-8"))
        print(f"  [filemem] initialize()  session={session_id} 已有事实={len(self.facts)} 条")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        hits = [f for f in self.facts if any(k and k in query for k in f.split())][:3]
        self.prefetch_calls.append({"query": query, "hits": len(hits)})
        print(f"  [filemem] prefetch()  query={query[:30]!r}  命中={len(hits)} 条")
        if not hits:
            return ""
        return "外部记忆 filemem 里相关的事实：\n" + "\n".join("- " + h for h in hits)

    def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None):
        try:
            if self.fail_on_sync:
                self.sync_failures += 1
                print("  [filemem] sync_turn() 注入一次后端故障，抛出 RuntimeError")
                raise RuntimeError("外部记忆后端不可用（主动注入故障以验证隔离）")
            self.facts.append(user_content)
            self.path.write_text(json.dumps(self.facts, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            self.sync_successes += 1
            print(f"  [filemem] sync_turn()  已存，现共 {len(self.facts)} 条 -> {self.path.name}")
        finally:
            # sync_turn 在后台线程运行；完成事件比固定 sleep 更可靠。
            self.sync_finished.set()

    def get_tool_schemas(self):
        return []  # 纯上下文型 provider，不暴露工具


def main():
    started = time.perf_counter()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        print("ERROR: 先 export DEEPSEEK_API_KEY（见本章 run.sh）", file=sys.stderr)
        sys.exit(1)
    print("使用全新 HERMES_HOME；运行目录 =", OUTPUT.name)

    ext_file = Path(os.environ["HERMES_HOME"]) / "external_memory.json"
    builtin_mem = Path(os.environ["HERMES_HOME"]) / "memories" / "MEMORY.md"

    builtin_mem.write_text("系统约定：部署前先核验配置。\n", encoding="utf-8")
    builtin_before = builtin_mem.read_bytes()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    turns = []

    def make_agent():
        # 本练习只验证外部 provider，不开放会主动改写内置记忆的工具。
        current = AIAgent(
        reasoning_config={"enabled": False},
            model=model, provider="deepseek", api_key=api_key, base_url=base_url,
            quiet_mode=True, max_iterations=6, enabled_toolsets=[],
        )
        current._memory_manager = MemoryManager()
        current_provider = FileBackedMemory(ext_file)
        current._memory_manager.add_provider(current_provider)
        current._memory_manager.initialize_all(
            session_id=current.session_id, platform="cli",
            hermes_home=str(HERMES_HOME), agent_context="primary",
        )
        return current, current_provider

    def run_turn(current, current_provider, message):
        current_provider.sync_finished.clear()
        turn_started = time.perf_counter()
        result = current.run_conversation(user_message=message, conversation_history=[])
        front_elapsed = time.perf_counter() - turn_started
        assert result.get("final_response"), "模型未返回正文"
        exit_reason = result.get("turn_exit_reason") or ""
        assert exit_reason.split("(", 1)[0] == "text_response", exit_reason
        assert not result.get("interrupted"), "本轮被中断"
        assert current_provider.sync_finished.wait(15), "后台 sync_turn 未在 15 秒内完成"
        assert builtin_mem.read_bytes() == builtin_before, "内置 MEMORY.md 被修改"
        turns.append({
            "user_message": message,
            "final_response": result["final_response"],
            "turn_exit_reason": result.get("turn_exit_reason"),
            "interrupted": bool(result.get("interrupted")),
            "frontend_seconds": round(front_elapsed, 3),
            "including_sync_seconds": round(time.perf_counter() - turn_started, 3),
        })
        return result

    # 注册自定义外部 provider。未在 config 里配 memory.provider 时，
    # agent._memory_manager 是 None，需要自己建一个并 initialize_all。
    from agent.memory_manager import MemoryManager  # noqa: E402
    agent, provider = make_agent()
    print("已注册外部 provider:", [p.name for p in agent._memory_manager.providers])

    # 顺带验证：第二个 external provider 会被拒绝（只允许一个 external）
    second = FileBackedMemory(ext_file)
    agent._memory_manager.add_provider(second)
    assert agent._memory_manager.providers == [provider], "第二个外部 provider 未被拒绝"
    print("再注册第二个后:", [p.name for p in agent._memory_manager.providers],
          "(应仍只有一个 external: filemem)")

    # ------------------------------------------------------------------
    # 步骤 1：一轮对话，sync_turn 把事实存进外部文件
    # ------------------------------------------------------------------
    banner("步骤 1：说一个事实，sync_turn 存到外部记忆文件")
    first_message = "记一下：我们的 API 文档在 wiki.internal/api。"
    r1 = run_turn(agent, provider, first_message)
    assert json.loads(ext_file.read_text(encoding="utf-8")) == [first_message]
    assert provider.sync_successes == 1
    first_sync_successes = provider.sync_successes
    print("[模型回答]", repr((r1.get("final_response") or "")[:120]))
    print("\n[外部记忆文件 external_memory.json]")
    print("  " + (ext_file.read_text(encoding="utf-8").strip()[:300] if ext_file.exists() else "(空)"))
    print("[内置 MEMORY.md 字节数]", len(builtin_mem.read_bytes()), "；逐字核对未变")
    agent.close()

    # ------------------------------------------------------------------
    # 步骤 2：下一轮 prefetch 召回
    # ------------------------------------------------------------------
    banner("步骤 2：新建 agent，不带对话历史，prefetch 从外部文件召回")
    agent, provider = make_agent()
    assert provider.facts == [first_message], "新 provider 未从文件恢复事实"
    recall_query = "API 文档地址是什么？简短回答。"
    r2 = run_turn(agent, provider, recall_query)
    assert "wiki.internal/api" in r2["final_response"], "模型未答出存储地址"
    assert any(c["query"] == recall_query and c["hits"] >= 1 for c in provider.prefetch_calls)
    assert len(json.loads(ext_file.read_text(encoding="utf-8"))) == 2
    print("[模型回答]", repr((r2.get("final_response") or "")[:200]))

    # ------------------------------------------------------------------
    # 步骤 3：Provider 写操作故障，前台不阻塞，但这一轮经历漏账
    # ------------------------------------------------------------------
    banner("步骤 3：让 sync_turn 抛错，主对话照常，但这一轮没存进去")
    before_fail = ext_file.read_text(encoding="utf-8") if ext_file.exists() else "[]"
    before_count = len(json.loads(before_fail))
    provider.fail_on_sync = True
    r3 = run_turn(agent, provider, "记一下：新的联系人电话是 010-8888。")
    print("[前台是否正常返回]", r3.get("turn_exit_reason"),
          " interrupted=", r3.get("interrupted"))
    print("[模型回答]", repr((r3.get("final_response") or "")[:120]))
    after_fail = ext_file.read_text(encoding="utf-8") if ext_file.exists() else "[]"
    after_count = len(json.loads(after_fail))
    assert provider.sync_failures == 1, "故障轮没有实际尝试 sync_turn"
    assert before_fail == after_fail, "故障轮仍修改了外部文件"
    print(f"\n-> 前台这一轮跑完了，但外部记忆文件里条数是 {after_count}，"
          f"和故障前的 {before_count} 一样。")
    print("-> 本次实际验证写故障：前台返回正常，但这一轮经历未写入。")

    agent.close()
    summary = {
        "passed": True, "model": model, "exit_code": 0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "fresh_home": True, "new_agent_recall_without_history": True,
        "second_external_provider_rejected": True,
        "builtin_unchanged": builtin_mem.read_bytes() == builtin_before,
        "builtin_sha256": hashlib.sha256(builtin_before).hexdigest(),
        "external_count_before_failure": before_count,
        "external_count_after_failure": after_count,
        "successful_syncs": first_sync_successes + provider.sync_successes,
        "sync_failures_observed": provider.sync_failures,
        "faulted_write_left_file_unchanged": before_fail == after_fail,
        "turns": turns,
    }
    (OUTPUT / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("验收通过，总耗时", summary["elapsed_seconds"], "秒；结果见 run-summary.json")


if __name__ == "__main__":
    main()
