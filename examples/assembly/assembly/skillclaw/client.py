"""连接 SkillClaw 原生会话存储、演化 HTTP 接口和客户端验证/加载流程。

本模块只负责适配；会话对象、验证任务、发布版本和技能清单均由上游
SkillClaw 读写。单机演示使用上游 local 存储，不另建共享文件协议。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config
from ..contracts import write_json
from .server import runtime_paths


def _upstream() -> None:
    sys.dont_write_bytecode = True
    upstream = str(config.SKILLCLAW_SRC)
    if upstream not in sys.path:
        sys.path.insert(0, upstream)
    config.clear_proxy_for_model()


def _client_config(alias: str, instance_home: Path | None = None):
    _upstream()
    from skillclaw.config import SkillClawConfig

    paths = runtime_paths()
    home = Path(instance_home or Path(paths["root"]) / "instances" / alias)
    return SkillClawConfig(
        model_name=config.MODEL,
        llm_provider="openai",
        llm_api_base=config.BASE_URL,
        llm_api_key=config.api_key(),
        llm_model_id=config.MODEL,
        prm_provider="openai",
        prm_url=config.BASE_URL,
        prm_api_key=config.api_key(),
        prm_model=config.MODEL,
        prm_m=1,
        prm_temperature=0.1,
        prm_max_new_tokens=8192,
        use_prm=False,
        use_skills=True,
        skills_dir=str(home / "skills"),
        record_dir=str(home / "records"),
        record_enabled=False,
        configure_openclaw=False,
        claw_type="hermes",
        sharing_enabled=True,
        sharing_backend="local",
        sharing_local_root=str(paths["store"]),
        sharing_group_id=str(paths["group_id"]),
        sharing_user_alias=alias,
        sharing_session_upload_interval=0,
        validation_enabled=True,
        validation_max_jobs_per_day=0,
        validation_max_concurrency=100,
    )


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in {".", ".."}:
        raise ValueError("实例名和会话 ID 只能包含英文字母、数字、下划线、点和短横线")
    return value


def upload_sessions(endpoint: str, sessions: list[dict]) -> list[str]:
    """调用上游原生上传方法，并通过上游对象存储读回核对真实会话 ID。

    sessions 每项包含 session_id、instance（或 user_alias）及 turns。
    turns 使用 SkillClaw 的 prompt_text/response_text/tool_calls 等原生字段。
    endpoint 是演化 HTTP 地址；会话本身按上游设计通过共享对象存储上传。
    """
    _upstream()
    from skillclaw.api_server import SkillClawAPIServer
    from skillclaw.skill_hub import SkillHub

    paths = runtime_paths()
    receipts: list[dict[str, Any]] = []
    ids: list[str] = []
    for session in sessions:
        session_id = _safe_id(str(session["session_id"]))
        alias = _safe_id(str(session.get("instance") or session.get("user_alias") or "anonymous"))
        turns = session.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"会话 {session_id} 没有真实 turns")
        if any(not isinstance(turn, dict) for turn in turns):
            raise ValueError(f"会话 {session_id} 的 turns 必须是字典列表")
        cfg = _client_config(alias)
        api = SkillClawAPIServer(cfg)
        if not asyncio.run(api._upload_session_data(session_id, turns)):
            raise RuntimeError(f"SkillClaw 上传失败：{session_id}")
        hub = SkillHub.object_storage_from_config(cfg)
        object_key = f"{paths['group_id']}/sessions/{session_id}.json"
        stored_bytes = hub._bucket.get_object(object_key).read()
        stored = json.loads(stored_bytes)
        if stored.get("session_id") != session_id or stored.get("turns") != turns:
            raise RuntimeError(f"会话上传后读回内容不一致：{session_id}")
        ids.append(session_id)
        receipts.append({
            "session_id": session_id, "instance": alias,
            "object_key": object_key, "num_turns": len(turns),
            "sha256": hashlib.sha256(stored_bytes).hexdigest(),
            "uploader": "SkillClawAPIServer._upload_session_data",
            "storage_backend": "SkillClaw LocalObjectStore",
            "evolve_endpoint": endpoint,
        })
    write_json(Path(paths["root"]) / "uploaded_sessions.json", sessions)
    write_json(Path(paths["root"]) / "upload_receipts.json", receipts)
    return ids


def trigger_evolution(endpoint: str) -> dict:
    """使用真实 POST /trigger；完整保存上游返回的运行结果。"""
    _upstream()
    import httpx

    with httpx.Client(trust_env=False, timeout=httpx.Timeout(1800.0, connect=10.0)) as client:
        response = client.post(endpoint.rstrip("/") + "/trigger")
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("evolve_server /trigger 没有返回 JSON 对象")
    root = Path(runtime_paths()["root"])
    write_json(root / "triggers" / f"{time.time_ns()}.json", result)
    write_json(root / "last_trigger.json", result)
    return result


def publish(endpoint: str, evolution: dict | None = None, *, validator_alias: str = "validator-D") -> dict:
    """真实客户端回放验证后，再触发服务端按验证结果发布。

    上游 local/OSS 模式没有独立 publish HTTP 路由；workflow 在下一次
    /trigger 中执行 _finalize_validation_jobs。这里不自行写发布清单。
    """
    _upstream()
    import os
    import signal
    import subprocess
    from contextlib import suppress
    from skillclaw.skill_hub import SkillHub
    from skillclaw.validation_store import ValidationStore
    from .validation import error_result

    # 离线回放允许较长推理；配置在子进程内生效，避免只改父进程常量。
    # 保留 validation.py 的真实回放、有限重试和结果处理，发布入口只管期限。
    replay_limits = {"request_timeout_sec": 240.0, "max_retries": 2,
                     "job_timeout_sec": 600.0}

    def run_isolated_job(job, validator_alias, root):
        job_id = _safe_id(job["job_id"])
        directory = Path(root) / "validation_processes" / job_id
        directory.mkdir(parents=True, exist_ok=False)
        request = {"job_id": job_id, "validator_alias": validator_alias,
                   "directory": str(directory), "output_dir": str(config.OUTPUT_DIR),
                   "skillclaw_run_id": os.environ["ASSEMBLY_SKILLCLAW_RUN_ID"],
                   "replay_limits": replay_limits}
        request_path = directory / "request.json"
        write_json(request_path, request)
        bootstrap = (
            "import json, sys\n"
            "from assembly.skillclaw import validation\n"
            "with open(sys.argv[1], encoding='utf-8') as stream:\n"
            "    limits = json.load(stream)['replay_limits']\n"
            "validation.REQUEST_TIMEOUT = limits['request_timeout_sec']\n"
            "validation.MAX_RETRIES = limits['max_retries']\n"
            "validation.main()\n"
        )
        started = time.time()
        with (directory / "console.log").open("w") as log:
            proc = subprocess.Popen(
                [sys.executable, "-B", "-c", bootstrap, str(request_path)],
                cwd=config.ASSEMBLY_DIR, env=os.environ.copy(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=replay_limits["job_timeout_sec"])
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if proc.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=2)
        process = {"pid": proc.pid, "started_at": started, "finished_at": time.time(),
                   "elapsed_sec": time.time() - started,
                   "timeout_sec": replay_limits["job_timeout_sec"],
                   "timed_out": timed_out, "returncode": proc.returncode,
                   "reaped": proc.poll() is not None, "directory": str(directory),
                   "replay_limits": replay_limits}
        write_json(directory / "process.json", process)
        result_path = directory / "result.json"
        if not timed_out and proc.returncode == 0 and result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(payload.get("result"), dict):
                    return {**payload, "process": process}
            except (OSError, ValueError, AttributeError):
                pass
        progress_path = directory / "progress.json"
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
        except (OSError, ValueError):
            progress = {"status": "incomplete_write", "reason": "子进程终止时进度文件未完整写出"}
        reason = ("验证子进程超过 600 秒，已终止并回收" if timed_out else "验证子进程异常退出")
        payload = {"summary": {"checked_jobs": 1, "validated_jobs": 0, "skipped_jobs": 1},
                   "result": error_result(job_id, reason, progress), "process": process}
        write_json(result_path, payload)
        return payload

    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("回放验证只读取环境变量 DEEPSEEK_API_KEY")
    cfg = _client_config(_safe_id(validator_alias))
    store = ValidationStore.from_config(cfg)
    jobs = store.list_jobs()
    if evolution is not None:
        ids = {row.get("validation_job_id") for row in evolution.get("evolutions", [])}
        jobs = [job for job in jobs if job.get("job_id") in ids]
    if not jobs:
        raise RuntimeError("没有服务端生成的待验证候选；不能跳过验证直接发布")
    root = Path(runtime_paths()["root"])
    write_json(root / "validation_jobs.json", jobs)
    validation_summary = {"checked_jobs": len(jobs), "validated_jobs": 0,
                          "skipped_jobs": 0, "error_jobs": 0, "jobs": []}
    results = {}
    for job in jobs:
        outcome = run_isolated_job(job, validator_alias, root)
        validation_summary["jobs"].append(outcome)
        error = outcome["result"].get("validation_error", False)
        validation_summary["error_jobs" if error else "validated_jobs"] += 1
        validation_summary["skipped_jobs"] += int(error)
        # 错误只落本地验收材料，不伪装成上游的评分或拒绝投票。
        results[job["job_id"]] = ([outcome["result"]] if error else store.list_results(job["job_id"]))
        write_json(root / "validation_results.json", results)
        write_json(root / "validation_worker.json", validation_summary)
    validation_summary["reason"] = "validation_error" if validation_summary["error_jobs"] else "validated"
    write_json(root / "validation_results.json", results)
    write_json(root / "validation_worker.json", validation_summary)
    # 即使验证拒绝，也由原生服务端记录拒绝决定；适配器不篡改验证结论。
    publication = ({"evolutions": [], "skipped": True, "reason": "验证错误，未请求发布"}
                   if validation_summary["error_jobs"] else trigger_evolution(endpoint))
    decisions = {job["job_id"]: store.load_decision(job["job_id"]) for job in jobs}
    manifest = SkillHub.from_config(cfg).list_remote()
    published = [entry for entry in publication.get("evolutions", []) if entry.get("uploaded")]
    result = {
        "validator_alias": validator_alias,
        "validation": validation_summary,
        "results": results,
        "decisions": decisions,
        "publication": publication,
        "manifest": {entry["name"]: entry for entry in manifest},
        "published_versions": [
            {"name": entry["skill_name"], "version": entry["version"]} for entry in published
        ],
    }
    write_json(root / "publication.json", result)
    return result


def _loaded_hashes(manager) -> tuple[str, dict[str, str]]:
    hashes = {
        skill["name"]: hashlib.sha256(Path(skill["file_path"]).read_bytes()).hexdigest()
        for skill in manager.get_all_skills()
    }
    if len(hashes) == 1:
        return next(iter(hashes.values())), hashes
    joined = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(joined).hexdigest(), hashes


def run_task(instance_home: str | Path, prompt: str, *, load_skills: bool = True,
             instance: str = "C", session_id: str | None = None, max_steps: int = 6) -> dict:
    """最小 OpenAI 工具调用 Agent：真实选读 SkillManager 目录中的技能。

    本函数不是 Hermes 运行时。它使用真实 DeepSeek 模型，提供仅能读取本实例
    已加载 SKILL.md 的 read 工具，保存模型选择、工具读取哈希和最终回答。
    """
    _upstream()
    import httpx
    from openai import OpenAI
    from skillclaw.skill_manager import SkillManager

    home = Path(instance_home)
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    manager = SkillManager(str(skills_dir), retrieval_mode="template")
    loaded_hash, skill_hashes = _loaded_hashes(manager)
    allowed = {Path(skill["file_path"]).resolve(): skill for skill in manager.get_all_skills()} if load_skills else {}
    catalog = manager.build_injection_prompt() if allowed else ""
    system = "你是后端工程助手。依据用户提供的事实回答；不知道本系统的约定时明确说明，不编造。"
    if catalog:
        system += "\n\n" + catalog
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    read_tool = {"type": "function", "function": {
        "name": "read", "description": "读取本实例已加载的技能文件。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"], "additionalProperties": False},
    }}
    session_id = session_id or f"{_safe_id(instance)}-{uuid.uuid4().hex}"
    client = OpenAI(api_key=config.api_key(), base_url=config.BASE_URL,
                    timeout=httpx.Timeout(300.0, connect=30.0), max_retries=2)
    turns: list[dict] = []
    reads: list[dict] = []
    calls: list[dict] = []
    answer = ""
    failure: dict[str, str] | None = None
    started = time.monotonic()
    try:
        for step in range(1, max_steps + 1):
            request = {
                "model": config.MODEL, "messages": messages,
                "temperature": 0.1, "max_completion_tokens": 4096,
                "extra_body": {"thinking": {"type": "disabled"}},
            }
            # 空目录或显式停用技能时，没有合法读取目标，不暴露 read 工具。
            if allowed:
                request["tools"] = [read_tool]
            request["extra_body"] = {**request.get("extra_body", {}), "thinking": {"type": "disabled"}}
            response = client.chat.completions.create(**request)
            message = response.choices[0].message
            assistant = message.model_dump(exclude_none=True)
            messages.append(assistant)
            tool_calls = [tool.model_dump() for tool in (message.tool_calls or [])]
            turn = {
                "turn_num": step,
                "raw_turn_kind": "tool_use" if tool_calls else "final",
                "prompt_text": prompt, "response_text": message.content or "",
                "tool_calls": tool_calls, "read_skills": [], "modified_skills": [],
                "tool_results": [], "tool_results_raw": [], "tool_observations": [],
                "tool_errors": [], "injected_skills": list(skill_hashes) if load_skills else [],
                "prm_score": None,
            }
            if getattr(message, "reasoning_content", None):
                turn["reasoning_content"] = message.reasoning_content
            turns.append(turn)
            calls.append({"response_id": response.id, "model": response.model,
                          "finish_reason": response.choices[0].finish_reason,
                          "usage": response.usage.model_dump() if response.usage else None})
            if not tool_calls:
                answer = message.content or ""
                if not answer:
                    raise RuntimeError("真实模型没有返回正文，不能视为完成任务")
                break
            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments)
                    target = Path(arguments["path"]).expanduser().resolve()
                    if call.function.name != "read" or target not in allowed:
                        raise ValueError("只能读取本实例 SkillManager 已加载的技能文件")
                    content = target.read_text(encoding="utf-8")
                    skill = allowed[target]
                    read_record = {"skill_name": skill["name"], "path": str(target),
                                   "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
                    reads.append(read_record)
                    turn["read_skills"].append(read_record)
                except (ValueError, KeyError, OSError) as exc:
                    content = f"read 工具失败：{exc}"
                    turn["tool_errors"].append({"tool_name": "read", "error": content})
                result = {"role": "tool", "tool_call_id": call.id, "content": content}
                messages.append(result)
                turn["tool_results_raw"].append(result)
        else:
            raise RuntimeError(f"Agent 在 {max_steps} 步内没有完成任务")
    except BaseException as exc:
        # 失败也保留已发生的真实调用和工具轨迹；密钥不进入证据文件。
        failure = {"type": type(exc).__name__,
                   "message": str(exc).replace(client.api_key, "[密钥已隐藏]")}
        raise
    finally:
        client.close()
        result = {
            "session_id": session_id, "instance": instance, "prompt": prompt,
            "agent_runtime": "最小 OpenAI 工具调用 Agent + SkillClaw SkillManager（非 Hermes）",
            "model": config.MODEL, "answer": answer, "turns": turns,
            "loaded_skill_hash": loaded_hash, "skill_hashes": skill_hashes,
            "read_skills": reads, "model_calls": calls, "messages": messages,
            "offered_tools": ["read"] if allowed else [],
            "stop_reason": "error" if failure else "final_answer",
            "error": failure,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_json(home / "runs" / f"{_safe_id(session_id)}.json", result)
        write_json(Path(runtime_paths()["root"]) / "agent_runs" / f"{_safe_id(session_id)}.json", result)
    return result


def pull_and_load(endpoint: str, instance_home: str | Path, prompt: str | None = None) -> tuple[str, dict]:
    """原生拉取、加载并真实运行任务；默认探测不提供内部路由答案。"""
    _upstream()
    from skillclaw.skill_hub import SkillHub
    from skillclaw.skill_manager import SkillManager

    home = Path(instance_home)
    cfg = _client_config("C", home)
    Path(cfg.skills_dir).mkdir(parents=True, exist_ok=True)
    hub = SkillHub.from_config(cfg)
    pull_result = hub.pull_skills(cfg.skills_dir)
    write_json(Path(runtime_paths()["root"]) / "third_instance_pull.json", pull_result)
    if pull_result.get("restored_from_backup"):
        raise RuntimeError("SkillClaw 拉取失败并恢复了备份，不能记为本次已发布版加载成功")
    total_remote = int(pull_result.get("total_remote", 0))
    obtained = int(pull_result.get("downloaded", 0)) + int(pull_result.get("skipped", 0))
    if obtained != total_remote:
        raise RuntimeError(f"SkillClaw 拉取不完整：成功或已存在 {obtained} 项，远端 {total_remote} 项")
    manager = SkillManager(cfg.skills_dir, retrieval_mode="template")
    loaded_hash, hashes = _loaded_hashes(manager)
    if not hashes:
        raise RuntimeError("共享存储中没有已发布技能，第三实例不能完成加载")
    manifest = {entry["name"]: entry for entry in hub.list_remote()}
    if set(hashes) != set(manifest) or len(manifest) != total_remote:
        raise RuntimeError("第三实例加载的技能集合未完整覆盖发布清单，或拉取期间清单发生变化")
    for name, digest in hashes.items():
        if manifest.get(name, {}).get("sha256") != digest:
            raise RuntimeError(f"第三实例技能哈希与发布清单不一致：{name}")
    probe = prompt or (
        "这是总装支付演示系统的值班任务。请依据本系统资料或已加载技能处理订单ORD-C-PROBE："
        '{"amount_due":900,"transactions":[{"txn_id":"TXN-PROBE","amount":900,"status":"success"}],'
        '"merchant_notification":"merchant_notification_missing"}。'
        "返回JSON对象，字段state、paid_amount、remaining、route、action。"
        "route未知时写unknown，不猜测内部约定。"
    )
    behavior = run_task(home, probe, load_skills=True)
    behavior["pull_result"] = pull_result
    behavior["remote_manifest"] = manifest
    behavior["local_hashes"] = hashes
    behavior["evolve_endpoint"] = endpoint
    write_json(Path(runtime_paths()["root"]) / "third_instance.json", behavior)
    return loaded_hash, behavior
