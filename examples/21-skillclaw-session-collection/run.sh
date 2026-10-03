#!/usr/bin/env bash
# 第 21 讲：跨实例会话收集（真实 SkillHub 客户端机制）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC
cd "$REPO_ROOT"

# 模型调用必须直连 DeepSeek，不走 Clash 代理；git/uv/pip 才走代理。
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

# API key 仅从 DEEPSEEK_API_KEY 环境变量读取。
: "${DEEPSEEK_API_KEY:?需要设置 DEEPSEEK_API_KEY}"

exec "$HERMES_SRC/.venv/bin/python" "$HERE/session_collection.py"
