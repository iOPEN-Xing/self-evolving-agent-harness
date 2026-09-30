#!/usr/bin/env bash
# 为第 11–23 讲及综合实践获取上游源码，并准备共用的 Hermes Python 环境。
# 用法：bash scripts/setup_deps.sh
#       PROXY=http://127.0.0.1:7890 bash scripts/setup_deps.sh
#       PROXY= bash scripts/setup_deps.sh  # 直连
# 需要 Git，以及 uv 或 Python 3.12；uv 会按需下载 Python 3.12。
# 已有源码目录不会被覆盖或自动更新；可自行进入目录执行 git pull。
# 本脚本不需要 API key，也不会读取或写入任何 API key 配置。
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$HERE/.." && pwd)"
DEPS_DIR="$REPO_ROOT/.deps"
PROXY="${PROXY-}"

# 同时覆盖大小写变量，避免继承的 SOCKS 代理或 NO_PROXY 绕过本次设置。
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
export PIP_PROXY="$PROXY"
if [[ -n "$PROXY" ]]; then
  export HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
  export http_proxy="$PROXY" https_proxy="$PROXY"
  echo "联网步骤使用 PROXY 指定的代理；不设置 PROXY 时默认直连。"
else
  echo "联网步骤使用直连。"
fi

if ! command -v git >/dev/null 2>&1; then
  echo "错误：请先安装 Git。" >&2
  exit 1
fi

mkdir -p "$DEPS_DIR"
clone_source() {
  local name="$1" url="$2"
  if [[ -e "$DEPS_DIR/$name" ]]; then
    echo "已存在 .deps/${name}，跳过 clone；如需更新，请自行在该目录执行 git pull。"
  else
    # 显式覆盖 Git 自身的代理设置，确保 PROXY= 也能直连。
    git -c http.proxy="$PROXY" clone --depth 1 "$url" "$DEPS_DIR/$name"
  fi
}

clone_source hermes-agent https://github.com/NousResearch/hermes-agent.git
clone_source hermes-agent-self-evolution https://github.com/NousResearch/hermes-agent-self-evolution.git
clone_source SkillClaw https://github.com/AMAP-ML/SkillClaw.git
clone_source OpenViking https://github.com/volcengine/OpenViking.git

HERMES_SRC="$DEPS_DIR/hermes-agent"
VENV="$HERMES_SRC/.venv"
PYTHON="$VENV/bin/python"
if [[ ! -d "$VENV" ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$VENV"
  else
    SYSTEM_PYTHON=""
    for candidate in python3.12 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && \
        "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
        SYSTEM_PYTHON="$candidate"
        break
      fi
    done
    if [[ -z "$SYSTEM_PYTHON" ]]; then
      echo "错误：本仓库统一使用 Python 3.12；请先安装 uv 或兼容的 Python，再重新运行。" >&2
      exit 1
    fi
    "$SYSTEM_PYTHON" -m venv "$VENV"
  fi
else
  echo "复用 .deps/hermes-agent/.venv。"
fi

if [[ ! -x "$PYTHON" ]] || ! "$PYTHON" -c \
  'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  echo "错误：已有 Hermes .venv 不可用，或 Python 不是 3.12；请检查后重新创建。" >&2
  exit 1
fi

# Hermes pyproject.toml 声明核心依赖，上游 setup-hermes.sh 支持 editable 安装。
# 练习使用核心包，不安装开发、桌面、语音等扩展。
# 第 21–23 讲还需 SkillClaw 的本地服务，依赖取自其 pyproject.toml 的 server extra。
# 一次解析两个项目的依赖，避免后装项目静默破坏前者的版本要求。
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PYTHON" -e "$HERMES_SRC" -e "$DEPS_DIR/SkillClaw[server]"
else
  # uv 创建的 venv 默认不带 pip；后续改用无 uv 的环境时也能继续安装。
  if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    "$PYTHON" -m ensurepip --upgrade
  fi
  "$PYTHON" -m pip --proxy "$PROXY" install -e "$HERMES_SRC" -e "$DEPS_DIR/SkillClaw[server]"
fi

echo "准备完成：四个上游源码已就绪，共用解释器为 .deps/hermes-agent/.venv/bin/python。"
echo "运行练习前，请从环境变量提供 GLM_API_KEY（兼容 BIGMODEL_API_KEY），然后执行相应目录的 bash run.sh。"
