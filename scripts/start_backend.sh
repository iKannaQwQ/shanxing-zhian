#!/usr/bin/env bash
# 启动正式后端（M6）。所有配置走环境变量，见 docs/07-backend.md。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/perception/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "缺少 Python 环境，请先运行 scripts/bootstrap_mac.sh" >&2
  exit 1
fi
if [ ! -x "$ROOT/risk_engine/build/rock_risk_cli" ]; then
  echo "缺少风险引擎，请先运行 scripts/build_risk_engine.sh" >&2
  exit 1
fi

cd "$ROOT"
exec "$PY" -m uvicorn backend.app.main:app \
  --host "${ROAD_HOST:-0.0.0.0}" \
  --port "${ROAD_PORT:-8000}"
