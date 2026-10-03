#!/usr/bin/env bash
# 第 11 讲练习运行脚本：后台复盘异步不阻塞前台。
#
# 用法：
#   DEEPSEEK_API_KEY=你的DeepSeekkey bash run.sh
#
# 说明：
#   - 跑模型前 unset 所有代理（直连 api.deepseek.com）。
#   - HERMES_HOME 钉在本目录，不碰 ~/.hermes。
#   - 输出同时打印到终端并写入 output/run.log。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC

# 必须 unset 代理，否则模型请求走 Clash 会失败。
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
: "${DEEPSEEK_API_KEY:?需要 export DEEPSEEK_API_KEY（DeepSeek key）}"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"

cd "$HERMES_SRC"
mkdir -p "$HERE/output"

"$HERMES_SRC/.venv/bin/python" "$HERE/run_background_review.py" 2>&1 | tee "$HERE/output/run.log"
