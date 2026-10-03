#!/usr/bin/env bash
# 第 22 讲：共享修订在第三处生效（真实 evolve_server 多服务）
# 前置：先跑 examples/21-skillclaw-session-collection/run.sh 收集会话。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC
cd "$REPO_ROOT"

unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

: "${DEEPSEEK_API_KEY:?需要设置 DEEPSEEK_API_KEY}"

# 独立 HERMES_HOME 第三实例的 hermes 二进制（可被环境覆盖）
export HERMES_BIN="${HERMES_BIN:-$(command -v hermes || echo hermes)}"

exec "$HERMES_SRC/.venv/bin/python" "$HERE/shared_revision.py"
