"""启动、检查和停止上游 SkillClaw 演化服务。

上游 ``--mock`` 会执行一轮后退出，因此常驻 HTTP 服务使用上游提供的
``local`` 对象存储后端。模型、会话评判、技能演化和验证仍由上游真实执行。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import config
from ..contracts import write_json


def runtime_paths() -> dict[str, Any]:
    """给同一演示进程及其子进程提供一致、隔离的运行目录。"""
    run_id = os.environ.setdefault(
        "ASSEMBLY_SKILLCLAW_RUN_ID",
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", run_id):
        raise ValueError("ASSEMBLY_SKILLCLAW_RUN_ID 只能包含字母、数字、点、下划线和连字符")
    root = config.OUTPUT_DIR / "skillclaw" / "runs" / run_id
    return {"root": root, "store": root / "store", "group_id": "assembly-" + run_id}


def _read_json(endpoint: str, path: str) -> dict[str, Any]:
    # 本机健康检查不经过调用者的 HTTP 代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint.rstrip("/") + path, timeout=1.0) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("SkillClaw 返回的健康检查内容不是 JSON 对象")
    return value


def _server_env(paths: dict[str, Any], port: int) -> dict[str, str]:
    # config.api_key 只读取既有环境配置；值只传入子进程环境，不写证据文件。
    api_key = config.api_key()
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("EVOLVE_") or name.lower() in {
            "http_proxy", "https_proxy", "all_proxy",
        }:
            env.pop(name, None)
    root = Path(paths["root"])
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": config.BASE_URL,
        "EVOLVE_MODEL": config.MODEL,
        "EVOLVE_ENGINE": "workflow",
        "EVOLVE_LLM_API_TYPE": "openai-completions",
        "EVOLVE_LLM_MAX_TOKENS": os.environ.get("ASSEMBLY_SKILLCLAW_MAX_TOKENS", "16384"),
        "EVOLVE_STORAGE_BACKEND": "local",
        "EVOLVE_STORAGE_LOCAL_ROOT": str(paths["store"]),
        "EVOLVE_SKILL_STORAGE_BACKEND": "local",
        "EVOLVE_GROUP_ID": str(paths["group_id"]),
        "EVOLVE_HISTORY_LOG": str(root / "evolve_history.jsonl"),
        "EVOLVE_PROCESSED_LOG": str(root / "evolve_processed.json"),
        "EVOLVE_PORT": str(port),
        "EVOLVE_INTERVAL": "86400",
        "EVOLVE_USE_SESSION_JUDGE": "1",
        "EVOLVE_USE_SKILL_VERIFIER": "1",
        "EVOLVE_SKILL_VERIFIER_MIN_SCORE": "0.75",
        "EVOLVE_PUBLISH_MODE": "validated",
        "EVOLVE_VALIDATION_REQUIRED_RESULTS": "1",
        "EVOLVE_VALIDATION_REQUIRED_APPROVALS": "1",
        "EVOLVE_VALIDATION_MIN_MEAN_SCORE": "0.75",
        "EVOLVE_VALIDATION_MAX_REJECTIONS": "1",
        "EVOLVE_SKILL_RELOAD_MODE": "off",
    })
    return env


def start_server(port: int = config.EVOLVE_SERVER_PORT) -> subprocess.Popen:
    """从上游源码启动服务，直到本次子进程通过健康检查再返回。

    上游 CLI 固定监听 ``0.0.0.0``；``EVOLVE_SERVER_HOST`` 用于访问服务。
    端口已被占用时直接失败，避免把已有服务误记为本次启动结果。
    """
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("SkillClaw 端口必须在 1—65535 之间")
    interpreter = config.HERMES_SRC / ".venv" / "bin" / "python"
    if not interpreter.is_file():
        raise FileNotFoundError(f"找不到 Hermes 虚拟环境解释器：{interpreter}")
    if not (config.SKILLCLAW_SRC / "evolve_server" / "__main__.py").is_file():
        raise FileNotFoundError(f"找不到 SkillClaw 服务入口：{config.SKILLCLAW_SRC}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise RuntimeError(f"端口 {port} 无法绑定；未启动或复用任何既有 SkillClaw 服务") from exc

    paths = runtime_paths()
    root = Path(paths["root"])
    env = _server_env(paths, port)
    Path(paths["store"]).mkdir(parents=True, exist_ok=True)
    log_path = root / "server.log"
    endpoint = f"http://{config.EVOLVE_SERVER_HOST}:{port}"
    command = [
        str(interpreter), "-B", "-m", "evolve_server",
        "--storage-backend", "local", "--local-root", str(paths["store"]),
        "--group-id", str(paths["group_id"]),
        "--port", str(port), "--interval", "86400", "--engine", "workflow",
        "--skill-verifier", "--publish-mode", "validated", "--model", config.MODEL,
    ]
    started_at = datetime.now(timezone.utc).isoformat()
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            command, cwd=config.SKILLCLAW_SRC, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
    # 停止时使用原运行目录，避免外部环境变量后来变化造成证据写错位置。
    proc._assembly_skillclaw_root = root
    record: dict[str, Any] = {
        "started_at": started_at,
        "pid": proc.pid,
        "command": command,
        "cwd": str(config.SKILLCLAW_SRC),
        "endpoint": endpoint,
        "listen_host": "0.0.0.0",
        "storage_backend": "local",
        "storage_implementation": "skillclaw.object_store.LocalObjectStore",
        "storage_root": str(paths["store"]),
        "group_id": paths["group_id"],
        "model": config.MODEL,
        "base_url": config.BASE_URL,
        "log": str(log_path),
        "ready": False,
    }
    deadline = time.monotonic() + float(os.environ.get("ASSEMBLY_SKILLCLAW_START_TIMEOUT", "60"))
    last_error = "服务尚未返回健康检查结果"
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"evolve_server 提前退出（{proc.returncode}）；日志：{log_path}")
            try:
                health = _read_json(endpoint, "/health")
                status = _read_json(endpoint, "/status")
                if health.get("status") == "ok" and status.get("running") is True:
                    # 再查进程状态，避免子进程绑定失败后误认同端口其他服务。
                    time.sleep(0.1)
                    if proc.poll() is not None:
                        raise RuntimeError(f"健康检查后服务已退出；日志：{log_path}")
                    record.update({
                        "ready": True, "health": health, "status": status,
                        "ready_at": datetime.now(timezone.utc).isoformat(),
                    })
                    write_json(root / "startup.json", record)
                    return proc
                last_error = "健康检查或运行状态未就绪"
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise TimeoutError(f"等待 evolve_server 就绪超时：{last_error}；日志：{log_path}")
    except BaseException as exc:
        record["error"] = str(exc)
        write_json(root / "startup.json", record)
        stop_server(proc)
        raise


def stop_server(proc: subprocess.Popen) -> None:
    """先发送 SIGTERM；上游周期任务未退出时再强制结束并回收子进程。"""
    forced = False
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            forced = True
            proc.kill()
            proc.wait(timeout=5)
    root = getattr(proc, "_assembly_skillclaw_root", None)
    if root is not None:
        write_json(Path(root) / "shutdown.json", {
            "pid": proc.pid,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "forced_kill": forced,
            "returncode": proc.returncode,
        })
