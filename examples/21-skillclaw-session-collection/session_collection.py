"""第 21 讲：真实模型工具循环与跨实例会话收集。

实例 A/B 的命令和结论由真实 GLM 工具循环生成；run_shell 只使用本地 mock
回执，不执行系统命令、不访问运维网络。第三条 constructed 对照是手写轨迹，
不能当作模型实跑。真实会话上传；手写对照另存，不进入学习队列。

只有客户端上传到共享存储的会话才进入学习材料；绕开上传的直连 LLM 不进队列。
整体会话数均值不能判断经历质量，必须逐条检查工具调用及回执。
上传格式对齐 api_server._upload_session_data，供第 22 讲 evolve_server drain。

运行：bash examples/21-skillclaw-session-collection/run.sh
密钥仅从 GLM_API_KEY 或 BIGMODEL_API_KEY 环境变量读取。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / ".deps" / "SkillClaw"))

SHARED_STORE = Path(os.environ.get(
    "SC_SHARED_STORE",
    str(REPO / "examples" / "22-skillclaw-shared-revision" / "output" / "shared_store"),
))
GROUP_ID = "default"
GLM_BASE = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-5.2")
API_KEY = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY")
INSTANCE_A_SKILLS = HERE / "output" / "instance_A" / "skills"
COLLECTED_JSONL = HERE / "output" / "collected_sessions.jsonl"
SKILL_NAME = "payment-timeout-troubleshoot"

# 保留 v1 原文：只有 health、provider ping、延迟高时通知值班人，故意没有连接池步骤。
V1_SKILL_MD = """---
name: payment-timeout-troubleshoot
description: Troubleshoot intermittent payment-service timeouts. Use when a user reports payment requests timing out or 504s.
category: ops
---

# Payment Timeout Troubleshoot

When the payment service times out, follow these steps:

