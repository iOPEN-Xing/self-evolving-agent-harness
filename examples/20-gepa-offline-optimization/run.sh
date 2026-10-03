#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HERMES_SRC="${HERMES_SRC:-$REPO_ROOT/.deps/hermes-agent}"
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
: "${DEEPSEEK_API_KEY:?需要设置 DEEPSEEK_API_KEY}"
"$HERMES_SRC/.venv/bin/python" -u "$HERE/gepa_offline.py"
