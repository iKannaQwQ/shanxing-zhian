#!/usr/bin/env bash
# 一键停止：先停 worker，再停后端，最后确认端口已释放
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/data/run"
PORT="${ROAD_PORT:-8000}"

say() { printf '%s\n' "$*"; }

stop_one() {
  local name="$1" pidfile="$2"
  if [ -f "$pidfile" ]; then
    local pid; pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      say "  停止 $name (pid $pid)…"
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      say "  $name 已停止"
    else
      say "  $name 未在运行（清理残留 pid 文件）"
    fi
    rm -f "$pidfile"
  else
    say "  $name 未启动"
  fi
}

say "== 停止服务 =="
# 顺序很重要：先停 worker，避免它在后端关闭后继续回传
stop_one "AI worker" "$RUN/ai_worker.pid"
stop_one "后端" "$RUN/backend.pid"

# 兜底：清掉可能残留的同名进程
pkill -f "scripts/ai_worker.py" 2>/dev/null
pkill -f "uvicorn backend.app.main" 2>/dev/null
sleep 1

say ""
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  say "[注意] 端口 $PORT 仍被占用："
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | sed 's/^/  /'
  say "  可用 kill -9 <PID> 强制结束"
else
  say "端口 $PORT 已释放"
fi

say ""
say "手机端请同时点击 App 内的「停止」，否则它会继续排队上传。"
