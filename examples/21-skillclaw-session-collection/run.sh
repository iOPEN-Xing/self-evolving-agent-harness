#!/usr/bin/env bash
# 第 21 讲：跨实例会话收集（真实 SkillHub 客户端机制）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC
cd "$REPO_ROOT"

# 模型调用必须直连 GLM，不走 Clash 代理；git/uv/pip 才走代理。
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

# API key 仅从 GLM_API_KEY 或 BIGMODEL_API_KEY 环境变量读取。
if [ -z "${GLM_API_KEY:-}" ] && [ -n "${BIGMODEL_API_KEY:-}" ]; then
  export GLM_API_KEY="$BIGMODEL_API_KEY"
fi

exec "$HERMES_SRC/.venv/bin/python" "$HERE/session_collection.py"
