#!/usr/bin/env bash
# 一张纸看清当前状态
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ROAD_PORT:-8000}"
BASE="http://127.0.0.1:$PORT"
PY="$ROOT/perception/.venv/bin/python"

if ! curl -sf -m 2 "$BASE/api/v1/health" >/dev/null 2>&1; then
  echo "后端未运行（$BASE/api/v1/health 不可达）"
  echo "启动: bash scripts/start_all.sh"
  exit 1
fi

curl -sS -m 5 "$BASE/api/v1/status" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
s = d.get("system", {})
print("后端      : 正常")
print("AI worker :", s.get("ai_worker"), "| 最近结果", s.get("ai_last_result_ms"), "ms")
print("待处理帧  :", s.get("pending_frames"), "| 失败/过期:", s.get("frames_failed"))
print("在线设备  :", s.get("devices_online"), "/", s.get("devices_total"))
print("最新判定  :", (d.get("latest") or {}).get("overall_risk"), "| 年龄", d.get("age_ms"), "ms",
      "| 过期" if d.get("stale") else "| 新鲜")
print("累计判定  :", d["counters"]["processed"], "帧")
'
