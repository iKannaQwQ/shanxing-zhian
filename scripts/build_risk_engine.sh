#!/usr/bin/env bash
# 构建并测试 C++ 风险引擎（macOS / Linux）。
# 用法: bash scripts/build_risk_engine.sh [Release|Debug]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_TYPE="${1:-Release}"

# 工程内自带 cmake；若不存在则回退到 PATH
CMAKE="$ROOT/.tools/venv/bin/cmake"
if [ ! -x "$CMAKE" ]; then
  CMAKE="$(command -v cmake || true)"
fi
if [ -z "$CMAKE" ]; then
  echo "未找到 cmake，请先运行 bash scripts/bootstrap_mac.sh" >&2
  exit 1
fi

cd "$ROOT/risk_engine"
"$CMAKE" -S . -B build -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
"$CMAKE" --build build -j "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

echo
echo "== 单元测试 =="
./build/rock_risk_tests

echo
echo "== 契约校验 =="
"$ROOT/.tools/venv/bin/python" "$ROOT/contracts/validate.py" --all
