"""回放验证的有界网络适配与逐任务子进程；不改变上游提示、评分或发布条件。"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import MethodType

from .. import config
from ..contracts import write_json

REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 2
JOB_TIMEOUT = 300.0


def error_result(job_id, reason, progress=None):
    return {"job_id": job_id, "validator_mode": "replay", "decision": "error",
            "accepted": False, "score": None, "validation_error": True,
            "reason": reason, "checks": {}, "progress": progress or {}}


class ReplayNetworkError(RuntimeError):
    pass


class ReplayNetwork:
    def __init__(self, cfg, directory):
        import httpx
        from openai import AsyncOpenAI

        self.directory = Path(directory)
        self.context = {}
        self.clients = {}
        # SDK 与 httpx 不再各自重试；总时限覆盖连接、读取和完整流式响应。
        for name, base_url, model in (
            ("glm_chat", cfg.llm_api_base, cfg.llm_model_id),
            ("prm_score", cfg.prm_url, cfg.prm_model),
        ):
            self.clients[name] = AsyncOpenAI(
                api_key=os.environ["GLM_API_KEY"], base_url=base_url,
                max_retries=0, timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0),
                http_client=httpx.AsyncClient(
                    trust_env=False, timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0)),
            )
        self.emit("configured", request_timeout_sec=REQUEST_TIMEOUT,
                  max_retries=MAX_RETRIES, backoff_sec=[1, 2], sdk_max_retries=0)

    def emit(self, event, **fields):
        value = {"event": event, "at": time.time(), **self.context, **fields}
        with (self.directory / "network.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
            stream.flush()

    async def complete(self, component, **kwargs):
        import httpx
        import openai

        body = dict(kwargs)
        for attempt in range(MAX_RETRIES + 1):
            started = time.monotonic()
            self.emit("request_started", component=component, attempt=attempt + 1)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    response = await self.clients[component].chat.completions.create(**body)
                    if body.get("stream"):
                        parts = []
                        async with response:
                            async for chunk in response:
                                for choice in chunk.choices:
                                    if choice.delta.content:
                                        parts.append(choice.delta.content)
                        content = "".join(parts)
                    else:
                        content = response.choices[0].message.content or ""
                self.emit("request_completed", component=component, attempt=attempt + 1,
                          elapsed_sec=time.monotonic() - started)
                return content
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = isinstance(exc, (TimeoutError, openai.APIConnectionError, httpx.TransportError)) or (
                    isinstance(status, int) and (status in (408, 409, 429) or status >= 500))
                # 沿用上游的两种兼容处理，仍受同一重试次数限制。
                response_text = getattr(getattr(exc, "response", None), "text", "") or ""
                if component == "glm_chat" and status == 400:
                    if "'temperature' is not supported" in response_text:
                        body.pop("temperature", None)
                        retryable = True
                    if "Stream must be set to true" in response_text:
                        body["stream"] = True
                        retryable = True
                self.emit("request_failed", component=component, attempt=attempt + 1,
                          elapsed_sec=time.monotonic() - started,
                          error_type=type(exc).__name__, status_code=status,
                          will_retry=retryable and attempt < MAX_RETRIES)
                if not retryable or attempt == MAX_RETRIES:
                    # 不把响应正文、请求头或密钥写入日志。
                    raise ReplayNetworkError(
                        f"{component} 请求失败：{type(exc).__name__}；尝试 {attempt + 1} 次") from None
                await asyncio.sleep(2 ** attempt)

    async def close(self):
        await asyncio.gather(*(client.close() for client in self.clients.values()))


async def validate_one(request):
    from .client import _client_config
    from skillclaw.validation_worker import ValidationWorker
    from skillclaw.prm_scorer import _parse_prm_score
    from evolve_server.core.llm_client import _normalize_temperature

    cfg = _client_config(request["validator_alias"])
    directory = Path(request["directory"])
    worker = ValidationWorker(cfg)
    job_id = request["job_id"]
    network = ReplayNetwork(cfg, directory)
    upstream_chat = worker._client
    # 关闭未使用的同步客户端，避免 asyncio.to_thread 留下无法取消的 SSL 读取。
    upstream_chat._client.close()
    worker._prm_scorer._client.close()

    async def chat(_self, messages, **kwargs):
        requested = kwargs.pop("temperature", upstream_chat.temperature)
        return await network.complete(
            "glm_chat", model=upstream_chat.model, messages=messages,
            max_completion_tokens=kwargs.pop("max_tokens", upstream_chat.max_tokens),
            temperature=_normalize_temperature(upstream_chat.model, requested), **kwargs)

    async def query_once(scorer, messages, vote_id):
        content = await network.complete(
            "prm_score", model=scorer.prm_model, messages=messages,
            temperature=scorer.temperature, max_completion_tokens=scorer.max_new_tokens)
        return _parse_prm_score(content), content

    worker._client.chat = MethodType(chat, worker._client)
    worker._prm_scorer._query_once = MethodType(query_once, worker._prm_scorer)
    original_branch = worker._run_replay_branch
    progress = {"job_id": job_id, "completed_branches": [], "status": "running"}

    async def branch(_self, case, skill, *, label):
        context = {"job_id": job_id, "session_id": case.get("session_id"),
                   "turn_num": case.get("turn_num"), "branch": label}
        network.context = context
        progress.update(current_case=context, status="running")
        write_json(directory / "progress.json", progress)
        try:
            result = await original_branch(case, skill, label=label)
        except Exception as exc:
            progress.update(status="error", error_type=type(exc).__name__, reason=str(exc))
            write_json(directory / "progress.json", progress)
            raise
        progress["completed_branches"].append({**context, "result": result})
        write_json(directory / "progress.json", progress)
        return result

    worker._run_replay_branch = MethodType(branch, worker)
    original_list = worker._store.list_open_jobs
    worker._store.list_open_jobs = lambda **kwargs: [
        job for job in original_list(**kwargs) if job.get("job_id") == job_id]
    try:
        summary = await worker.run_once(force=True)
        result = worker._store.load_result(job_id, request["validator_alias"])
        if result is None:
            result = error_result(job_id, progress.get("reason", "上游未产生验证结果"), progress)
        write_json(directory / "result.json", {"summary": summary, "result": result})
    finally:
        await network.close()


def run_isolated_job(job, validator_alias, root, *, timeout=JOB_TIMEOUT):
    """只将路径与任务标识放入请求文件，密钥通过既有环境继承。"""
    from .client import _safe_id

    job_id = _safe_id(job["job_id"])
    directory = Path(root) / "validation_processes" / job_id
    directory.mkdir(parents=True, exist_ok=False)
    request = {"job_id": job_id, "validator_alias": validator_alias,
               "directory": str(directory), "output_dir": str(config.OUTPUT_DIR),
               "skillclaw_run_id": os.environ["ASSEMBLY_SKILLCLAW_RUN_ID"]}
    request_path = directory / "request.json"
    write_json(request_path, request)
    started = time.time()
    with (directory / "console.log").open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "assembly.skillclaw.validation", str(request_path)],
            cwd=config.ASSEMBLY_DIR, env=os.environ.copy(), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
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
               "elapsed_sec": time.time() - started, "timeout_sec": timeout,
               "timed_out": timed_out, "returncode": proc.returncode, "reaped": proc.poll() is not None,
               "directory": str(directory)}
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
    reason = f"验证子进程超过 {timeout:g} 秒，已终止并回收" if timed_out else "验证子进程异常退出"
    payload = {"summary": {"checked_jobs": 1, "validated_jobs": 0, "skipped_jobs": 1},
               "result": error_result(job_id, reason, progress), "process": process}
    write_json(directory / "result.json", payload)
    return payload


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not os.environ.get("GLM_API_KEY", "").strip():
        raise RuntimeError("验证子进程需要环境变量 GLM_API_KEY")
    os.environ.pop("BIGMODEL_API_KEY", None)
    config.OUTPUT_DIR = Path(request["output_dir"])
    os.environ["ASSEMBLY_SKILLCLAW_RUN_ID"] = request["skillclaw_run_id"]
    from .client import _upstream
    _upstream()
    asyncio.run(validate_one(request))


if __name__ == "__main__":
    main()
