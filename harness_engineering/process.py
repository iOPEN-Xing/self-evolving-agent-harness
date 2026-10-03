"""POSIX 作业时限：取消整组子进程，保留 stdout/stderr 供调用方脱敏。"""
import math
import os
import signal
import subprocess


def _kill_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def run_bounded(command, *, timeout, cwd=None, env=None, terminate_grace=1):
    """禁止 shell；只用于会结束的作业，不用于启动常驻共享服务。"""
    if os.name != "posix":
        raise RuntimeError("进程组取消当前仅支持 macOS/Linux")
    if isinstance(command, str) or not command:
        raise ValueError("command 必须是非空参数列表")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
           for v in (timeout, terminate_grace)):
        raise ValueError("时限必须是有限正数")
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        _kill_group(process, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            _kill_group(process, signal.SIGKILL)
            stdout, stderr = process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
        raise
    finally:
        # 父进程退出不等于孙进程退出；清除本作业留下的后台子进程。
        _kill_group(process, signal.SIGKILL)
        process.wait()
