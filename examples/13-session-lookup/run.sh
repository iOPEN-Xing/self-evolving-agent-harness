#!/usr/bin/env bash
# 第 13 讲练习运行脚本：Session 回查（state.db 里的原始对话与 MEMORY.md）。
#
# 用法：
#   GLM_API_KEY=你的智谱key bash run.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
export HERMES_SRC

unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY

export GLM_API_KEY="${GLM_API_KEY:-${BIGMODEL_API_KEY:-}}"
: "${GLM_API_KEY:?需要 export GLM_API_KEY（智谱 key）}"
export GLM_BASE_URL="${GLM_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"

if [ ! -x "$HERMES_SRC/.venv/bin/python" ]; then
  echo "缺少 Hermes 运行环境，请先在仓库根目录执行 bash scripts/setup_deps.sh" >&2
  exit 1
fi

cd "$HERMES_SRC"
mkdir -p "$HERE/output"

"$HERMES_SRC/.venv/bin/python" "$HERE/run_session_lookup.py" 2>&1 | tee "$HERE/output/run.log"
