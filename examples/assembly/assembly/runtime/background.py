"""观察 Hermes 原生复盘线程，并验证前台与后台异常之间的隔离。"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from ..contracts import BackgroundReviewResult, TaskRun, write_json
from .foreground import run_task


def _tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按真实 tool_call_id 对应调用和回执，保留后台实际尝试的动作。"""
    results = {
        msg.get("tool_call_id"): msg.get("content", "")
        for msg in messages if msg.get("role") == "tool"
    }
    calls = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"raw": arguments}
            calls.append({
                "id": call.get("id", ""),
                "name": function.get("name", ""),
                "arguments": arguments,
                "result": results.get(call.get("id")),
            })
    return calls


class _ReviewObserver:
    """只包装原生 target 和真实调用；不替换线程、模型或工具结果。"""

    def __init__(self, agent: Any):
        self.agent = agent
        self.records: list[dict[str, Any]] = []
        self.actions: list[str] = []
        self.warnings: list[dict[str, Any]] = []
        self.foreground_observation: dict[str, Any] = {}
        self.join_requested_monotonic: float | None = None

    def __enter__(self) -> "_ReviewObserver":
        import agent.background_review as native
        from run_agent import AIAgent

        self._native = native
        self._agent_class = AIAgent
        self._factory = native.spawn_background_review_thread
        self._run = AIAgent.run_conversation
        self._status = self.agent.status_callback
        self._callback = self.agent.background_review_callback

        def factory(parent, messages_snapshot, *args, **kwargs):
            target, prompt = self._factory(parent, messages_snapshot, *args, **kwargs)
            if parent is not self.agent:
                return target, prompt
            record: dict[str, Any] = {
                "review_memory": kwargs.get("review_memory", False),
                "review_skills": kwargs.get("review_skills", False),
                "prompt": prompt,
                "snapshot_message_count": len(messages_snapshot),
                "spawn_requested_monotonic": time.perf_counter(),
            }
            self.records.append(record)

            def observed_target():
                record["_thread"] = threading.current_thread()
                record["started_monotonic"] = time.perf_counter()
                record["started_at"] = time.time()
                try:
                    return target()
                finally:
                    record["target_exited_monotonic"] = time.perf_counter()
                    record["target_exited_at"] = time.time()

            return observed_target, prompt

        def observed_run(review_agent, *args, **kwargs):
            current = threading.current_thread()
            record = next(
                (item for item in self.records if item.get("_thread") is current),
                None,
            )
            if record is None or review_agent is self.agent:
                return self._run(review_agent, *args, **kwargs)
            try:
                result = self._run(review_agent, *args, **kwargs)
                # 原生复盘没有检查返回字典中的 failed；旁路保留，避免误报成功。
                record["result"] = {
                    key: copy.deepcopy(result.get(key))
                    for key in (
                        "final_response", "completed", "failed", "interrupted",
                        "turn_exit_reason", "api_calls", "model", "provider",
                        "input_tokens", "output_tokens", "total_tokens",
                    )
                }
                return result
            except BaseException as exc:
                record["raised_error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                messages = copy.deepcopy(getattr(review_agent, "_session_messages", []))
                # 复盘 user 消息是实际会话的末个 user 消息；不把继承的前台
                # 工具回执算作后台动作。完整会话仍保留供逐条对照。
                boundary = next(
                    (i for i in range(len(messages) - 1, -1, -1)
                     if messages[i].get("role") == "user"),
                    len(messages),
                )
                record["session_messages"] = messages
                record["new_messages"] = messages[boundary:]
                record["tool_calls"] = _tool_calls(messages[boundary:])

        def status(kind, message):
            if kind == "warn":
                self.warnings.append({
                    "kind": kind, "message": str(message),
                    "at": time.time(), "monotonic": time.perf_counter(),
                    "thread_name": threading.current_thread().name,
                })
            if self._status:
                self._status(kind, message)

        def callback(message):
            self.actions.append(str(message))
            if self._callback:
                self._callback(message)

        native.spawn_background_review_thread = factory
        AIAgent.run_conversation = observed_run
        self.agent.status_callback = status
        self.agent.background_review_callback = callback
        return self

    def __exit__(self, *_exc):
        self._native.spawn_background_review_thread = self._factory
        self._agent_class.run_conversation = self._run
        self.agent.status_callback = self._status
        self.agent.background_review_callback = self._callback

    def foreground_returned(self, timing: dict[str, Any]) -> None:
        self.foreground_observation = {
            **timing,
            "observed_monotonic": time.perf_counter(),
            "reviews": [{
                "index": index,
                "alive": bool(item.get("_thread") and item["_thread"].is_alive()),
            } for index, item in enumerate(self.records)],
        }

    def join(self, timeout_sec: float) -> None:
        self.join_requested_monotonic = time.perf_counter()
        deadline = self.join_requested_monotonic + timeout_sec
        for record in self.records:
            thread = record.get("_thread")
            if thread is None:
                raise RuntimeError("原生复盘 target 尚未开始，无法取得真实线程。")
            record["join_requested_monotonic"] = time.perf_counter()
            thread.join(max(0.0, deadline - time.perf_counter()))
            record["join_observed_monotonic"] = time.perf_counter()
            record["join_observed_at"] = time.time()
            record["alive_after_join"] = thread.is_alive()
            if thread.is_alive():
                raise TimeoutError(f"后台复盘在 {timeout_sec:g} 秒观察期限内没有结束。")

    def errors(self) -> list[str]:
        errors = [
            item["message"] for item in self.warnings
            if "Auxiliary background review failed:" in item["message"]
        ]
        for record in self.records:
            if record.get("raised_error"):
                errors.append(record["raised_error"])
            result = record.get("result", {})
            if result.get("failed") or result.get("interrupted"):
                errors.append(
                    "后台 run_conversation 返回失败状态："
                    + str(result.get("final_response") or result.get("turn_exit_reason"))
                )
        return list(dict.fromkeys(errors))

    def to_dict(self) -> dict[str, Any]:
        records = []
        for record in self.records:
            data = {key: value for key, value in record.items() if not key.startswith("_")}
            thread = record.get("_thread")
            data["thread"] = None if thread is None else {
                "name": thread.name, "ident": thread.ident,
                "daemon": thread.daemon, "alive": thread.is_alive(),
            }
            if "target_exited_monotonic" in record and "started_monotonic" in record:
                data["review_duration_sec"] = (
                    record["target_exited_monotonic"] - record["started_monotonic"]
                )
            records.append(data)
        return {
            "native_entry": "AIAgent._spawn_background_review → spawn_background_review_thread",
            "timing_note": "target_exited 是原生 target 返回时刻；join_observed 是确认线程终止的上界。",
            "foreground_return": self.foreground_observation,
            "join_requested_monotonic": self.join_requested_monotonic,
            "reviews": records, "actions": self.actions,
            "warnings": self.warnings, "errors": self.errors(),
        }


def _exercise(
    agent: Any, instance: str, prompt: str, second_prompt: str | None,
    evidence_dir: Path, timeout_sec: float,
) -> BackgroundReviewResult:
    evidence_dir = Path(evidence_dir)
    observer = _ReviewObserver(agent)
    result = BackgroundReviewResult(
        # 只有实际时序满足下面的验证条件，才把 blocked_foreground 设为 False。
        session_id=agent.session_id, triggered=False, blocked_foreground=True,
    )
    tasks: list[TaskRun] = []
    timing: list[dict[str, Any]] = []
    validation_error = ""
    injected: dict[str, Any] = {}
    original_runtime = agent._current_main_runtime
    had_override = "_current_main_runtime" in vars(agent)
    previous_override = vars(agent).get("_current_main_runtime")

    def fail_first_review():
        if threading.current_thread().name == "bg-review" and not injected:
            injected.update(at=time.time(), monotonic=time.perf_counter(),
                            thread_ident=threading.get_ident())
            raise RuntimeError("runtime-demo: injected background review fork failure")
        return original_runtime()

    try:
        with observer:
            if second_prompt is not None:
                agent._current_main_runtime = fail_first_review
            tasks.append(run_task(agent, instance, prompt))
            timing.append(dict(agent._runtime_last_timing))
            observer.foreground_returned(timing[0])
            if second_prompt is not None:
                # 连续前台调用在任何后台 join 之前发起。
                tasks.append(run_task(agent, instance, second_prompt))
                timing.append(dict(agent._runtime_last_timing))
            observer.join(timeout_sec)

            assert observer.records, "本轮没有触发 Hermes 原生后台复盘。"
            assert all(task.stop_reason == "final_answer" for task in tasks), \
                "前台任务没有正常完成。"
            assert all(item["_thread"].daemon for item in observer.records), \
                "观察到的复盘线程不是 daemon。"
            first = observer.records[0]
            if second_prompt is None:
                assert observer.foreground_observation["reviews"][0]["alive"], \
                    "前台返回后后台已经结束，本次时序不能证明异步完成。"
                assert first["target_exited_monotonic"] > timing[0]["returned_monotonic"], \
                    "后台没有晚于前台返回。"
                assert not observer.errors(), "后台真实复盘失败，详见 observation.json。"
                assert first.get("result", {}).get("completed") is True, \
                    "后台复盘没有提供正常完成的真实返回值。"
            else:
                assert injected, "预定的后台异常没有发生。"
                assert any(
                    "Auxiliary background review failed:" in item["message"]
                    and "runtime-demo: injected background review fork failure" in item["message"]
                    for item in observer.warnings
                ), "没有捕获原生 _emit_auxiliary_failure 的告警。"
    except BaseException as exc:
        validation_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if had_override:
            agent._current_main_runtime = previous_override
        elif "_current_main_runtime" in vars(agent):
            del agent._current_main_runtime
        result.triggered = bool(observer.records)
        result.actions = observer.actions.copy()
        result.failed = bool(observer.errors())
        result.error = "\n".join(observer.errors())
        first = observer.records[0] if observer.records else {}
        foreground_reviews = observer.foreground_observation.get("reviews", [])
        nonblocking_evidence = {
            "native_daemon": bool(first.get("_thread") and first["_thread"].daemon),
            "required_foregrounds_returned": (
                len(tasks) == (2 if second_prompt is not None else 1)
                and all(task.stop_reason == "final_answer" for task in tasks)
            ),
            "join_requested_after_all_foregrounds": bool(
                timing and observer.join_requested_monotonic is not None
                and observer.join_requested_monotonic >= max(
                    value["returned_monotonic"] for value in timing
                )
            ),
            "first_review_alive_after_foreground_return": bool(
                foreground_reviews and foreground_reviews[0]["alive"]
            ),
            "first_review_target_exited_after_foreground": bool(
                timing and "target_exited_monotonic" in first
                and first["target_exited_monotonic"] > timing[0]["returned_monotonic"]
            ),
        }
        proven = (
            nonblocking_evidence["native_daemon"]
            and nonblocking_evidence["required_foregrounds_returned"]
            and nonblocking_evidence["join_requested_after_all_foregrounds"]
        )
        if second_prompt is None:
            proven = (
                proven
                and nonblocking_evidence["first_review_alive_after_foreground_return"]
                and nonblocking_evidence["first_review_target_exited_after_foreground"]
            )
        nonblocking_evidence["proven"] = proven
        result.blocked_foreground = not proven
        if timing and observer.records:
            baseline = timing[0]["started_monotonic"]
            if "started_monotonic" in first:
                result.started_after_sec = first["started_monotonic"] - baseline
            if "target_exited_monotonic" in first and not first.get("alive_after_join", True):
                # join 可能在第二个前台任务之后才发生；不能把这段等待算进
                # 首个后台耗时。target 退出接近线程结束，仍明确保留上下界。
                result.finished_after_sec = first["target_exited_monotonic"] - baseline
                tasks[0].background_elapsed_sec = result.finished_after_sec
        for index, task in enumerate(tasks):
            filename = "foreground.json" if index == 0 else "foreground-second.json"
            write_json(evidence_dir / filename, task)
        write_json(evidence_dir / "background.json", result)
        write_json(evidence_dir / "observation.json", {
            **observer.to_dict(), "foreground_timings": timing,
            "nonblocking_evidence": nonblocking_evidence,
            "failure_injection": injected, "validation_error": validation_error,
            "accepted": not validation_error,
            "finished_after_sec_basis": "首个原生 target 退出减首个前台开始；是线程完成时刻的近似下界，不含之后才执行的 join 等待。",
        })
    return result


def measure_nonblocking(
    agent: Any, instance: str, prompt: str, *, evidence_dir: Path,
    timeout_sec: float = 240.0,
) -> BackgroundReviewResult:
    """真实运行前台，确认原生后台仍存活，再等待其结束并保存动作。"""
    return _exercise(agent, instance, prompt, None, evidence_dir, timeout_sec)


def isolate_background_failure(
    agent: Any, instance: str, prompt: str, second_prompt: str, *,
    evidence_dir: Path, timeout_sec: float = 240.0,
) -> BackgroundReviewResult:
    """只让首个后台 fork 的运行参数读取失败，立即继续同一前台会话。"""
    return _exercise(agent, instance, prompt, second_prompt, evidence_dir, timeout_sec)
