"""后台帧处理：有界队列 + 串行推理 + 广播。

关键点：HTTP 请求只做"收下来 + 入队 + 立即返回 202"，
真正的重计算在后台任务里跑。客户端因此不会被服务端算力限速。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .config import Settings
from .db import Database, now_ns
from .ws import ConnectionManager


class FrameWorker:
    def __init__(self, db: Database, pipeline: Any, hub: ConnectionManager,
                 settings: Settings):
        self.db = db
        self.pipeline = pipeline
        self.hub = hub
        self.settings = settings
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=settings.queue_capacity)
        self.counters = {"queued": 0, "processed": 0, "failed": 0, "dropped": 0}
        self._task: asyncio.Task | None = None
        self._previous_risk: str | None = None
        self.last_processing_ms: float = 0

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="frame-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    @property
    def depth(self) -> int:
        return self.queue.qsize()

    # ---------- 入队 ----------

    async def submit(self, item: dict) -> str:
        """返回 "queued" / "queued_after_drop"。"""
        try:
            self.queue.put_nowait(item)
            self.counters["queued"] += 1
            return "queued"
        except asyncio.QueueFull:
            # 前提是"新帧更有价值"：丢最旧的，并留下审计记录
            try:
                dropped = self.queue.get_nowait()
                self.queue.task_done()
                self.db.mark_frame(dropped["frame_id"], "dropped", "queue overflow")
                self.db.insert_event(now_ns(), dropped["frame_id"], dropped["device_id"],
                                     "WARNING", "QUEUE_OVERFLOW", "服务端队列满，丢弃最旧帧")
                self.counters["dropped"] += 1
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(item)
            self.counters["queued"] += 1
            return "queued_after_drop"

    # ---------- 处理 ----------

    async def _run(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                result = await asyncio.to_thread(
                    self.pipeline.process_bundle,
                    item["metadata"], item["rgb"], item["depth"], item["confidence"])
                self._persist(item, result)
                self.last_processing_ms = result.get("total_ms", 0)
                await self.hub.broadcast(self._message(item, result))
                self.counters["processed"] += 1
                if self.counters["processed"] % 20 == 0:
                    print(f"[worker] 已处理 {self.counters['processed']} 帧 "
                          f"(队列 {self.depth}, 丢弃 {self.counters['dropped']}) "
                          f"单帧 {self.last_processing_ms:.0f}ms", flush=True)
            except Exception as exc:  # noqa: BLE001
                self.counters["failed"] += 1
                self.db.mark_frame(item["frame_id"], "failed", f"{type(exc).__name__}: {exc}")
                self.db.insert_event(now_ns(), item["frame_id"], item["device_id"],
                                     "ERROR", "PIPELINE_FAILED", str(exc))
                print(f"[worker] 处理失败 {item['frame_id'][:8]}: {exc}", flush=True)
            finally:
                self.queue.task_done()

    def _persist(self, item: dict, result: dict) -> None:
        status = result["road_status"]
        perception = result["perception"]
        ts = now_ns()

        self.db.insert_assessment({
            "frame_id": item["frame_id"],
            "device_id": item["device_id"],
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
            "processing_ms": result.get("total_ms", 0.0),
            "json": json.dumps(status, ensure_ascii=False),
        })
        self.db.mark_frame(item["frame_id"], "done")

        risk = status["overall_risk"]
        if risk != self._previous_risk:
            level = {"HIGH": "CRITICAL", "MEDIUM": "WARNING",
                     "UNKNOWN": "WARNING", "LOW": "INFO", "SAFE": "INFO"}.get(risk, "INFO")
            self.db.insert_event(ts, item["frame_id"], item["device_id"], level,
                                 "RISK_CHANGED",
                                 f"{self._previous_risk or 'NONE'} -> {risk}")
            self._previous_risk = risk

    def _message(self, item: dict, result: dict) -> dict:
        status = result["road_status"]
        perception = result["perception"]
        return {
            "type": "assessment",
            "frame_id": item["frame_id"],
            "device_id": item["device_id"],
            "seq": item["metadata"].get("seq"),
            "ts_ns": time.time_ns(),
            "overall_risk": status["overall_risk"],
            "system_status": status["system_status"],
            "passability": status.get("passability", {}),
            "alternative": status.get("alternative_path", {}).get("status"),
            "road_status": perception.get("road_mask_status"),
            "road_coverage": perception.get("road", {}).get("coverage_ratio"),
            "rock_count": len(perception.get("rockfalls", [])),
            "processing_ms": result.get("total_ms", 0.0),
            "dropped_since_last": item.get("dropped_since_last", 0),
            "reasons": [r.get("code") for r in status.get("reasons", [])][:5],
        }
