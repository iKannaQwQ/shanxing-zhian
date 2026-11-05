"""FastAPI 应用：接收、后台处理、查询、WebSocket 推送。

设计要点见 docs/07-backend.md：
  * 上传立即返回 202，处理与传输解耦；
  * 有界队列，满了丢最旧并留审计记录；
  * SQLite 持久化，按保留窗口自动清理。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import Body, FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .db import Database, now_ns
from .worker import FrameWorker
from .ws import ConnectionManager


GAP_EVENT_INTERVAL_NS = 10 * 1_000_000_000


async def _cleanup_loop(db: Database, settings: Settings) -> None:
    """按保留窗口定期清理，避免 SQLite 无限增长。"""
    while True:
        await asyncio.sleep(settings.cleanup_interval_s)
        cutoff = now_ns() - int(settings.retention_hours * 3600 * 1e9)
        removed = db.purge_older_than(cutoff)
        if any(removed.values()):
            print(f"[retention] 清理 {removed}", flush=True)


def create_app(settings: Settings | None = None,
               pipeline_factory: Callable[[Settings], Any] | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)

        factory = pipeline_factory
        if factory is None:
            from .pipeline import InferencePipeline

            def factory(s: Settings) -> Any:
                return InferencePipeline(device=s.device, rock_weights=s.rock_weights,
                                         simulate_rock=s.simulate_rock)

        print("[backend] 加载推理管线…", flush=True)
        pipeline = await asyncio.to_thread(factory, settings)
        hub = ConnectionManager()
        worker = FrameWorker(db, pipeline, hub, settings)
        await worker.start()

        app.state.db = db
        app.state.pipeline = pipeline
        app.state.hub = hub
        app.state.worker = worker
        app.state.settings = settings
        app.state.started_at = time.time()
        app.state.client_gaps = {}
        app.state.device_thermal = {}

        cleanup = asyncio.create_task(_cleanup_loop(db, settings), name="retention")
        print(f"[backend] 就绪 db={settings.db_path} 队列={settings.queue_capacity} "
              f"保留={settings.retention_hours}h", flush=True)
        try:
            yield
        finally:
            cleanup.cancel()
            await worker.stop()
            try:
                pipeline.close()
            except Exception:  # noqa: BLE001
                pass
            db.close()

    app = FastAPI(title="mountain-road-backend", version="0.1.0", lifespan=lifespan)

    def ready() -> bool:
        return hasattr(app.state, "db")

    # ---------- 健康与状态 ----------

    @app.get("/api/v1/health")
    async def health() -> dict:
        pipeline = getattr(app.state, "pipeline", None)
        return {
            "status": "ok" if ready() else "starting",
            "service": "mountain-road-backend",
            "rock_detector": bool(getattr(pipeline, "rock_detector", None)),
            "simulate_rock": getattr(pipeline, "simulate_rock", "none"),
            "uptime_s": round(time.time() - app.state.started_at, 1) if ready() else 0,
            "epoch_ns": now_ns(),
        }

    @app.get("/api/v1/status")
    async def status() -> dict:
        if not ready():
            return {"ready": False}
        return {
            "ready": True,
            "latest": app.state.db.latest_assessment(),
            "queue": {"depth": app.state.worker.depth, "capacity": settings.queue_capacity},
            "counters": app.state.worker.counters,
            "db": app.state.db.stats(),
            "ws_clients": app.state.hub.client_count,
            "last_processing_ms": app.state.worker.last_processing_ms,
        }

    @app.get("/api/v1/assessments")
    async def assessments(limit: int = 50, offset: int = 0) -> dict:
        if not ready():
            return {"items": [], "limit": limit, "offset": offset}
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        return {"items": app.state.db.list_assessments(limit, offset),
                "limit": limit, "offset": offset}

    @app.get("/api/v1/events")
    async def events(limit: int = 50) -> dict:
        if not ready():
            return {"items": []}
        return {"items": app.state.db.list_events(max(1, min(limit, 500)))}

    @app.get("/api/v1/devices")
    async def devices() -> dict:
        if not ready():
            return {"items": []}
        return {"items": app.state.db.list_devices()}

    @app.post("/api/v1/devices/heartbeat")
    async def heartbeat(payload: dict = Body(...)) -> dict:
        device_id = (payload or {}).get("device_id")
        if not device_id:
            return JSONResponse(status_code=422,
                                content={"ok": False, "error": "missing device_id"})
        if ready():
            meta = payload.get("meta") or {}
            app.state.db.upsert_device(device_id, now_ns(), meta)

            # 热状态变化写入事件流：让下游知道这段数据是降频采集的
            thermal = meta.get("thermal")
            if thermal:
                previous = app.state.device_thermal.get(device_id)
                if thermal != previous:
                    app.state.device_thermal[device_id] = thermal
                    degraded = thermal in ("过热", "严重过热")
                    app.state.db.insert_event(
                        now_ns(), None, device_id,
                        "WARNING" if degraded else "INFO",
                        "DEVICE_THERMAL_DEGRADED" if degraded else "DEVICE_THERMAL_RECOVERED",
                        f"热状态 {previous or '-'} -> {thermal}，"
                        f"目标 {meta.get('target_fps', '?')}fps，"
                        f"JPEG 质量 {meta.get('jpeg_quality', '?')}")
        return {"ok": True, "device_id": device_id, "server_ns": now_ns()}

    # ---------- 上传 ----------

    @app.post("/api/v1/ingest/frame", status_code=202)
    async def ingest(meta: str = Form(...),
                     rgb: UploadFile = File(...),
                     depth: UploadFile = File(...),
                     confidence: UploadFile | None = File(default=None)):
        if not ready():
            return JSONResponse(status_code=503, content={"ok": False, "code": "STARTING"})

        try:
            metadata = json.loads(meta)
        except json.JSONDecodeError as exc:
            return JSONResponse(status_code=422,
                                content={"ok": False, "code": "BAD_JSON", "message": str(exc)})

        for key in ("frame_id", "device_id", "ts_capture_ns", "depth", "capture"):
            if key not in metadata:
                return JSONResponse(status_code=422,
                                    content={"ok": False, "code": "MISSING_FIELD",
                                             "message": key})

        rgb_bytes = await rgb.read()
        depth_bytes = await depth.read()
        conf_bytes = await confidence.read() if confidence is not None else None

        problems = []
        if len(rgb_bytes) != metadata["capture"]["bytes"]:
            problems.append(f"rgb {len(rgb_bytes)} != {metadata['capture']['bytes']}")
        expected_depth = metadata["depth"]["width"] * metadata["depth"]["height"] * 4
        if len(depth_bytes) < expected_depth:
            problems.append(f"depth {len(depth_bytes)} < {expected_depth}")
        if problems:
            return JSONResponse(status_code=422,
                                content={"ok": False, "code": "SIZE_MISMATCH",
                                         "message": "; ".join(problems)})

        db: Database = app.state.db
        received_ns = now_ns()
        dropped = int(metadata.get("delivery", {}).get("dropped_since_last", 0) or 0)

        db.insert_frame({
            "frame_id": metadata["frame_id"],
            "device_id": metadata["device_id"],
            "session_id": metadata.get("session_id"),
            "seq": metadata.get("seq"),
            "ts_capture_ns": metadata["ts_capture_ns"],
            "ts_received_ns": received_ns,
            "width": metadata["capture"].get("width"),
            "height": metadata["capture"].get("height"),
            "jpeg_bytes": len(rgb_bytes),
            "depth_bytes": len(depth_bytes),
            "confidence_bytes": len(conf_bytes) if conf_bytes else None,
            "dropped_since_last": dropped,
            "status": "queued",
            "error": None,
        })
        db.upsert_device(metadata["device_id"], received_ns)

        if dropped:
            # 逐帧记录会把事件流刷屏，掩盖真正重要的风险变化。
            # 按设备聚合：10 秒最多一条，累计这段时间的丢弃数。
            device_id = metadata["device_id"]
            gaps = app.state.client_gaps
            stats = gaps.setdefault(device_id, {"total": 0, "last_ns": 0})
            stats["total"] += dropped
            if received_ns - stats["last_ns"] >= GAP_EVENT_INTERVAL_NS:
                db.insert_event(received_ns, metadata["frame_id"], device_id,
                                "WARNING", "CLIENT_FRAME_GAP",
                                f"过去 {GAP_EVENT_INTERVAL_NS // 1_000_000_000} 秒累计丢弃 "
                                f"{stats['total']} 帧（客户端队列溢出）")
                stats["total"] = 0
                stats["last_ns"] = received_ns

        outcome = await app.state.worker.submit({
            "frame_id": metadata["frame_id"],
            "device_id": metadata["device_id"],
            "metadata": metadata,
            "rgb": rgb_bytes,
            "depth": depth_bytes,
            "confidence": conf_bytes,
            "dropped_since_last": dropped,
        })

        return {"accepted": True, "frame_id": metadata["frame_id"],
                "queue_depth": app.state.worker.depth, "outcome": outcome}

    # ---------- WebSocket ----------

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        await websocket.accept()
        hub: ConnectionManager | None = getattr(app.state, "hub", None)
        if hub is not None:
            hub.register(websocket)
            latest = app.state.db.latest_assessment()
            if latest:
                await websocket.send_text(json.dumps(
                    {"type": "snapshot", "latest": latest}, ensure_ascii=False))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            if hub is not None:
                hub.disconnect(websocket)

    # 前端静态资源（构建产物）。放在最后注册，API 路由优先匹配。
    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
        print(f"[backend] 前端已挂载: {dist}", flush=True)
    else:
        print("[backend] 未找到 frontend/dist，仅提供 API（先运行 npm run build）", flush=True)

    return app


# uvicorn 入口：backend.app.main:app
app = create_app()
