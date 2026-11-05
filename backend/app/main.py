"""FastAPI 应用：接收、落盘、排队、判定、推送。

架构边界：
  * 后端**不加载任何模型**，AI 推理由独立 worker 完成（scripts/ai_worker.py）；
  * 后端负责：接收原始帧 → 落盘 → 排队 → 收到 AI 结果后跑风险引擎 → 落库 → 推送；
  * 网页请求只读数据库与内存状态，**永不触发推理**。

数据流：
    iPhone --HTTP multipart--> /ingest/frame --落盘+入队--> frames(status=stored)
                                                              ^
                                        AI worker 轮询 /frames/pending
                                                              v
                              POST /ingest/perception --> 风险引擎 --> assessments
                                                              v
                                                   WebSocket 推送到前端
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .db import Database, now_ns
from .risk import RiskEngine, assess_and_store, perception_to_message
from .storage import ArtifactStore
from .ws import ConnectionManager

PERCEPTION_SCHEMA = ROOT / "contracts" / "schemas" / "perception_report.schema.json"


def _validate_perception(report: dict) -> list[str]:
    """按契约校验 AI 回传。格式不对就拒收，而不是让它悄悄进判定。"""
    import jsonschema

    schema = json.loads(PERCEPTION_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(report),
                    key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in errors[:5]]

GAP_EVENT_INTERVAL_NS = 10 * 1_000_000_000


async def _monitor_loop(app: FastAPI, settings: Settings) -> None:
    """过期检测、设备离线、保留清理。全部是读库 + 判状态，不做任何推理。"""
    offline_devices: set[str] = set()
    stale_announced = False
    while True:
        await asyncio.sleep(2.0)
        try:
            db: Database = app.state.db
            hub: ConnectionManager = app.state.hub

            # 1) 帧等待 AI 结果超时
            cutoff = now_ns() - int(settings.frame_expire_seconds * 1e9)
            for frame_id in db.expire_stale_frames(cutoff):
                frame = db.frame(frame_id)
                db.insert_event(now_ns(), frame_id,
                                frame["device_id"] if frame else None,
                                "WARNING", "FRAME_EXPIRED",
                                f"AI 结果超过 {settings.frame_expire_seconds:.0f}s 未返回")
                await hub.broadcast({"type": "error", "code": "FRAME_EXPIRED",
                                     "frame_id": frame_id})

            # 2) 设备离线（边沿触发，避免每 2 秒刷一条）
            offline_after = int(settings.device_offline_seconds * 1e9)
            for device in db.device_online(offline_after):
                device_id = device["device_id"]
                if not device["online"] and device_id not in offline_devices:
                    offline_devices.add(device_id)
                    db.insert_event(now_ns(), None, device_id, "WARNING",
                                    "DEVICE_OFFLINE",
                                    f"已 {device['silent_ms'] / 1000:.0f}s 没有数据")
                    await hub.broadcast({"type": "device_offline", "device_id": device_id,
                                         "silent_ms": device["silent_ms"]})
                elif device["online"] and device_id in offline_devices:
                    offline_devices.discard(device_id)
                    await hub.broadcast({"type": "device_online", "device_id": device_id})

            # 3) 数据过期（边沿触发）
            latest = db.latest_assessment()
            age_ms = ((now_ns() - latest["ts_assessed_ns"]) / 1_000_000
                      if latest else None)
            is_stale = age_ms is not None and age_ms > settings.stale_after_seconds * 1000
            if is_stale != stale_announced:
                stale_announced = is_stale
                await hub.broadcast({"type": "stale" if is_stale else "fresh",
                                     "age_ms": round(age_ms or 0)})

            # 4) 保留窗口清理（数据库 + 文件）
            retention_cutoff = now_ns() - int(settings.retention_hours * 3600 * 1e9)
            removed = db.purge_older_than(retention_cutoff)
            if any(removed.values()):
                keep = set(db.all_frame_ids())
                files = app.state.store.prune_missing(keep)
                print(f"[retention] 数据库 {removed}，文件 {files} 个", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[monitor] 出错: {exc}", flush=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        store = ArtifactStore(settings.artifacts_dir)
        hub = ConnectionManager()
        engine = RiskEngine()

        app.state.db = db
        app.state.store = store
        app.state.hub = hub
        app.state.engine = engine
        app.state.settings = settings
        app.state.started_at = time.time()
        app.state.client_gaps = {}
        app.state.device_thermal = {}

        monitor = asyncio.create_task(_monitor_loop(app, settings), name="monitor")
        print(f"[backend] 就绪 db={settings.db_path} 帧目录={settings.artifacts_dir}", flush=True)
        print("[backend] 推理已解耦：请另起 scripts/ai_worker.py", flush=True)
        try:
            yield
        finally:
            monitor.cancel()
            engine.close()
            db.close()

    app = FastAPI(title="mountain-road-backend", version="0.2.0", lifespan=lifespan)

    def ready() -> bool:
        return hasattr(app.state, "db")

    # ---------- 健康与总览 ----------

    @app.get("/api/v1/health")
    async def health() -> dict:
        return {
            "status": "ok" if ready() else "starting",
            "service": "mountain-road-backend",
            "version": "0.2.0",
            "inference": "external",
            "uptime_s": round(time.time() - app.state.started_at, 1) if ready() else 0,
            "epoch_ns": now_ns(),
        }

    @app.get("/api/v1/status")
    async def status() -> dict:
        if not ready():
            return {"ready": False}
        db: Database = app.state.db
        latest = db.latest_assessment()
        age_ms = (now_ns() - latest["ts_assessed_ns"]) / 1_000_000 if latest else None
        counters = db.counters()

        # AI 推理 FPS：最近 10 秒完成的判定数 / 10
        window_seconds = 10
        recent = db.assessments_since(now_ns() - window_seconds * 1_000_000_000)
        ai_fps = round(recent / window_seconds, 2)
        ai_age_ms = round(age_ms) if age_ms is not None else None
        pending = counters["queued"]
        # 有积压却长时间没产出 -> worker 很可能挂了，而不是"空闲"
        if pending > 0 and (ai_age_ms is None or ai_age_ms > 5000):
            ai_state = "STALLED"
        elif ai_age_ms is not None and ai_age_ms < 5000:
            ai_state = "ONLINE"
        else:
            ai_state = "IDLE"

        device_list = db.device_online(int(settings.device_offline_seconds * 1e9))

        return {
            "ready": True,
            "server_ns": now_ns(),
            "latest": latest,
            "age_ms": round(age_ms) if age_ms is not None else None,
            "stale": age_ms is not None and age_ms > settings.stale_after_seconds * 1000,
            "stale_after_ms": int(settings.stale_after_seconds * 1000),
            "queue": {"depth": counters["queued"], "capacity": None},
            "counters": counters,
            "devices": device_list,
            "ai_fps": ai_fps,
            "system": {
                "backend": "OK",
                "ai_worker": ai_state,
                "ai_last_result_ms": ai_age_ms,
                "pending_frames": pending,
                "devices_online": sum(1 for d in device_list if d.get("online")),
                "devices_total": len(device_list),
                "ws_clients": app.state.hub.client_count,
                "frames_failed": counters["failed"],
            },
            "db": db.stats(),
            "artifacts": app.state.store.disk_usage(),
            "ws_clients": app.state.hub.client_count,
            "last_processing_ms": latest["processing_ms"] if latest else 0,
        }

    # ---------- 查询 ----------

    @app.get("/api/v1/assessments")
    async def assessments(limit: int = 50, offset: int = 0) -> dict:
        if not ready():
            return {"items": [], "limit": limit, "offset": offset}
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        return {"items": app.state.db.list_assessments(limit, offset),
                "limit": limit, "offset": offset, "server_ns": now_ns()}

    @app.get("/api/v1/events")
    async def events(limit: int = 50) -> dict:
        if not ready():
            return {"items": []}
        return {"items": app.state.db.list_events(max(1, min(limit, 500)))}

    @app.get("/api/v1/rock-events")
    async def rock_events(limit: int = 50, offset: int = 0, status: str = "ALL") -> dict:
        if not ready():
            return {"items": [], "total": 0}
        limit = max(1, min(limit, 200))
        db: Database = app.state.db
        return {
            "items": db.list_rock_events(limit, max(0, offset), status),
            "total": db.count_rock_events(status),
            "limit": limit,
            "offset": max(0, offset),
        }

    @app.patch("/api/v1/rock-events/{event_id}")
    async def update_rock_event(event_id: int, payload: dict = Body(...)) -> Any:
        if not ready():
            return JSONResponse(status_code=503, content={"ok": False})
        status = (payload or {}).get("status")
        if status is not None and status not in ("OPEN", "ACKNOWLEDGED", "RESOLVED", "IGNORED"):
            return JSONResponse(status_code=422,
                                content={"ok": False, "code": "BAD_STATUS"})
        note = (payload or {}).get("note")
        updated = app.state.db.update_rock_event(event_id, note, status)
        if not updated:
            return JSONResponse(status_code=404, content={"ok": False, "code": "NOT_FOUND"})
        return {"ok": True, "event": updated}

    @app.get("/api/v1/stats/timeline")
    async def timeline(minutes: int = 30, buckets: int = 30) -> dict:
        """按时间分桶的风险/通行分布，供历史页画时间轴。"""
        if not ready():
            return {"buckets": [], "minutes": minutes}
        minutes = max(1, min(minutes, 24 * 60))
        buckets = max(1, min(buckets, 240))
        db: Database = app.state.db
        now = now_ns()
        since = now - minutes * 60 * 1_000_000_000
        rows = db.timeline_rows(since)
        width = (now - since) // buckets

        result = []
        for index in range(buckets):
            start = since + index * width
            end = start + width
            chunk = [r for r in rows if start <= r["ts_assessed_ns"] < end]
            risks = [r["overall_risk"] for r in chunk]
            worst = "UNKNOWN"
            order = ["SAFE", "LOW", "MEDIUM", "HIGH", "UNKNOWN"]
            if risks:
                worst = max(risks, key=order.index)
            result.append({
                "start_ns": start,
                "count": len(chunk),
                "worst_risk": worst if risks else None,
                "blocked": sum(1 for r in chunk
                               if r["a_to_b"] == "BLOCKED" or r["b_to_a"] == "BLOCKED"),
                "unknown": sum(1 for r in risks if r == "UNKNOWN"),
            })
        return {"buckets": result, "minutes": minutes, "since_ns": since,
                "total": len(rows), "server_ns": now}

    @app.get("/api/v1/devices")
    async def devices() -> dict:
        if not ready():
            return {"items": []}
        return {"items": app.state.db.device_online(
            int(settings.device_offline_seconds * 1e9))}

    @app.post("/api/v1/devices/heartbeat")
    async def heartbeat(payload: dict = Body(...)) -> dict:
        device_id = (payload or {}).get("device_id")
        if not device_id:
            return JSONResponse(status_code=422,
                                content={"ok": False, "error": "missing device_id"})
        if ready():
            meta = payload.get("meta") or {}
            app.state.db.upsert_device(device_id, now_ns(), meta, count_frame=False)
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
                        f"目标 {meta.get('target_fps', '?')}fps")
        return {"ok": True, "device_id": device_id, "server_ns": now_ns()}

    # ---------- 帧 ----------

    @app.get("/api/v1/frames/pending")
    async def frames_pending(limit: int = 4) -> dict:
        """AI worker 拉取待处理帧。返回文件路径与元数据，不返回二进制。"""
        if not ready():
            return {"items": [], "count": 0}
        limit = max(1, min(limit, 16))
        items = []
        for frame in app.state.db.frames_pending(limit):
            metadata = None
            metadata_path = Path(frame["artifact_dir"] or "") / "metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    metadata = None
            items.append({
                "frame_id": frame["frame_id"],
                "device_id": frame["device_id"],
                "seq": frame["seq"],
                "ts_received_ns": frame["ts_received_ns"],
                "metadata": metadata,
                "rgb_path": frame["rgb_path"],
                "depth_path": frame["depth_path"],
                "confidence_path": frame["confidence_path"],
            })
        return {"items": items, "count": len(items), "server_ns": now_ns()}

    @app.get("/api/v1/frames/{frame_id}")
    async def frame_info(frame_id: str) -> Any:
        if not ready():
            return JSONResponse(status_code=503, content={"ok": False})
        frame = app.state.db.frame(frame_id)
        if not frame:
            return JSONResponse(status_code=404, content={"ok": False, "error": "not found"})
        return frame

    @app.get("/api/v1/frames/{frame_id}/{artifact}")
    async def frame_artifact(frame_id: str, artifact: str) -> Any:
        names = {"rgb": "rgb.jpg", "depth": "depth.bin",
                 "confidence": "confidence.bin", "metadata": "metadata.json",
                 "road_mask": "road_mask.png", "perception": "perception.json"}
        if artifact not in names:
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": "unknown artifact"})
        path = app.state.store.directory(frame_id) / names[artifact]
        if not path.exists():
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": "file missing"})
        return FileResponse(str(path))

    # ---------- 接收 ----------

    @app.post("/api/v1/ingest/frame", status_code=202)
    async def ingest_frame(meta: str = Form(...),
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

        paths = app.state.store.save(metadata["frame_id"], metadata,
                                     rgb_bytes, depth_bytes, conf_bytes)

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
            "status": "stored",
            "error": None,
            **paths,
        })
        db.upsert_device(metadata["device_id"], received_ns, metadata.get("device"))

        if dropped:
            stats = app.state.client_gaps.setdefault(
                metadata["device_id"], {"total": 0, "last_ns": 0})
            stats["total"] += dropped
            if received_ns - stats["last_ns"] >= GAP_EVENT_INTERVAL_NS:
                db.insert_event(received_ns, metadata["frame_id"], metadata["device_id"],
                                "WARNING", "CLIENT_FRAME_GAP",
                                f"过去 {GAP_EVENT_INTERVAL_NS // 1_000_000_000} 秒累计丢弃 "
                                f"{stats['total']} 帧")
                stats["total"] = 0
                stats["last_ns"] = received_ns

        return {"accepted": True, "frame_id": metadata["frame_id"],
                "status": "stored", "artifact_dir": paths["artifact_dir"]}

    @app.post("/api/v1/ingest/perception", status_code=202)
    async def ingest_perception(payload: dict = Body(...)) -> Any:
        """AI worker 回传感知结果。后端据此跑风险引擎并推送。"""
        if not ready():
            return JSONResponse(status_code=503, content={"ok": False, "code": "STARTING"})

        frame_id = (payload or {}).get("frame_id")
        report = (payload or {}).get("perception") or (payload or {}).get("report")
        if not frame_id or not isinstance(report, dict):
            return JSONResponse(status_code=422,
                                content={"ok": False, "code": "BAD_PAYLOAD",
                                         "message": "需要 frame_id 与 perception"})

        problems = _validate_perception(report)
        if problems:
            return JSONResponse(status_code=422,
                                content={"ok": False, "code": "INVALID_REPORT",
                                         "errors": problems})

        db: Database = app.state.db
        frame = db.frame(frame_id)
        if not frame:
            return JSONResponse(status_code=404,
                                content={"ok": False, "code": "UNKNOWN_FRAME"})
        if frame["status"] == "processed":
            return {"accepted": False, "code": "ALREADY_PROCESSED", "frame_id": frame_id}

        try:
            status = assess_and_store(db, app.state.engine, frame_id,
                                      frame["device_id"], report)
        except Exception as exc:  # noqa: BLE001
            db.mark_frame(frame_id, "failed", f"{type(exc).__name__}: {exc}")
            db.insert_event(now_ns(), frame_id, frame["device_id"],
                            "ERROR", "ASSESS_FAILED", str(exc))
            await app.state.hub.broadcast({"type": "error", "code": "ASSESS_FAILED",
                                           "frame_id": frame_id, "message": str(exc)})
            return JSONResponse(status_code=500,
                                content={"ok": False, "code": "ASSESS_FAILED",
                                         "message": str(exc)})

        await app.state.hub.broadcast(perception_to_message(frame, report, status))
        return {"accepted": True, "frame_id": frame_id,
                "overall_risk": status["overall_risk"],
                "system_status": status["system_status"]}

    # ---------- WebSocket ----------

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        await websocket.accept()
        hub: ConnectionManager | None = getattr(app.state, "hub", None)
        if hub is not None:
            hub.register(websocket)
            latest = app.state.db.latest_assessment()
            # 连上先给快照。必须标明这是历史数据，不能让它被当成实时推送。
            await websocket.send_text(json.dumps({
                "type": "snapshot",
                "latest": latest,
                "is_history": True,
                "server_ns": now_ns(),
            }, ensure_ascii=False))
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

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
