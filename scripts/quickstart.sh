#!/usr/bin/env bash
# 一键快速启动（macOS / Linux 开发机）
#
# 前置: perception/.venv + .tools（Node/CMake）+ 风险引擎 + 前端 dist
# 默认 --demo：合成演示，不需要模型权重，独立端口 8001，不碰真实数据库
#     --full：真实系统，后端 8000 + AI worker（需要 perception/weights 里的道路权重）
#     --install：首次运行，创建 perception/.venv 并安装依赖
#     --rebuild：强制重建风险引擎/前端/演示数据
#
# 用法:
#   bash scripts/quickstart.sh --install --demo
#   bash scripts/quickstart.sh --demo
#   bash scripts/quickstart.sh --full
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="demo"
INSTALL=0
REBUILD=0
PORT=""

usage() {
  cat <<'EOF'
用法: bash scripts/quickstart.sh [--demo|--full] [--install] [--rebuild] [--port N]

  --demo      合成演示（默认，端口 8001，不需要模型权重）
  --full      完整系统（端口 8000，需要 perception/weights 里的道路权重）
  --install   首次运行：创建 perception/.venv 并安装依赖
  --rebuild   强制重建风险引擎、前端和演示数据
  --port N    指定端口
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --demo) MODE="demo" ;;
    --full) MODE="full" ;;
    --install) INSTALL=1 ;;
    --rebuild) REBUILD=1 ;;
    --port) PORT="${2:?--port 需要参数}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [ -z "$PORT" ]; then
  if [ "$MODE" = "demo" ]; then PORT=8001; else PORT="${ROAD_PORT:-8000}"; fi
fi

say() { printf '%s\n' "$*"; }
die() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

check_port() {
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    say "端口 $PORT 已被占用："
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | sed 's/^/  /' || true
    die "换一个端口：bash scripts/quickstart.sh --$MODE --port 8002"
  fi
}

# ---------- 1. 工程内工具链 ----------
if [ ! -x .tools/venv/bin/python ] || [ ! -x .tools/venv/bin/cmake ] \
   || [ ! -x .tools/node/bin/node ]; then
  have uv || die "缺少 uv。先安装：https://docs.astral.sh/uv/"
  say "== 工具链不完整，运行 bootstrap_mac.sh =="
  bash scripts/bootstrap_mac.sh
fi

# ---------- 2. Python 运行环境 ----------
py_ok() {
  perception/.venv/bin/python -c \
    "import numpy, cv2, fastapi, uvicorn, jsonschema, PIL" >/dev/null 2>&1
}

if ! py_ok; then
  if [ "$INSTALL" -eq 1 ]; then
    have uv || die "缺少 uv。先安装：https://docs.astral.sh/uv/"
    say "== 创建 perception/.venv 并安装依赖（首次可能 5–15 分钟）=="
    [ -x perception/.venv/bin/python ] || \
      uv venv --python 3.11 perception/.venv
    uv pip install --python perception/.venv/bin/python \
      -r requirements-backend.txt \
      -r requirements-ai-worker.txt \
      -r requirements-dev.txt
  else
    die "缺少 perception/.venv 或依赖。首次运行：bash scripts/quickstart.sh --install --$MODE"
  fi
elif [ "$INSTALL" -eq 1 ]; then
  have uv || die "缺少 uv。先安装：https://docs.astral.sh/uv/"
  say "== 同步 Python 依赖 =="
  uv pip install --python perception/.venv/bin/python \
    -r requirements-backend.txt \
    -r requirements-ai-worker.txt \
    -r requirements-dev.txt >/dev/null
fi

# ---------- 3. 风险引擎 ----------
if [ ! -x risk_engine/build/rock_risk_cli ] || [ "$REBUILD" -eq 1 ]; then
  say "== 构建 C++ 风险引擎 =="
  bash scripts/build_risk_engine.sh
fi

# ---------- 4. 前端 ----------
export PATH="$ROOT/.tools/node/bin:$PATH"
if [ ! -d frontend/node_modules ]; then
  say "== 安装前端依赖 =="
  (cd frontend && npm install)
fi
if [ ! -f frontend/dist/index.html ] || [ "$REBUILD" -eq 1 ]; then
  say "== 构建前端 =="
  (cd frontend && npm run build)
fi

# ---------- 5. 启动 ----------
if [ "$MODE" = "demo" ]; then
  if [ ! -f data/demo_sandbox/demo.db ] || [ "$REBUILD" -eq 1 ]; then
    say "== 生成合成演示数据 =="
    perception/.venv/bin/python scripts/make_demo_sandbox.py
  fi
  check_port
  say ""
  say "演示环境（合成数据，页面有 DEMO 标识）"
  say "  用户端    http://127.0.0.1:$PORT/"
  say "  管理后台  http://127.0.0.1:$PORT/#/admin"
  say "  Ctrl-C 停止"
  say ""
  exec env \
    ROAD_DB="$ROOT/data/demo_sandbox/demo.db" \
    ROAD_ARTIFACTS_DIR="$ROOT/data/demo_sandbox/artifacts" \
    ROAD_PORT="$PORT" \
    ROAD_STALE_AFTER_S=86400 \
    ROAD_DEVICE_OFFLINE_S=86400 \
    ROAD_ARTIFACT_HOURS=72 \
    ROAD_RETENTION_HOURS=72 \
    perception/.venv/bin/python -m uvicorn backend.app.main:app \
      --host 0.0.0.0 --port "$PORT"
fi

# ---- full ----
WEIGHT="${ROAD_ROAD_MODEL:-perception/weights/aeroscapes_unet_512_bs8_expE.pt}"
if [ ! -f "$WEIGHT" ]; then
  die "缺少道路权重：$WEIGHT
  仓库不包含权重；把 .pt 放到 perception/weights/，或设置 ROAD_ROAD_MODEL=...。
  只想看演示请用：bash scripts/quickstart.sh --demo"
fi
say ""
say "完整系统（后端 + AI worker）"
say "  用户端    http://<本机局域网 IP>:$PORT/"
say "  管理后台  http://<本机局域网 IP>:$PORT/#/admin"
say ""
ROAD_PORT="$PORT" bash scripts/start_all.sh
say ""
say "查看状态: bash scripts/status.sh"
say "停止    : bash scripts/stop_all.sh"
