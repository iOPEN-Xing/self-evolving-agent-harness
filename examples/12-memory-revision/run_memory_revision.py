#!/usr/bin/env python3
"""第 12 讲练习：错误记忆比没有更糟，修订要有依据、可追溯。

用真实 Hermes AIAgent + deepseek-flash 演示记忆的"写入 -> 发现有误 -> replace 修订"闭环：
  1. 先向 MEMORY.md 写入一条记忆（预置一条上一轮后台复盘或 memory 工具可能存下的过时事实作为起点）。
  2. 前台明确告知事实过时，并要求模型调用 memory(action=replace) 修订。
  3. 打印修订前后 MEMORY.md 的内容，证明改了哪一条、改成了什么，可追溯。

memory_tool.py 里 replace 的语义：
  - 用 old_text 子串在现有条目里找"唯一"匹配的那一条；
  - 找不到、或命中内容不同的多条，会报错；完全重复的条目另行处理；
  - 命中后整条替换为 new_content，原子写回磁盘。

运行环境由 run.sh 负责（unset 代理、export key、HERMES_HOME 独立）。
"""
import os
from revision_validation import revision_checks
import sys
import time
import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
sys.path.insert(0, str(HERMES_SRC))
os.chdir(HERMES_SRC)
os.environ["HERMES_HOME"] = str(HERE / ".hermes-home" / uuid.uuid4().hex)
(Path(os.environ["HERMES_HOME"]) / "memories").mkdir(parents=True, exist_ok=True)

from run_agent import AIAgent  # noqa: E402

MEM = Path(os.environ["HERMES_HOME"]) / "memories" / "MEMORY.md"


def banner(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def read_mem():
    return MEM.read_text(encoding="utf-8") if MEM.exists() else "(空文件)"


def list_memory_operations(messages):
    """把这一轮里模型发起的 memory 工具调用参数摘出来，便于追溯。"""
    out = []
    for m in messages or []:
        for tc in (m.get("tool_calls") or []):
            fn = (tc.get("function") or {}).get("name", "")
            if fn == "memory":
                import json
                try:
                    args = json.loads((tc.get("function") or {}).get("arguments", "{}"))
                except Exception:
                    args = {}
                operations = args.get("operations", [args])
                out.extend({"target": args.get("target"), **operation} for operation in operations)
    return out


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        print("ERROR: 先 export DEEPSEEK_API_KEY（见本章 run.sh）", file=sys.stderr)
        sys.exit(1)
    print("HERMES_HOME =", os.environ["HERMES_HOME"])
    print("模型 deepseek-flash  base_url =", base_url)

    agent = AIAgent(
        reasoning_config={"enabled": False},
        model="deepseek-flash", provider="deepseek",
        api_key=api_key, base_url=base_url,
        quiet_mode=True, max_iterations=6, enabled_toolsets=["memory"],
    )
    store = agent._memory_store
    # 单独验证前台明确调用；不让后台复盘混入文件变化。
    agent._memory_nudge_interval = 0
    agent._skill_nudge_interval = 0
    print("验证范围：前台明确调用 replace，不验证后台自主发现或触发。")

    # ------------------------------------------------------------------
    # 步骤 1：写入一条"现在看是错的"记忆。预置一条后台复盘或上一轮对话可能存下的事实作为起点。
    # 我们直接用 MemoryStore.add 写，保证内容确定、可复现。
    # ------------------------------------------------------------------
    banner("步骤 1：先写入一条记忆（稍后会证明它其实是错的）")
    wrong = "支付服务的缓存用 Redis 6.2，部署在 cache-01 上。"
    r = store.add("memory", wrong)
    print("store.add ->", r.get("success"), r.get("message", r.get("error", "")))
    print("\n[修订前 MEMORY.md]")
    print(read_mem())
    before = read_mem()

    # ------------------------------------------------------------------
    # 步骤 2：新一轮对话，模型发现这条记忆有误，用 memory(action=replace) 修订。
    # ------------------------------------------------------------------
    banner("步骤 2：新一轮对话指出记忆有误，让模型用 replace 修订")
    prompt = (
        "纠正一下：上一轮存进 MEMORY.md 的那条记忆已经过时。"
        "支付服务上周已经把缓存从 Redis 6.2 升到了 Redis 7.2。"
        "请用 memory 工具的 replace，把 MEMORY.md 里提到 Redis 6.2 的那一条，"
        "只将版本号替换为 Redis 7.2。replace 会替换整条内容，不是替换子串："
        "完整新条目必须为：支付服务的缓存用 Redis 7.2，部署在 cache-01 上。"
        "只执行这一条 replace，禁止 add、测试探针或其它写入；其它条目不要动。工具完成后简短确认并停止。"
    )
    print("用户：", prompt)
    t0 = time.time()
    res = agent.run_conversation(user_message=prompt)
    print(f"\n[前台返回] {time.time() - t0:.2f}s  exit={res.get('turn_exit_reason')}")
    print("[模型回答]", repr((res.get("final_response") or "")[:240]))

    calls = list_memory_operations(res.get("messages"))
    print(f"\n本轮模型发起的 memory 操作 {len(calls)} 条（一次调用可含多条 operations）：")
    for i, c in enumerate(calls, 1):
        print(f"  #{i} action={c.get('action')} target={c.get('target')} "
              f"old_text={c.get('old_text')!r}")

    # ------------------------------------------------------------------
    # 步骤 3：对比修订前后，证明可追溯。
    # ------------------------------------------------------------------
    banner("步骤 3：对比修订前后 MEMORY.md")
    after = read_mem()
    print("[修订后 MEMORY.md]")
    print(after)
    print("\n-- 差异 --")
    if before != after:
        print("内容确实变了。把 § 拆成条目逐条看：")
        be = [e.strip() for e in before.split("\n§\n") if e.strip()]
        af = [e.strip() for e in after.split("\n§\n") if e.strip()]
        print("  修订前条目：", be)
        print("  修订后条目：", af)
        print("\n说明：replace 用子串定位旧条目，命中后整条替换；"
              "找不到或命中内容不同的多条会报错；不证明新事实本身正确。")
    else:
        print("内容没变（模型这一轮可能没成功调用 replace）。")

    checks = revision_checks(before, after, res)
    summary = {"model": "deepseek-flash", "base_url": base_url,
               "turn_exit_reason": res.get("turn_exit_reason"), "checks": checks,
               "before": before, "after": after, "messages": res.get("messages", [])}
    run_home = Path(os.environ["HERMES_HOME"])
    (run_home / "revision-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("验证：", json.dumps(checks, ensure_ascii=False))
    agent.close()
    if not all(checks.values()):
        raise SystemExit("修订验证未全部通过，见本次 revision-result.json")


if __name__ == "__main__":
    main()
