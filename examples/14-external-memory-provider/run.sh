#!/usr/bin/env bash
# 第 14 讲练习运行脚本：外部记忆 Provider（生命周期 / 边界 / 故障隔离）。
#
# 用法：
#   GLM_API_KEY=你的智谱key bash run.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC

unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

export GLM_API_KEY="${GLM_API_KEY:-${BIGMODEL_API_KEY:-}}"
: "${GLM_API_KEY:?需要 export GLM_API_KEY（智谱 key）}"
export GLM_BASE_URL="${GLM_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"

cd "$HERMES_SRC"
mkdir -p "$HERE/output"
RUN_OUTPUT_DIR="$(mktemp -d "$HERE/output/run-XXXXXXXX")"
export RUN_OUTPUT_DIR
export HERMES_HOME="$RUN_OUTPUT_DIR/hermes-home"
printf '%s\n' "$RUN_OUTPUT_DIR" > "$HERE/output/latest-run.txt"

"$HERMES_SRC/.venv/bin/python" -u "$HERE/run_provider.py" 2>&1 | tee "$RUN_OUTPUT_DIR/run.log"
