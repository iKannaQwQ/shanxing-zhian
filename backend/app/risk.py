"""风险判定：调用 C++ 引擎并把结果落库。

界限很清楚：
  * **AI 推理由外部 worker 完成**，后端不加载任何模型；
  * 风险引擎是确定性规则计算（几毫秒），留在后端进程内是合理的——
    它不是"AI 推理"，而是判定逻辑，属于后端的职责。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .db import Database, now_ns

ROOT = Path(__file__).resolve().parents[2]
ENGINE_BIN = ROOT / "risk_engine" / "build" / "rock_risk_cli"


class RiskEngine:
    """常驻 C++ 引擎（--stream），每行一帧。"""

    def __init__(self, binary: Path = ENGINE_BIN):
        self.binary = Path(binary)
        if not self.binary.exists():
            raise FileNotFoundError(
                f"未找到风险引擎 {self.binary}，请先运行 scripts/build_risk_engine.sh")
        self._process = subprocess.Popen([str(self.binary), "--stream"],
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         text=True, bufsize=1)

    def assess(self, report: dict[str, Any]) -> dict[str, Any]:
        assert self._process.stdin and self._process.stdout
        self._process.stdin.write(json.dumps(report, ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("风险引擎进程意外退出")
        return json.loads(line)

    def close(self) -> None:
        try:
            if self._process.stdin:
                self._process.stdin.close()
            self._process.terminate()
        except Exception:  # noqa: BLE001
            pass


def assess_and_store(db: Database, engine: RiskEngine, frame_id: str,
                     device_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """跑判定、落库、必要时写风险变化事件。返回 RoadStatus。"""
    started = time.perf_counter()
    status = engine.assess(report)
    engine_ms = (time.perf_counter() - started) * 1000

    previous = db.latest_assessment()
    previous_risk = previous["overall_risk"] if previous else None

    perception = report
    ts = now_ns()
    db.insert_assessment({
        "frame_id": frame_id,
        "device_id": device_id,
        "ts_assessed_ns": ts,
        "overall_risk": status["overall_risk"],
        "system_status": status["system_status"],
        "a_to_b": status.get("passability", {}).get("A_TO_B"),
        "b_to_a": status.get("passability", {}).get("B_TO_A"),
        "alternative_status": status.get("alternative_path", {}).get("status"),
        "road_detected": 1 if perception.get("road_detected") else 0,
        "road_status": perception.get("road_mask_status"),
        "road_coverage": perception.get("road", {}).get("coverage_ratio", 0.0),
        "rock_count": len(perception.get("rockfalls", [])),
        "processing_ms": round(engine_ms, 2),
        "source": report.get("source", "LIVE"),
        "json": json.dumps(status, ensure_ascii=False),
    })
    db.mark_frame(frame_id, "processed")

    # 出现落石即登记一条事件，供事件管理页跟进处理状态
    rock_count = len(perception.get("rockfalls", []))
    if rock_count > 0:
        db.insert_rock_event(ts, frame_id, device_id, status["overall_risk"], rock_count)

    risk = status["overall_risk"]
    if risk != previous_risk:
        level = {"HIGH": "CRITICAL", "MEDIUM": "WARNING",
                 "UNKNOWN": "WARNING", "LOW": "INFO", "SAFE": "INFO"}.get(risk, "INFO")
        db.insert_event(ts, frame_id, device_id, level, "RISK_CHANGED",
                        f"{previous_risk or 'NONE'} -> {risk}")
    return status


def perception_to_message(frame: dict, report: dict, status: dict) -> dict:
    """WebSocket 推送用的精简消息。"""
    return {
        "type": "assessment",
        "frame_id": frame["frame_id"],
        "device_id": frame["device_id"],
        "seq": frame.get("seq"),
        "ts_ns": now_ns(),
        "overall_risk": status["overall_risk"],
        "system_status": status["system_status"],
        "passability": status.get("passability", {}),
        "alternative": status.get("alternative_path", {}).get("status"),
        "road_status": report.get("road_mask_status"),
        "road_coverage": report.get("road", {}).get("coverage_ratio"),
        "rock_count": len(report.get("rockfalls", [])),
        "processing_ms": status.get("engine_ms", 0.0),
        "dropped_since_last": frame.get("dropped_since_last", 0),
        "reasons": [r.get("code") for r in status.get("reasons", [])][:5],
    }