1. Check the payment service is up: `curl -sS -m 5 http://payment-svc/health`.
2. Check upstream provider latency: `curl -sS -m 5 -o /dev/null -w '%{time_total}' https://provider.example/ping`.
3. If provider latency is high, page the provider on-call.
"""


def section(title: str) -> None:
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}", flush=True)


def build_hub():
    from skillclaw.skill_hub import SkillHub

    SHARED_STORE.mkdir(parents=True, exist_ok=True)
    return SkillHub(
        backend="local", endpoint="", bucket="", access_key_id="",
        secret_access_key="", local_root=str(SHARED_STORE),
        group_id=GROUP_ID, user_alias="instance-A",
    )


def stage_instance_a_v1() -> Path:
    """写出实例 A 实际推送和读取的 v1 技能。"""
    skill_dir = INSTANCE_A_SKILLS / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(V1_SKILL_MD, encoding="utf-8")
    return skill_dir


def mock_run_shell(command: str) -> tuple[str, str, int]:
    """受控运维环境：仅返回预设观测，不执行任何真实 shell 命令。"""
    cmd = command.lower()
    # 旧的错误写法必须先排除，不能被后续管理库查询规则判为成功。
    if "pgbouncer --show-pools" in cmd or "pgbouncer show-pools" in cmd:
        return "", "bash: pgbouncer: command not found", 127
    if "payment-svc/health" in cmd:
        return "ok\n", "", 0
    if "provider.example/ping" in cmd and "time_total" in cmd:
        return "0.12\n", "", 0
    if "pgbouncer" in cmd and "show pools" in cmd:
        return "name|user|cl_active|cl_waiting|maxwait\npg_default|payment|80|3|120\n", "", 0
    return "", "sandbox read-only mock: unknown diagnostic command", 2


def chat_completion(payload: dict) -> dict:
    """用标准库直连 GLM；每次请求前移除代理环境变量，不记录密钥。"""
    for key in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)
    if not API_KEY:
        raise RuntimeError("请先设置 GLM_API_KEY 或 BIGMODEL_API_KEY 环境变量")
    req = urllib.request.Request(
        f"{GLM_BASE.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    # 显式禁用系统代理，避免 urllib 在 macOS 上回退到系统代理配置。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120) as resp:
        return json.loads(resp.read())["choices"][0]["message"]


def new_turn(prompt: str, skill_view: str, source: str) -> dict:
    """保留 summarizer 消费字段；来源和技能原文另存，不能以模型推测替代观测。"""
    return {
        "turn_num": 1,
        "prompt_text": prompt,
        "response_text": "",
        "read_skills": [{"skill_name": SKILL_NAME}],
        "modified_skills": [],
        "injected_skills": [],
        "tool_calls": [],
        "tool_results": [],
        "tool_errors": [],
        "prm_score": None,
        "source": source,
        "skill_view": skill_view,
        "skill_sha256": hashlib.sha256(skill_view.encode("utf-8")).hexdigest(),
    }


def shell_result(call_id: str, command: str, stdout: str, stderr: str, exit_code: int) -> dict:
    return {
        "tool_call_id": call_id,
        "has_error": exit_code != 0,
        "content": json.dumps(
            {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}, ensure_ascii=False,
        ),
        "command": command,
    }


class GlmToolLoopAgent:
    """模型真实选择命令、读取 mock 回执并回答；不预设排障结论。"""

    def __init__(self, *, skill_md: str, alias: str, task_prompt: str, max_turns: int = 6):
        if max_turns < 1:
            raise ValueError("max_turns 必须大于 0")
        self.skill_view = skill_md
        self.alias = alias
        self.task_prompt = task_prompt
        self.max_turns = max_turns

    def run(self) -> dict:
        turn = new_turn(self.task_prompt, self.skill_view, "real_glm_toolloop")
        messages = [
            {"role": "system", "content": (
                "你是运维排障助手。下面 skill_view 是你实际持有的完整技能。"
                "请按技能使用 run_shell 逐步排查，依据工具回执给出中文结论。"
                "run_shell 是只读 mock 运维环境，不能把未执行的检查说成已执行，"
                "不能把猜测说成已经确认的根因。\n\n"
                f"<skill_view>\n{self.skill_view}\n</skill_view>"
            )},
            {"role": "user", "content": self.task_prompt},
        ]
        tools = [{
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "在只读 mock 运维环境中执行一条诊断命令，返回 stdout、stderr 和 exit_code。",
                "parameters": {
                    "type": "object", "properties": {"command": {"type": "string"}},
                    "required": ["command"], "additionalProperties": False,
                },
            },
        }]
        # rounds 保留多轮调用的边界；扁平字段供现有 summarizer 直接消费。
        turn["rounds"] = []
        turn["stop_reason"] = "max_turns"
        for round_num in range(1, self.max_turns + 1):
            print(f"  {self.alias}：请求 {GLM_MODEL}，第 {round_num}/{self.max_turns} 轮", flush=True)
            message = chat_completion({
                "model": GLM_MODEL, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0.2,
            })
            calls = message.get("tool_calls") or []
            round_record = {
                "round_num": round_num, "response_text": message.get("content") or "",
                "tool_calls": calls, "tool_results": [],
            }
            turn["rounds"].append(round_record)
            if not calls:
                turn["response_text"] = message.get("content") or ""
                turn["stop_reason"] = "final_answer"
                break

            # 原样保留模型返回的 OpenAI 风格调用及可能需要回传的 reasoning_content。
            messages.append(dict(message, role="assistant"))
            for call in calls:
                turn["tool_calls"].append(call)
                function = call.get("function") or {}
                command = ""
                try:
                    if function.get("name") != "run_shell":
                        raise ValueError("未知工具；只支持 run_shell")
                    arguments = json.loads(function.get("arguments", ""))
                    if not isinstance(arguments, dict) or not isinstance(arguments.get("command"), str):
                        raise ValueError("run_shell 参数必须含字符串 command")
                    command = arguments["command"]
                    stdout, stderr, exit_code = mock_run_shell(command)
                except (ValueError, TypeError) as exc:
                    stdout, stderr, exit_code = "", f"工具参数错误：{exc}", 2
                result = shell_result(call["id"], command, stdout, stderr, exit_code)
                turn["tool_results"].append(result)
                round_record["tool_results"].append(result)
                messages.append({
                    "role": "tool", "tool_call_id": call["id"], "content": result["content"],
                })
                print(f"    run_shell({command!r}) -> {result['content']}", flush=True)
        if turn["stop_reason"] == "max_turns":
            # 不把最后一轮调用前的文字或程序补写的话冒充模型最终回答。
            print("  已达到轮数上限，模型尚未给出无工具调用的最终回答。", flush=True)
        else:
            print(f"  模型最终回答：{turn['response_text']}", flush=True)
        return turn


def build_constructed_turn() -> dict:
    """唯一手写对照：命令、回执、结论均预设，不调用模型，也不算实跑发现。"""
    turn = new_turn(
        "[constructed 手写对照，非模型实跑] 支付 504，演示手写连接池查询轨迹。",
        V1_SKILL_MD, "constructed_demo",
    )
    command = 'psql -h db -U payment -d pgbouncer -c "SHOW POOLS;"'
    turn["tool_calls"] = [{
        "id": "constructed-pools", "type": "function",
        "function": {"name": "run_shell", "arguments": json.dumps({"command": command})},
    }]
    turn["tool_results"] = [shell_result(
        "constructed-pools", command,
        "name|user|cl_active|cl_waiting|maxwait\npg_default|payment|80|3|120\n", "", 0,
    )]
    # summarizer 目前不消费 source，因此 prompt/response 也明确标注手写性质。
    turn["response_text"] = (
        "[constructed 手写对照，非模型实跑] 预设回执为 cl_active=80、cl_waiting=3、"
        "maxwait=120s；用于说明手写轨迹的样子，不证明模型发现了连接池问题。"
    )
    return turn


def make_session(session_id: str, alias: str, turn: dict) -> dict:
    return {
        "session_id": session_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user_alias": alias, "num_turns": 1, "source": turn["source"], "turns": [turn],
    }


def upload_session(hub, session: dict) -> str:
    """使用与 api_server._upload_session_data 相同的真实 put 路径和外层字段。"""
    key = f"{hub._prefix()}sessions/{session['session_id']}.json"
    hub._bucket.put_object(key, json.dumps(session, ensure_ascii=False).encode("utf-8"))
    return key


def count_queued_sessions(hub) -> int:
    return len(list((SHARED_STORE / hub._prefix() / "sessions").glob("*.json")))


def direct_call_glm(prompt: str) -> str:
    """直连对照：没有工具、没有技能注入、没有会话上传。"""
    message = chat_completion({
        "model": GLM_MODEL,
        "messages": [
            {"role": "system", "content": "你是运维助手，用中文一句话回答。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3, "max_tokens": 2000,
    })
    return message.get("content") or ""


def assess_session(session: dict) -> tuple[str, str]:
    """按匹配的调用和成功回执判断，不从 session_id 或模型自述推断发现。"""
    if session["source"] == "constructed_demo":
        return "不适用（手写轨迹）", "否（预设回执，非模型实跑）"
    health = provider = pools = pool_attempted = False
    for turn in session["turns"]:
        results = {r["tool_call_id"]: r for r in turn["tool_results"]}
        for call in turn["tool_calls"]:
            function = call.get("function") or {}
            if function.get("name") != "run_shell":
                continue
            try:
                command = json.loads(function["arguments"])["command"]
                result = results[call["id"]]
                observation = json.loads(result["content"])
            except (KeyError, ValueError, TypeError):
                continue
            if not isinstance(command, str) or result.get("command") != command:
                continue
            cmd = command.lower()
            pool_attempted |= "pgbouncer" in cmd
            if result["has_error"] or observation.get("exit_code") != 0:
                continue
            stdout = observation.get("stdout", "").strip()
            health |= "payment-svc/health" in cmd and stdout == "ok"
            provider |= "provider.example/ping" in cmd and "time_total" in cmd and stdout == "0.12"
            pools |= ("pgbouncer" in cmd and "show pools" in cmd
                      and "name|user|cl_active|cl_waiting|maxwait" in stdout
                      and "pg_default|payment|80|3|120" in stdout)
    if health and provider:
        stage = "第2步正常；第3步条件不成立"
    elif health:
        stage = "第1步正常；第2步未获正常回执"
    elif provider:
        stage = "第2步有回执；第1步未核实"
    else:
        stage = "第1步尚未核实"
    if pools:
        stage += "；已额外查池"
        discovery = "是（mock 池：80活跃/3等待/120s）"
    elif pool_attempted:
        discovery = "否（尝试查池，未得有效回执）"
    else:
        discovery = "否（未查到连接池新信息）"
    if any(t.get("stop_reason") == "max_turns" for t in session["turns"]):
        stage += "；达到轮数上限"
    return stage, discovery


def print_session_table(sessions: list[dict]) -> None:
    headers = ["session", "实例", "来源", "按v1走到第几步卡住", "是否真查到新信息", "对应技能"]
    rows = []
    for session in sessions:
        stage, discovery = assess_session(session)
        skills = sorted({s["skill_name"] for t in session["turns"] for s in t["read_skills"]})
        rows.append([session["session_id"], session["user_alias"], session["source"],
                     stage, discovery, ", ".join(skills)])

    def width(text: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

    widths = [max(width(row[i]) for row in [headers, *rows]) for i in range(len(headers))]
    for row in [headers, *rows]:
        print(" | ".join(value + " " * (w - width(value)) for value, w in zip(row, widths)))
    print("  结论：整体会话数均值（如 3/2=1.5）说明不了哪条真贡献了可复用方法。")


def main() -> None:
    if not API_KEY:
        sys.exit("ERROR: 请先设置 GLM_API_KEY 或 BIGMODEL_API_KEY 环境变量")

    section("步骤 0：准备真实 SkillHub local 共享存储，清空旧会话队列")
    hub = build_hub()
    sess_dir = SHARED_STORE / GROUP_ID / "sessions"
    for path in sess_dir.glob("*.json"):
        path.unlink()
    print(f"  共享存储根：{SHARED_STORE}")
    print(f"  会话前缀：{hub._prefix()}sessions/")

    section("步骤 1：实例 A 用真实 SkillHub.push_skills 推送 v1")
    skill_dir = stage_instance_a_v1()
    print(f"  push_skills 结果：{hub.push_skills(str(INSTANCE_A_SKILLS))}")
    skill_view = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    section("步骤 2：实例 A 真实 GLM 工具循环（工具环境为本地 mock）")
    turn_a = GlmToolLoopAgent(
        skill_md=skill_view, alias="instance-A",
        task_prompt=(
            "支付服务偶发超时。curl 健康检查正常、上游 provider ping 延迟也正常，但用户仍报 504。"
            "按你手里的 payment-timeout-troubleshoot 技能，用 run_shell 一步步排查，给我结论。"
        ),
    ).run()
    section("步骤 3：实例 B 独立的真实 GLM 工具循环（同一 v1、同一 mock 规则）")
    turn_b = GlmToolLoopAgent(
        skill_md=skill_view, alias="instance-B",
        task_prompt=(
            "另一套环境也报支付 504，健康检查和上游 provider ping 延迟看起来都正常，"
            "用户仍反馈偶发超时。按你手里的 payment-timeout-troubleshoot 技能，"
            "用 run_shell 一步步排查，给我结论。"
        ),
    ).run()
    section("步骤 4：构造唯一 constructed 手写对照（不是模型实跑）")
    sessions = [
        make_session("sess-real-a1", "instance-A", turn_a),
        make_session("sess-real-b1", "instance-B", turn_b),
        make_session("sess-constructed-ref", "instance-A", build_constructed_turn()),
    ]
    print("  sess-constructed-ref：constructed_demo；命令、回执、结论都是手写示例。")

    section("步骤 5：真实会话上传，手写轨迹另存，清单标明来源")
    COLLECTED_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with COLLECTED_JSONL.open("w", encoding="utf-8") as stream:
        for session in sessions:
            if session.get('source') == 'constructed_demo':
                reference = HERE / 'output' / 'constructed-reference'
                reference.mkdir(parents=True, exist_ok=True)
                (reference / (session['session_id']+'.json')).write_text(json.dumps(session,ensure_ascii=False,indent=2))
                continue
            key = upload_session(hub, session)
            stream.write(json.dumps(session, ensure_ascii=False) + "\n")
            print(f"  uploaded {key} ({session['user_alias']}, {session['source']})")
    print(f"  会话清单：{COLLECTED_JSONL}")

    section("步骤 6：直连 GLM 缓存问答（无工具、无技能、不上传）")
    before = count_queued_sessions(hub)
    print(f"  直连调用前队列会话数：{before}")
    print(f"  直连回答：{direct_call_glm('缓存命中率低怎么优化？一句话。')}")
    after = count_queued_sessions(hub)
    print(f"  直连调用后队列会话数：{after}")
    if before == after:
        print("  ✓ 本次直连调用没有进入共享存储会话队列。")
    else:
        print("  队列数量发生变化；本次对照未能验证数量不变，需检查其他写入或 drain。")

    section("步骤 7：逐条核对三条会话（新增信息均指 mock 环境中的观测）")
    print_session_table(sessions)
    print("  会话可供第 22 讲 evolve_server drain；constructed 对照不能当作真实经历。")


if __name__ == "__main__":
    main()
