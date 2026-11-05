#!/usr/bin/env bash
# 用法: source scripts/env.sh
# 把所有工具路径收拢到工程内，避免污染系统环境（沙箱下也无需 sudo）。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export PROJECT_ROOT="$ROOT"

export PATH="$ROOT/.tools/node/bin:$ROOT/.tools/venv/bin:$PATH"

# uv 的一切写入都留在工程内，保证可复现、可删除
export UV_CACHE_DIR="$ROOT/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"

# MPS 缺失算子回退到 CPU（只在本地调试用，性能会掉）
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

echo "PROJECT_ROOT=$PROJECT_ROOT"
echo "python: $(command -v python || echo '未激活')"
echo "node  : $(command -v node || echo '缺失')"
echo "cmake : $(command -v cmake || echo '缺失')"
