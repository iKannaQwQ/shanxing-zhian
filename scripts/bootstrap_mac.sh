#!/usr/bin/env bash
# macOS(Apple Silicon) 开发环境引导。幂等：可重复执行。
# 说明：工程内自包含安装，不写 ~/.local、不用 sudo。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NODE_VERSION="v24.22.2"          # LTS
NODE_ARCH="darwin-arm64"
PY_VERSION="3.11"

echo "==> 工程根目录: $ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "!! 未找到 uv。请先安装 (https://docs.astral.sh/uv/) 后重试。" >&2
  exit 1
fi

mkdir -p .tools

# --- Node.js（前端需要）---
if [ ! -x ".tools/node/bin/node" ]; then
  echo "==> 安装 Node $NODE_VERSION ($NODE_ARCH)"
  tmp=".tools/node-download.tar.gz"
  curl -fsSL --retry 3 -o "$tmp" \
    "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-${NODE_ARCH}.tar.gz"
  tar xzf "$tmp" -C .tools
  rm -f "$tmp"
  mv ".tools/node-${NODE_VERSION}-${NODE_ARCH}" .tools/node
else
  echo "==> Node 已存在: $(.tools/node/bin/node --version)"
fi

# --- Python 工具链 venv（cmake / jsonschema / pytest）---
export UV_CACHE_DIR="$ROOT/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"

if [ ! -x ".tools/venv/bin/python" ]; then
  echo "==> 创建 Python $PY_VERSION 工具环境"
  uv venv --python "$PY_VERSION" .tools/venv
fi

echo "==> 安装构建/校验工具"
uv pip install --python .tools/venv/bin/python cmake jsonschema pytest >/dev/null
.tools/venv/bin/cmake --version | head -1

# --- 自检 ---
echo "==> 环境自检"
.tools/venv/bin/python scripts/check_env.py || true

echo
echo "完成。下一步:"
echo "  source scripts/env.sh"
echo "  python contracts/validate.py --all"
