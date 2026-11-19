#!/usr/bin/env bash
# 一键启动：检查环境 → 起后端 → 等就绪 → 起 AI worker → 打印访问地址
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/perception/.venv/bin/python"
RUN="$ROOT/data/run"
LOGS="$ROOT/data/logs"
PORT="${ROAD_PORT:-8000}"
BASE="http://127.0.0.1:$PORT"

# 可通行区 / 障碍来源（与 scripts/env.sh 保持一致，说明见 docs/22 §9）
# plane = 深度平面内点（固定俯视的桌面/沙盘演示）
# road_model = U-Net 道路分割（真实道路，或沙盘里需要区分 5 cm 道路时）
: "${ROAD_ROAD_SOURCE:=plane}"
: "${ROAD_OBSTACLE_MODE:=geometric}"
export ROAD_ROAD_SOURCE ROAD_OBSTACLE_MODE

mkdir -p "$RUN" "$LOGS"

say() { printf '%s\n' "$*"; }
fail() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }

say "== 1/4 检查环境 =="
[ -x "$PY" ] || fail "缺少 Python 环境。先运行: bash scripts/bootstrap_mac.sh"
say "  Python: $($PY --version 2>&1)"

if [ ! -x "$ROOT/risk_engine/build/rock_risk_cli" ]; then
  say "  风险引擎未编译，开始编译…"
  bash "$ROOT/scripts/build_risk_engine.sh" >"$LOGS/build_engine.log" 2>&1 \
    || fail "风险引擎编译失败，详见 $LOGS/build_engine.log"
fi
say "  风险引擎: 就绪"

if [ -f "$RUN/backend.pid" ] && kill -0 "$(cat "$RUN/backend.pid")" 2>/dev/null; then
  say "  后端已在运行 (pid $(cat "$RUN/backend.pid"))"
else
  say "== 2/4 启动后端 =="
  nohup "$PY" -m uvicorn backend.app.main:app --host 0.0.0.0 --port "$PORT" \
    >>"$LOGS/backend.log" 2>&1 &
  echo $! >"$RUN/backend.pid"
  say "  已启动 (pid $(cat "$RUN/backend.pid"))，等待就绪…"
fi

for _ in $(seq 1 60); do
  if curl -sf -m 2 "$BASE/api/v1/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -sf -m 2 "$BASE/api/v1/health" >/dev/null 2>&1 \
  || fail "后端 60 秒内未就绪，查看 $LOGS/backend.log"
say "  后端就绪: $BASE"

if [ -f "$RUN/ai_worker.pid" ] && kill -0 "$(cat "$RUN/ai_worker.pid")" 2>/dev/null; then
  say "  AI worker 已在运行 (pid $(cat "$RUN/ai_worker.pid"))"
else
  say "== 3/4 启动 AI worker =="
  say "  模型加载约需 10–20 秒，请稍候"
  nohup "$PY" "$ROOT/scripts/ai_worker.py" --backend "$BASE" \
    >>"$LOGS/ai_worker.log" 2>&1 &
  echo $! >"$RUN/ai_worker.pid"
  say "  已启动 (pid $(cat "$RUN/ai_worker.pid"))"
fi

say "== 4/4 完成 =="
IP="$(ipconfig getifaddr en0 2>/dev/null || echo 127.0.0.1)"
say ""
say "  用户端    http://$IP:$PORT/"
say "  管理后台  http://$IP:$PORT/#/admin"
say ""
say "  检查状态  bash scripts/status.sh"
say "  停止全部  bash scripts/stop_all.sh"
