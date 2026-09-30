"""统一配置：路径、模型、密钥与总装开关。

密钥只从环境变量读取（GLM_API_KEY / BIGMODEL_API_KEY），不写入任何文件。
真实调用模型默认直连（脚本会清除代理）；只有 clone 上游、安装依赖时才走 Clash。
"""
from __future__ import annotations

import os
from pathlib import Path

# examples/assembly/assembly/config.py
# parents[0]=assembly(pkg) [1]=assembly [2]=examples [3]=repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
DEPS = REPO_ROOT / ".deps"
HERMES_SRC = DEPS / "hermes-agent"
SKILLCLAW_SRC = DEPS / "SkillClaw"
OPENVIKING_SRC = DEPS / "OpenViking"

MODEL = os.environ.get("ASSEMBLY_MODEL", "glm-5.2")
PROVIDER = "glm"
BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

ASSEMBLY_DIR = Path(__file__).resolve().parents[1]  # examples/assembly
OUTPUT_DIR = ASSEMBLY_DIR / "output"
SCENARIOS_DIR = ASSEMBLY_DIR / "scenarios"

# SkillClaw evolve_server 默认监听地址
EVOLVE_SERVER_HOST = os.environ.get("EVOLVE_SERVER_HOST", "127.0.0.1")
EVOLVE_SERVER_PORT = int(os.environ.get("EVOLVE_SERVER_PORT", "8193"))
EVOLVE_SERVER_ENDPOINT = f"http://{EVOLVE_SERVER_HOST}:{EVOLVE_SERVER_PORT}"

SKILL_NAME = "payment-status-investigation"


def load_env_file() -> None:
    """环境变量缺 GLM_API_KEY 时，从 ~/.hermes/.env 加载（只进当前进程）。"""
    if os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY"):
        return
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def clear_proxy_for_model() -> None:
    """真实调用模型默认直连；确需代理时在外部重新 export。"""
    for name in ("http_proxy", "https_proxy", "all_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(name, None)


def api_key() -> str:
    load_env_file()
    key = os.environ.get("GLM_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or ""
    if not key:
        raise RuntimeError("缺少 GLM_API_KEY（或 BIGMODEL_API_KEY），请先在环境变量配置")
    return key


def ensure_paths() -> None:
    missing = [p for p in (HERMES_SRC, SKILLCLAW_SRC) if not p.exists()]
    if missing:
        raise RuntimeError(
            "缺少上游依赖：" + ", ".join(str(p) for p in missing)
            + "；请先在仓库根运行 bash scripts/setup_deps.sh")
