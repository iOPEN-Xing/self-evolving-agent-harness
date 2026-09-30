#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
export HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
case "${1:-lifecycle}" in
  lifecycle) SCRIPT=run_lab17.py ;;
  consolidate)
    : "${GLM_API_KEY:-${BIGMODEL_API_KEY:?需要设置 GLM_API_KEY 或 BIGMODEL_API_KEY}}"
    SCRIPT=run_lab17_consolidate.py ;;
  *) printf '用法：bash run.sh lifecycle 或 bash run.sh consolidate\n' >&2; exit 2 ;;
esac
cd "$HERMES_SRC"
"$HERMES_SRC/.venv/bin/python" -u "$HERE/$SCRIPT"
