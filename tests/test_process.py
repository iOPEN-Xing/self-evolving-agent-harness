import subprocess
import sys
import time

import pytest

from harness_engineering.process import run_bounded


def test_success_and_failure_preserve_real_exit_codes():
    value = run_bounded([sys.executable, "-c", "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)"], timeout=3)
    assert value.returncode == 7
    assert value.stdout.strip() == "out"
    assert value.stderr.strip() == "err"


def test_timeout_cancels_uncooperative_grandchild_and_keeps_partial_output(tmp_path):
    marker, ready = tmp_path / "late-write", tmp_path / "ready"
    child = ("import signal,time; from pathlib import Path; "
             "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"Path({str(ready)!r}).write_text('ready'); time.sleep(1.5); "
             f"Path({str(marker)!r}).write_text('leaked')")
    parent = (f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
              "print('started',flush=True); time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_bounded([sys.executable, "-c", parent], timeout=0.5, terminate_grace=0.1)
    assert ready.exists()
    assert "started" in caught.value.stdout
    assert time.monotonic() - started < 3
    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan")])
def test_invalid_timeout_rejected(timeout):
    with pytest.raises(ValueError):
        run_bounded([sys.executable, "-c", "pass"], timeout=timeout)
