"""真实 Hermes 前台与原生异步复盘；不同 HERMES_HOME 使用独立进程。"""

from .foreground import make_agent, run_task

__all__ = ["make_agent", "run_task"]
