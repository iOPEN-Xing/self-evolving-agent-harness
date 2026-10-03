"""第 22 讲练习：SkillClaw 共享修订在第三处生效（真实 evolve_server 多服务）。

目标：核验多地经验能否在第三处生效；要核对加载路径，并尊重
direct/validated 发布边界。本练习调用真实 evolve_server（workflow 引擎）：
  drain 会话 -> summarizer(LLM) -> session_judge(LLM) -> aggregate_by_skill
  -> execute improve_skill(LLM) -> 上传 v2 到共享存储 -> ack 会话。

演示流程：
  1. direct 发布：对第 21 讲收集的会话跑 evolve_server --once --publish-mode direct，
     检查真实 JSON summary、v2 是否补上正确的连接池查询，以及会话是否被 ack。
  2. validated 边界：另起一个隔离共享存储，跑 --publish-mode validated，
     evolve 产出候选但只排队 validation job、不上传、版本不前进。
  3. 第三处实例：三个全新的 DeepSeek 工具循环分别注入 v1、拉取的 v2、下载 v2 但仍加载的 v1。
     核对 skill_view、文件 SHA-256、真实模型工具调用和 mock 回执，不从回答措辞推断行动。
     工具环境复用第 21 讲的 mock，不执行真实运维命令；预期差异须由本次运行确认。

运行：
  bash examples/21-skillclaw-session-collection/run.sh   # 先收集会话
  export DEEPSEEK_API_KEY=...
  unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
  bash examples/22-skillclaw-shared-revision/run.sh
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
REPO = REPO_ROOT
HERMES_SRC = Path(
    os.environ.get("HERMES_SRC") or REPO_ROOT / ".deps" / "hermes-agent"
).expanduser().resolve()
SKILLCLAW_DIR = REPO / ".deps" / "SkillClaw"
sys.path.insert(0, str(SKILLCLAW_DIR))
VENV_PY = HERMES_SRC / ".venv" / "bin" / "python"

# 第 21 讲的目录名不是 Python 模块名；按文件导入，不运行其 main 或上传会话。
L21_PATH = HERE.parent / "21-skillclaw-session-collection" / "session_collection.py"
_l21_spec = importlib.util.spec_from_file_location("lesson21_toolloop", L21_PATH)
if _l21_spec is None or _l21_spec.loader is None:
    raise ImportError(f"无法加载第 21 讲工具循环：{L21_PATH}")
lesson21 = importlib.util.module_from_spec(_l21_spec)
_l21_spec.loader.exec_module(lesson21)
ToolLoopAgent = lesson21.ToolLoopAgent

SHARED_STORE = Path(
    os.environ.get(
        "SC_SHARED_STORE",
        str(REPO / "examples" / "22-skillclaw-shared-revision" / "output" / "shared_store"),
    )
)
GROUP_ID = "default"
DEEPSEEK_BASE = lesson21.DEEPSEEK_BASE
DEEPSEEK_MODEL = lesson21.DEEPSEEK_MODEL
API_KEY = lesson21.API_KEY

# 隔离的 validated 演示存储（不污染主共享存储）
VALIDATED_STORE = HERE / "output" / "validated_store"
RESULTS_TXT = HERE / "output" / "run_results.txt"
SKILL_NAME = "payment-timeout-troubleshoot"
POOL_CAPACITY = 80  # 受控场景配置；SHOW POOLS mock 本身不返回容量上限。
CORRECT_POOL_COMMAND = 'psql -h <host> -p <port> -d pgbouncer -c "SHOW POOLS;"'

PROMPT = (
    "支付服务偶发超时，curl 健康检查正常、上游 provider ping 延迟也正常，"
    "但用户还是报超时。按你持有的技能，用 run_shell 实际执行诊断，再依据回执给出结论。"
    "本受控环境配置的连接池容量为 80，管理入口为 db:6432。"
    "只执行技能中已有的诊断步骤；若仍未定位，请明确说明并提出后续建议，勿自行执行额外检查。"
)


def section(title: str) -> None:
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")


def strip_proxies(env: dict) -> dict:
    e = dict(env)
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        e.pop(k, None)
    return e


def run_evolve(store: Path, publish_mode: str, history_tag: str) -> dict:
    """调用真实 evolve_server（workflow 引擎，local 后端）跑一次。"""
    env = strip_proxies(os.environ)
    env.update({
        "OPENAI_API_KEY": API_KEY,
        "OPENAI_BASE_URL": DEEPSEEK_BASE,
        "EVOLVE_MODEL": DEEPSEEK_MODEL,
        "EVOLVE_USE_SESSION_JUDGE": "1",
        "EVOLVE_HISTORY_LOG": str(REPO / ".runtime" / f"evolve_history_{history_tag}.jsonl"),
        "EVOLVE_PROCESSED_LOG": str(REPO / ".runtime" / f"evolve_processed_{history_tag}.json"),
        "PYTHONPATH": str(SKILLCLAW_DIR),
    })
    cmd = [
        str(VENV_PY), "-m", "evolve_server",
        "--engine", "workflow", "--once",
        "--storage-backend", "local", "--local-root", str(store),
        "--model", DEEPSEEK_MODEL,
        "--publish-mode", publish_mode,
    ]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(SKILLCLAW_DIR), env=env, capture_output=True, text=True)
    out = proc.stdout.strip()
    summary: dict = {}
    brace = out.find("{")
    if brace != -1:
        try:
            summary = json.loads(out[brace:])
        except json.JSONDecodeError:
            summary = {"_parse_error": "summary json not parseable", "_stdout_tail": out[-2000:]}
    if proc.returncode != 0:
        print("  [stderr tail]", proc.stderr[-1500:])
    return summary


def manifest_version(store: Path, skill_name: str) -> int:
    mp = store / GROUP_ID / "manifest.jsonl"
    if not mp.exists():
        return 0
    for line in mp.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("name") == skill_name:
            return int(rec.get("version", 0))
    return 0


def queued_sessions(store: Path) -> int:
    d = store / GROUP_ID / "sessions"
    return len(list(d.glob("*.json"))) if d.exists() else 0


def validation_jobs(store: Path) -> list:
    d = store / GROUP_ID / "validation_jobs"
    return list(d.glob("*.json")) if d.exists() else []


def stage_validated_store(v1_md: str, session: dict) -> None:
    """在隔离存储重放 direct 前保留的一条 L21 真实会话，不另造学习材料。"""
    shutil.rmtree(VALIDATED_STORE, ignore_errors=True)
    from skillclaw.skill_hub import SkillHub
    hub = SkillHub(
        backend="local", endpoint="", bucket="", access_key_id="", secret_access_key="",
        local_root=str(VALIDATED_STORE), group_id=GROUP_ID, user_alias="instance-A",
    )
    skill_dir = VALIDATED_STORE / "staged_skill" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(v1_md, encoding="utf-8")
    print("  push v1 ->", hub.push_skills(str(VALIDATED_STORE / "staged_skill")))
    hub._bucket.put_object(
        f"{hub._prefix()}sessions/{session['session_id']}.json",
        json.dumps(session, ensure_ascii=False).encode("utf-8"),
    )
    print(f"  重放 1 条真实会话 -> {session['session_id']}")


def run_third_instance(skill_md: str, task_prompt: str, label: str) -> dict:
    """新建 Agent，完整注入 skill_view；只依据匹配的调用和成功回执判断行为。"""
    # 每次 run 都从空对话开始；不调用 L21 的会话上传函数，不写学习会话。
    turn = ToolLoopAgent(skill_md=skill_md, alias=label, task_prompt=task_prompt).run()
    skill_view = turn["skill_view"]
    calls: list[str] = []
    results: list[dict] = []
    by_id = {result["tool_call_id"]: result for result in turn["tool_results"]}
    ran_query = found_saturation = False
    for call in turn["tool_calls"]:
        function = call.get("function") or {}
        if function.get("name") != "run_shell":
            continue
        try:
            command = json.loads(function["arguments"])["command"]
        except (KeyError, ValueError, TypeError):
            continue
        if not isinstance(command, str):
            continue
        calls.append(command)
        cmd = command.lower()
        is_pool_query = ("pgbouncer" in cmd and "show pools" in cmd
                         and "pgbouncer --show-pools" not in cmd
                         and "pgbouncer show-pools" not in cmd)
        ran_query |= is_pool_query
        result = by_id.get(call["id"])
        # 不能让另一条命令的回执或模型最终回答冒充当前调用的观测。
        if result is None or result.get("command") != command:
            results.append({"tool_call_id": call["id"], "command": command,
                            "stdout": "", "has_error": True, "stderr": "缺少匹配回执",
                            "exit_code": None})
            continue
        observation = json.loads(result["content"])
        results.append({**observation, "tool_call_id": call["id"], "command": command,
                        "has_error": result["has_error"]})
        if not is_pool_query or result["has_error"] or observation.get("exit_code") != 0:
            continue
        rows = csv.DictReader(io.StringIO(observation.get("stdout", "")), delimiter="|")
        for row in rows:
            try:
                found_saturation |= (int(row["cl_active"]) == POOL_CAPACITY
                                     and int(row["cl_waiting"]) > 0
                                     and int(row["maxwait"]) > 0)
            except (KeyError, ValueError, TypeError):
                continue
    return {
        "label": label,
        "skill_file_sha256": hashlib.sha256(skill_view.encode("utf-8")).hexdigest(),
        "skill_view_preview": skill_view[:200],
        "skill_view": skill_view,
        "tool_calls": calls,
        "tool_results": results,
        "final_answer": turn["response_text"],
        "ran_correct_pool_query": ran_query,
        "found_saturation": found_saturation,
        "pool_capacity": POOL_CAPACITY,
        "pool_capacity_source": "受控场景配置，不是 SHOW POOLS 返回字段",
        "stop_reason": turn["stop_reason"],
        "raw_tool_calls": turn["tool_calls"],
        "raw_tool_results": turn["tool_results"],
    }


def pull_v2_into_instance(label: str) -> dict:
    """每次拉取使用全新落盘目录，保留下载文件供复核，不覆盖已有实例。"""
    from skillclaw.skill_hub import SkillHub
    hub = SkillHub(
        backend="local", endpoint="", bucket="", access_key_id="", secret_access_key="",
        local_root=str(SHARED_STORE), group_id=GROUP_ID, user_alias=label,
    )
    root = HERE / "output" / "third_instances"
    root.mkdir(parents=True, exist_ok=True)
    skills_dir = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=root)) / "skills"
    result = hub.pull_skills(str(skills_dir), mirror=False, include_names=[SKILL_NAME])
    path = skills_dir / SKILL_NAME / "SKILL.md"
    if result.get("downloaded") != 1 or not path.is_file():
        raise RuntimeError(f"{label} 未成功下载技能：{result}")
    data = path.read_bytes()
    return {"pull_result": result, "skill_path": str(path),
            "downloaded_sha256": hashlib.sha256(data).hexdigest(),
            "skill_md": data.decode("utf-8")}


def pool_command_lines(skill_md: str) -> tuple[list[str], bool]:
    """保留生成命令原文，并检查 psql 管理库查询；不自动修补 evolve 的结果。"""
    lines = [line for line in skill_md.splitlines()
             if any(word in line.lower() for word in ("psql", "pgbouncer", "show pools"))]
    correct = False
    for line in lines:
        # 支持 Markdown 行内代码、代码块和普通命令行。
        for fragment in [line, *re.findall(r"`([^`]+)`", line)]:
            start = fragment.find("psql ")
            if start < 0:
                continue
            try:
                tokens = shlex.split(fragment[start:])
            except ValueError:
                continue
            correct |= any(tokens[i:i + 2] == ["-d", "pgbouncer"]
                           for i in range(len(tokens) - 1)) and any(
                tokens[i] == "-c" and tokens[i + 1].strip().upper() == "SHOW POOLS;"
                for i in range(len(tokens) - 1)
            )
    return lines, correct


def main() -> None:
    if not API_KEY:
        print("ERROR: 请先 export DEEPSEEK_API_KEY=...")
        sys.exit(1)
    if not (SHARED_STORE / GROUP_ID / "sessions").exists() or queued_sessions(SHARED_STORE) == 0:
        print("ERROR: 共享存储里没有会话。请先跑 examples/21-skillclaw-session-collection/run.sh")
        sys.exit(1)

    for path in sorted((SHARED_STORE / GROUP_ID / 'sessions').glob('*.json')):
        if json.loads(path.read_text()).get('source') != 'real_deepseek_toolloop':
            excluded = SHARED_STORE / GROUP_ID / 'excluded-constructed'; excluded.mkdir(exist_ok=True)
            target = excluded / path.name
            if target.exists(): target = excluded / (str(time.time_ns())+'-'+path.name)
            path.rename(target)
    if queued_sessions(SHARED_STORE) == 0:
        raise RuntimeError('过滤后没有真实会话；停止学习，不使用手写轨迹。')

    lines: list[str] = []

    def log(s: str = "") -> None:
        print(s)
        lines.append(s)
        # 逐条保存，失败时也保留已观察到的结果；不写入 sessions 学习队列。
        RESULTS_TXT.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v1_path = SHARED_STORE / GROUP_ID / "skills" / SKILL_NAME / "versions" / "v1" / "SKILL.md"
    if not v1_path.is_file():
        v1_path = lesson21.INSTANCE_A_SKILLS / SKILL_NAME / "SKILL.md"
    v1_md = v1_path.read_bytes().decode("utf-8")
    # direct 会 ack 队列；预先在内存保留一条真实会话供步骤 2 重放。
    sessions = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((SHARED_STORE / GROUP_ID / "sessions").glob("*.json"))]
    validated_session = next((s for s in sessions if s.get("source") == "real_deepseek_toolloop"), None)
    if validated_session is None:
        raise RuntimeError("缺少 L21 的真实工具循环会话，请先运行新版第 21 讲；不以手写轨迹替代。")

    # ---- 1) direct 发布 ----
    section("步骤 1：direct 发布 —— evolve_server 跑一次，v1 -> v2")
    before_v = manifest_version(SHARED_STORE, "payment-timeout-troubleshoot")
    before_n = queued_sessions(SHARED_STORE)
    log(f"  跑前：manifest version={before_v}, 队列会话数={before_n}")
    summary = run_evolve(SHARED_STORE, "direct", "direct")
    after_v = manifest_version(SHARED_STORE, "payment-timeout-troubleshoot")
    after_n = queued_sessions(SHARED_STORE)
    log(f"  跑后：manifest version={after_v}, 队列会话数={after_n}（ack 后应为 0）")
    log(f"  JSON summary: sessions={summary.get('sessions')}, "
        f"skill_groups={summary.get('skill_groups')}, "
        f"skills_evolved={summary.get('skills_evolved')}, "
        f"elapsed={summary.get('elapsed_seconds')}s")
    js = summary.get("session_judge", {})
    log(f"  session_judge: judged={js.get('judged_sessions')}, mean_score={js.get('mean_score')}")
    for ev in summary.get("evolutions", []):
        log(f"    action={ev.get('action')} skill={ev.get('skill_name')} "
            f"v={ev.get('version')} uploaded={ev.get('uploaded')}")
        log(f"    rationale: {str(ev.get('rationale', ''))[:240]}")

    uploaded = any(ev.get("skill_name") == SKILL_NAME and ev.get("uploaded")
                   for ev in summary.get("evolutions", []))
    log(f"  本次成功路径核对：队列 ack 到 0={after_n == 0}，"
        f"目标技能上传={uploaded}，版本前进={after_v > before_v}")
    if not uploaded or after_v <= before_v:
        log("  ! 本次 direct 未确认新版本上传，停止第三实例验证，不能把既有文件称为本次 v2。")
        raise RuntimeError("direct 未生成可验证的新共享版本，详见 run_results.txt")
    # 本文沿用“v2”称呼修订版本，同时记录真实版本号，避免把旧文件当作新上传。
    v2_path = SHARED_STORE / GROUP_ID / "skills" / SKILL_NAME / "SKILL.md"
    v2_bytes = v2_path.read_bytes()
    v2_md = v2_bytes.decode("utf-8")
    v2_sha256 = hashlib.sha256(v2_bytes).hexdigest()
    log(f"  受检修订版本：manifest v{after_v}，共享存储 SHA-256={v2_sha256}")
    log("")
    log("  === v2 SKILL.md（共享存储）===")
    for ln in v2_md.splitlines():
        log("  " + ln)
    command_lines, correct_command = pool_command_lines(v2_md)
    log("  === v2 第 4 步/连接池相关命令原文核对（不改写生成结果）===")
    for ln in command_lines:
        log("  " + ln)
    wrong_command = "pgbouncer --show-pools" in v2_md.lower() or "pgbouncer show-pools" in v2_md.lower()
    if wrong_command:
        log("  !!! 错误命令：pgbouncer --show-pools（或 show-pools），mock 将返回 command not found / exit 127。")
    log(f"  正确写法：{CORRECT_POOL_COMMAND}")
    if wrong_command or not correct_command:
        log("  ! v2 命令需人工改为 SHOW POOLS；本脚本不补写技能，后续按实际调用和回执记录结果。")

    # ---- 2) validated 边界 ----
    section("步骤 2：validated 发布边界 —— 只排队、不上传")
    stage_validated_store(v1_md, validated_session)
    vv_before = manifest_version(VALIDATED_STORE, "payment-timeout-troubleshoot")
    v_summary = run_evolve(VALIDATED_STORE, "validated", "validated")
    vv_after = manifest_version(VALIDATED_STORE, "payment-timeout-troubleshoot")
    jobs = validation_jobs(VALIDATED_STORE)
    log(f"  validated 跑前 version={vv_before}, 跑后 version={vv_after}（应仍为 {vv_before}，未前进）")
    log(f"  queued validation jobs: {len(jobs)}")
    for j in jobs:
        job = json.loads(j.read_text(encoding="utf-8"))
        log(f"    job={job.get('job_id')} status={job.get('status')} "
            f"candidate={job.get('candidate_skill_name')} "
            f"min_results={job.get('min_results')} min_approvals={job.get('min_approvals')}")
    vp = v_summary.get("validation_publish", {})
    log(f"  validation_publish summary: {vp}")
    log(f"  本次 validated 边界核对：候选已排队={bool(jobs)}，版本未前进={vv_after == vv_before}")

    # ---- 3) 第三处实例 ----
    section("步骤 3：第三处实例 —— A 加载 v1 / B 下载并加载 v2 / C 下载 v2 但加载 v1")
    log("  隔离条件：每个对比新建 DeepSeek 工具循环 Agent，messages 从零开始，不沿用上轮对话。")
    log("  Memory/技能起点固定：A 和 C 注入同一 v1，B 注入 pull 下来的 v2；不加载其他记忆。")
    log("  第三实例后台学习关闭：本步骤不写入或上传任何学习会话，只保存本次核验记录。")
    log(f"  受检 v1 来源：{v1_path}")
    log(f"  受检 v2 来源：步骤 1 刚上传的共享存储版本 v{after_v}；SHA-256={v2_sha256}")
    log("  不使用 HERMES_HOME 残留上下文；旧版按回答字符串匹配得出的行动结论作废。")
    log("  工具调用由真实模型发起；run_shell 使用与 L21 完全相同的 mock，不操作真实服务。")
    log("  容量 80 来自受控场景配置，回执返回 cl_active=80、cl_waiting=3、maxwait=120。")

    def record(label: str, skill_md: str) -> dict:
        result = run_third_instance(skill_md, PROMPT, label)
        log(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def download(label: str) -> dict:
        pulled = pull_v2_into_instance(label)
        log(f"  {label} pull result: {pulled['pull_result']}")
        log(f"  {label} 下载文件：{pulled['skill_path']}")
        log(f"  {label} 文件 SHA-256={pulled['downloaded_sha256']}")
        matches = pulled["downloaded_sha256"] == v2_sha256
        log(f"  {label} 下载与步骤 1 共享存储 v2 SHA-256 一致={matches}")
        if not matches:
            raise RuntimeError("下载内容与固定受检版本不一致，停止比较，不能宣称 v2 已生效。")
        return pulled

    log("\n  A. v1 实例：注入 v1 原文并实际调用工具")
    result_a = record("A-v1", v1_md)

    log("\n  B. v2 实例：真实 pull 到全新目录，再注入下载的 SKILL.md 原文")
    pulled_b = download("B-v2")
    result_b = record("B-v2", pulled_b["skill_md"])
    loaded_v2 = result_b["skill_file_sha256"] == pulled_b["downloaded_sha256"] == v2_sha256
    log(f"  B 注入文本与下载文件及共享存储 v2 SHA-256 一致={loaded_v2}")

    log("\n  C. 下载成功但未加载：再次真实 pull v2 到全新目录，Agent 仍注入与 A 相同的 v1")
    pulled_c = download("C-unloaded")
    result_c = record("C-unloaded", v1_md)
    loaded_v1 = result_c["skill_file_sha256"] == result_a["skill_file_sha256"]
    log(f"  C 磁盘 v2 SHA-256={pulled_c['downloaded_sha256']}，"
        f"实际注入 v1 SHA-256={result_c['skill_file_sha256']}；与 A 同文={loaded_v1}")

    log("\n  本次实际行为对比（结论由调用与回执判断，完整模型回答见上文）：")
    for result in (result_a, result_b, result_c):
        log(f"  {result['label']}: ran_correct_pool_query={result['ran_correct_pool_query']}, "
            f"found_saturation={result['found_saturation']}, stop_reason={result['stop_reason']}")
    if (loaded_v2 and loaded_v1 and v1_md != v2_md
            and not result_a["ran_correct_pool_query"]
            and result_b["ran_correct_pool_query"] and result_b["found_saturation"]
            and not result_c["ran_correct_pool_query"]
            and all(r["stop_reason"] == "final_answer" for r in (result_a, result_b, result_c))):
        log("  ✓ 本次 B 的模型输入包含 v2，实际发起连接池查询并收到饱和回执；A/C 未查询连接池。")
        log("  本次受控对比支持：下载成功还需将新技能加载进模型输入，才能观察到预期行为变化。")
    else:
        log("  ! 本次未完整观察到预期差异；请核对技能原文、工具回执与停止原因，不宣称改进已生效。")
    log("  以上仅确认本次路径，不推出失败重跑一定不漏会话或一定不重复学习。")
    log("  validated 解释与综合实践：validated 候选只进 validation_jobs、版本不前进；direct 才写 skills/。")
    section("完成")
    log(f"  本次技能输入、下载路径与哈希、工具调用及回执、模型最终回答已写入 {RESULTS_TXT}")


if __name__ == "__main__":
    main()
