#!/usr/bin/env bash
# 启动独立 AI worker（与后端进程分离）。后端需先启动。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/perception/.venv/bin/python"
[ -x "$PY" ] || { echo "缺少 Python 环境，请先运行 scripts/bootstrap_mac.sh" >&2; exit 1; }
cd "$ROOT"
exec "$PY" scripts/ai_worker.py --backend "${ROAD_BACKEND:-http://127.0.0.1:8000}" "$@"
