#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
export HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
: "${GLM_API_KEY:-${BIGMODEL_API_KEY:?需要设置 GLM_API_KEY 或 BIGMODEL_API_KEY}}"
cd "$HERMES_SRC"
"$HERMES_SRC/.venv/bin/python" -u "$HERE/run_lab15.py"
