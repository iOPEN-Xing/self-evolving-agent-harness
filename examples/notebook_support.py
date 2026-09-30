"""第 03 至 09 讲共用的本地运行设施；教学机制写在各讲 notebook 中。"""
from __future__ import annotations

import atexit
import http.client
import json
import re
import shlex
import signal
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox


class Lab:
    """启动真实 SDK 与 GLM 适配器，并记录不含认证头的模型请求。"""

    def __init__(self, directory: Path):
        key = os.environ.get("GLM_API_KEY", "").strip()
        if not key:
            raise ValueError("请在启动进程前设置 GLM_API_KEY 环境变量。")
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("请使用 Python 3.12 运行本练习。")
        for package, expected in (("openai-codex", "0.154.0"), ("litellm", "1.101.0")):
            if version(package) != expected:
                raise RuntimeError(f"请安装 {package}=={expected}，本练习按该版本核验。")
        self.directory = directory.resolve()
        self.run_id = os.environ.get("LAB_VALIDATION_RUN_ID") or (time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(8))
        if not re.fullmatch(r"[a-zA-Z0-9-]{8,80}", self.run_id):
            raise ValueError("本次运行编号不合法。")
        self.runtime = self.directory / ".runtime" / self.run_id
        self.runtime.mkdir(parents=True, mode=0o700, exist_ok=False)
        self._secrets = [key]
        self._request_lock = threading.Lock()
        self._extra_clients = []
        self.work = self.runtime / "work"
        self.work.mkdir()
        (self.runtime / "codex-home").mkdir()
        # 标记实验项目根，避免把上层项目规则当成本讲的实验条件。
        (self.work / ".git").mkdir()
        self.requests = []
        self.results = []
        self.turn_objects = []
        self.codex = self.proxy = self.observer = self.log = None
        self._log_thread = None
        self._closed = False
        self._cleanup_errors = []
        atexit.register(self.close)
        try:
            self._start(key)
        except BaseException:
            self.close()
            raise

    def _start(self, key):
        token = "sk-lab-" + secrets.token_hex(24)
        self._secrets.append(token)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        (self.runtime / "custom_handler.py").write_text('''from litellm.integrations.custom_logger import CustomLogger
class GlmFix(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if not isinstance(data.get("reasoning_effort"), str):
            data.pop("reasoning_effort", None)
        data.pop("reasoning", None)
        return data
proxy_handler = GlmFix()
''', encoding="utf-8")
        config_path = self.runtime / "litellm.yaml"
        config_path.write_text('''model_list:
  - model_name: glm-codex
    litellm_params:
      model: openai/chat_completions/glm-5.2
      api_base: https://open.bigmodel.cn/api/paas/v4
      api_key: os.environ/GLM_API_KEY
general_settings:
  master_key: os.environ/LAB_PROXY_TOKEN
litellm_settings:
  drop_params: true
  set_verbose: false
  callbacks: custom_handler.proxy_handler
''', encoding="utf-8")
        env = os.environ.copy()
        env.update(GLM_API_KEY=key, LAB_PROXY_TOKEN=token, PYTHONPATH=str(self.runtime))
        self.log = (self.runtime / "adapter.log").open("w", encoding="utf-8")
        self.proxy = subprocess.Popen(
            [str(Path(sys.executable).with_name("litellm")), "--config", str(config_path),
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=self.runtime, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", start_new_session=True,
        )
        def capture_log():
            for line in self.proxy.stdout:
                for secret in self._secrets:
                    line = line.replace(secret, "<密钥已隐藏>")
                self.log.write(line)
                self.log.flush()
        self._log_thread = threading.Thread(target=capture_log, daemon=True)
        self._log_thread.start()
        for _ in range(90):
            if self.proxy.poll() is not None:
                raise RuntimeError("适配器启动失败，请检查本讲 .runtime 内的本地日志。")
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f"http://127.0.0.1:{port}/health/liveliness", timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            raise TimeoutError("适配器启动超时。")

        lab = self

        class Observer(BaseHTTPRequestHandler):
            # 只记录 JSON 请求体中的教学字段，不记录 HTTP 认证头。
            def do_POST(self):
                self.connection.settimeout(10)
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8 * 1024 * 1024:
                        raise ValueError("请求体长度不合法")
                    body = self.rfile.read(length)
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("请求体必须是对象")
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                observed = {k: v for k, v in payload.items() if k in (
                    "model", "instructions", "input", "tools", "previous_response_id", "reasoning")}
                # 单独复制，避免协议转换和并发处理改写已经保存的请求。
                observed = json.loads(json.dumps(observed))
                with lab._request_lock:
                    lab.requests.append(observed)
                    lab._write_json(f"request-{len(lab.requests):03}.json", observed)
                # Responses 允许 ExternalMessage 没有 call_id；Chat Completions
                # 要求工具结果与一个调用配对。补协议信封，保持工具级权限，
                # 不把外部材料抬成 user/developer 指令，也不执行任何新工具。
                if isinstance(payload.get("input"), list):
                    converted = []
                    for item in payload["input"]:
                        if isinstance(item, dict) and item.get("type") == "function_call_output" and not item.get("call_id"):
                            call_id = "external_" + str(item.get("id") or secrets.token_hex(6))
                            converted.append({"type": "function_call", "call_id": call_id,
                                              "name": item.get("name", "external_delivery"), "arguments": "{}"})
                            item = {**item, "call_id": call_id}
                        converted.append(item)
                    payload["input"] = converted
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=240)
                try:
                    conn.request("POST", self.path, body=body, headers={
                        "Content-Type": "application/json", "Authorization": "Bearer " + token})
                    response = conn.getresponse()
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException) as exc:
                    lab._write_json("adapter-failure.json", {"error_type": type(exc).__name__, "error": lab.clean(exc)})
                    self.close_connection = True
                finally:
                    conn.close()

            def log_message(self, *_args):
                pass

        self.observer = ThreadingHTTPServer(("127.0.0.1", 0), Observer)
        self.observer.daemon_threads = True
        threading.Thread(target=self.observer.serve_forever, daemon=True).start()
        self.config = CodexConfig(
            cwd=str(self.work),
            config_overrides=(
                'model="glm-codex"', 'model_provider="lab"',
                'model_providers.lab.name="GLM-5.2 本地适配器"',
                f'model_providers.lab.base_url="http://127.0.0.1:{self.observer.server_port}/v1"',
                'model_providers.lab.env_key="LAB_LOCAL_TOKEN"',
                'model_providers.lab.wire_api="responses"',
                'model_providers.lab.requires_openai_auth=false',
                'features.multi_agent=false', 'features.multi_agent_v2=false',
                'shell_environment_policy.inherit="core"',
                'sandbox_workspace_write.network_access=false',
                'sandbox_workspace_write.exclude_tmpdir_env_var=true',
                'sandbox_workspace_write.exclude_slash_tmp=true',
                'shell_environment_policy.exclude=["*KEY*","*TOKEN*","*SECRET*","*PASSWORD*"]',
            ),
            env={**{name: "" for name in os.environ
                    if any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))},
                 "CODEX_HOME": str(self.runtime / "codex-home"),
                 "LAB_LOCAL_TOKEN": token, "GLM_API_KEY": "", "BIGMODEL_API_KEY": "",
                 "OPENAI_API_KEY": "",
                 # 有的启动器把中文路径编码损坏后放进 shell 的最近命令变量。
                 # Codex 0.154.0 构造工具环境时要求有效 UTF-8。
                 "_": sys.executable},
        )
        self.codex = Codex(self.config)

    def _write_json(self, name, value):
        # 原始路径仅留在私有运行目录；凭证在任何 JSON 记录中都隐藏。
        text = json.dumps(value, ensure_ascii=False, indent=2)
        for secret in self._secrets:
            text = text.replace(secret, "<密钥已隐藏>")
        (self.runtime / name).write_text(text, encoding="utf-8")

    def manage_client(self, client):
        """额外的底层客户端也由本次 Lab 负责释放。"""
        self._extra_clients.append(client)
        return client

    def thread(self, **kwargs):
        cwd = Path(kwargs.get("cwd", self.work)).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(self.work):
            raise ValueError("Thread 的 cwd 必须是本次 work 内已存在的目录。")
        kwargs["cwd"] = str(cwd)
        options = dict(cwd=str(self.work), sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all)
        options.update(kwargs)
        return self.codex.thread_start(**options)

    def run(self, thread, prompt, *, timeout=360, **kwargs):
        """限定一次 Turn 的等待时间；失败不伪装成模型完成。"""
        box = {}
        def worker():
            try:
                # turn/start 的协议等待也计入预算，不能在计时线程外阻塞。
                handle = thread.turn(prompt, **kwargs)
                box["handle"] = handle
                box["result"] = handle.run()
            except BaseException as exc:
                box["error"] = exc
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        worker_thread.join(timeout)
        if worker_thread.is_alive():
            interruption = []
            def interrupt():
                try:
                    if "handle" in box:
                        box["handle"].interrupt()
                except Exception as exc:
                    interruption.append(self.clean(exc))
            stopper = threading.Thread(target=interrupt, daemon=True)
            stopper.start()
            stopper.join(5)
            worker_thread.join(5)
            self._write_json("turn-failure.json", {"thread_id": thread.id, "status": "timeout",
                "timeout_seconds": timeout, "interrupt_errors": interruption})
            try:
                self.close()
            finally:
                raise TimeoutError(f"本次 Turn 超过 {timeout} 秒；已请求中断并关闭本次连接。")
        if "error" in box:
            exc = box["error"]
            self._write_json("turn-failure.json", {"thread_id": thread.id,
                "error_type": type(exc).__name__, "error": self.clean(exc)})
            raise RuntimeError(f"Turn 执行失败：{type(exc).__name__}: {self.clean(exc)}") from None
        result = box["result"]
        self.turn_objects.append(result)
        self.results.append({"thread_id": thread.id, "turn_id": result.id,
                             "status": result.status.value})
        self._write_json(f"turn-{len(self.results):03}.json",
            {"thread_id": thread.id, "turn_id": result.id, "status": result.status.value,
             "final_response": result.final_response,
             "items": [i.model_dump(mode="json", by_alias=True) for i in result.items]})
        if result.status.value != "completed":
            raise AssertionError(f"Turn 未完成：{result.status.value}；请查看本次 turn 记录。")
        print("Turn：", result.status.value)
        print(self.clean(result.final_response))
        return result

    def clean(self, text):
        text = str(text)
        for secret in self._secrets:
            text = text.replace(secret, "<密钥已隐藏>")
        return text.replace(str(self.runtime), "<本次运行目录>").replace(str(Path.home()), "~")

    def request_text(self, index=-1):
        if not self.requests or not -len(self.requests) <= index < len(self.requests):
            raise AssertionError(f"未捕获预期的模型请求：索引 {index}，实际 {len(self.requests)} 条。")
        return json.dumps(self.requests[index], ensure_ascii=False)

    def show_requests(self, start=0):
        """展示真实请求的组成；不把字节数或字符数称为 token 数。"""
        for i, request in enumerate(self.requests[start:], start + 1):
            inputs = request.get("input", [])
            print(f"请求 {i}：工具 {len(request.get('tools', []))} 个；输入字符 {len(json.dumps(inputs, ensure_ascii=False))}")
            for item in inputs if isinstance(inputs, list) else []:
                print(" ", item.get("type", "message"), item.get("role", ""),
                      self.clean(json.dumps(item, ensure_ascii=False))[:220])

    def verify(self, checks, **facts):
        if not checks or any(type(value) is not bool for value in checks.values()):
            raise ValueError("检查项不能为空，且每项必须是 bool；字符串或计数不能代替判断。")
        reserved = {"model", "sdk", "adapter", "checked_at", "checks", "turns", "request_count", "run_id"}
        if reserved.intersection(facts):
            raise ValueError("附加事实不能覆盖验证记录的保留字段。")
        for name, passed in checks.items():
            print(("PASS" if passed else "FAIL"), name)
        record = {"run_id": self.run_id, "model": "glm-5.2", "sdk": version("openai-codex"),
                  "adapter": version("litellm"), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "checks": checks, "turns": self.results, "request_count": len(self.requests), **facts}
        self._write_json("checks.json", record)
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise AssertionError("检查失败：" + "；".join(failed))
        return record

    def close(self):
        if self._closed:
            if self._cleanup_errors:
                raise RuntimeError("上次进程清理未完全成功：" + "；".join(self._cleanup_errors))
            return
        self._closed = True
        errors = []
        def attempt(action):
            try:
                action()
            except Exception as exc:
                errors.append(self.clean(exc))
        for client in reversed(self._extra_clients):
            attempt(client.close)
        if self.codex:
            attempt(self.codex.close)
        if self.observer:
            attempt(self.observer.shutdown)
            attempt(self.observer.server_close)
        if self.proxy:
            def stop_proxy():
                # 适配器可能派生工作进程，一并结束本次专属进程组。
                try:
                    os.killpg(self.proxy.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.proxy.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proxy.pid, signal.SIGKILL)
                    self.proxy.wait(5)
            attempt(stop_proxy)
        if self._log_thread:
            self._log_thread.join(5)
        if self.proxy and self.proxy.stdout and not (self._log_thread and self._log_thread.is_alive()):
            attempt(self.proxy.stdout.close)
        if self._log_thread and self._log_thread.is_alive():
            errors.append("适配器日志线程未退出")
        elif self.log:
            attempt(self.log.close)
        atexit.unregister(self.close)
        self._cleanup_errors = errors
        if errors:
            self._write_json("cleanup-failure.json", {"errors": errors})
            raise RuntimeError("进程清理未完全成功：" + "；".join(errors))


def tool_output_contains(request, marker):
    """只看工具回执，避免把用户消息或模型复述误认作工具结果。"""
    inputs = request.get("input", [])
    return isinstance(inputs, list) and any(
        isinstance(item, dict) and item.get("type") == "function_call_output"
        and marker in str(item.get("output", "")) for item in inputs)


def command_receipts(requests):
    """按 call_id 配对真实命令和回执；重复携带的历史只计一次。"""
    calls, receipts = {}, {}
    for request in requests:
        inputs = request.get("input", [])
        for item in inputs if isinstance(inputs, list) else []:
            if not isinstance(item, dict):
                continue
            call_id = item.get("call_id")
            if not call_id:
                continue
            if item.get("type") == "function_call" and item.get("name") == "exec_command":
                try:
                    args = json.loads(item.get("arguments", "{}"))
                except (TypeError, ValueError):
                    continue
                if isinstance(args, dict) and isinstance(args.get("cmd"), str):
                    calls[call_id] = args
            elif item.get("type") == "function_call_output" and call_id in calls:
                output = str(item.get("output", ""))
                match = re.search(r"^Process exited with code (-?\d+)\s*$", output, re.M)
                receipts[call_id] = {"call_id": call_id, "command": calls[call_id]["cmd"],
                    "arguments": calls[call_id], "output": output,
                    "exit_code": int(match[1]) if match else None}
    return list(receipts.values())


def denied_write(receipts, command, target):
    """必须是指定目标的实际写入拒绝；启动失败、缺路径和无回执均失败。"""
    for receipt in receipts:
        output = receipt["output"].lower()
        if (receipt["command"] == command and receipt["exit_code"] not in (None, 0)
            and str(target).lower() in output
            and not any(word in output for word in ("sandbox_apply", "no such file", "command not found"))
            and any(word in output for word in ("operation not permitted", "permission denied", "read-only file system"))):
            return True
    return False


def exact_command(raw, expected):
    """只展开一个明确的 shell 包装，不接受尾随命令或任意解释器。"""
    try:
        parts = raw if isinstance(raw, list) else shlex.split(raw)
        if not all(isinstance(part, str) for part in parts):
            return False
        return parts == shlex.split(expected) or (
            len(parts) == 3 and parts[0] in ("/bin/sh", "/bin/bash", "/bin/zsh")
            and parts[1] in ("-c", "-lc") and parts[2] == expected)
    except (ValueError, TypeError):
        return False


def read_json_answer(text, required):
    """结构不合法就报错，不通过摘取关键词或修补回答制造通过。"""
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise AssertionError("模型回答不是完整 JSON 对象，请核对本次 Turn 输出。") from exc
    if not isinstance(answer, dict) or set(answer) != set(required):
        raise AssertionError(f"回答字段不符合约定，要求：{', '.join(required)}")
    return answer
