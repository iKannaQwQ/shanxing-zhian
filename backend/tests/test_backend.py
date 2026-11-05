"""后端单元测试：接收、后台处理、持久化、清理、广播。

不加载真实模型：用 FakePipeline 顶替，测试只关心编排与持久化。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Database, now_ns
from backend.app.main import create_app
from backend.app.ws import ConnectionManager


class FakePipeline:
    """返回固定 RoadStatus，避免测试依赖 YOLOP 与 C++ 引擎。"""

    rock_detector = None
    simulate_rock = "none"

    def __init__(self) -> None:
        self.calls = 0

    def process_bundle(self, metadata, rgb, depth, confidence):  # noqa: ANN001
        self.calls += 1
        return {
            "road_status": {
                "overall_risk": "MEDIUM",
                "system_status": "OK",
                "passability": {"A_TO_B": "RESTRICTED", "B_TO_A": "RESTRICTED"},
                "alternative_path": {"status": "UNKNOWN"},
                "reasons": [{"code": "ROCK_PARTIAL_INTRUSION", "message": "x"}],
            },
            "perception": {
                "road_detected": True,
                "road_mask_status": "OK",
                "road": {"coverage_ratio": 0.21},
                "rockfalls": [{"rock_id": "R1"}],
            },
            "decode_ms": 1.0,
            "perception_ms": 10.0,
            "engine_ms": 0.3,
            "total_ms": 12.3,
        }

    def close(self) -> None:
        pass


def make_bundle(seq: int = 1, rgb_size: int = 1024):
    frame_id = str(uuid.uuid4())
    metadata = {
        "schema_version": "1.0",
        "msg_type": "frame_bundle",
        "frame_id": frame_id,
        "device_id": "iphone13,3-TEST",
        "session_id": str(uuid.uuid4()),
        "seq": seq,
        "ts_capture_ns": time.time_ns(),
        "capture": {"width": 1280, "height": 960, "bytes": rgb_size,
                    "format": "jpeg", "jpeg_quality": 0.72,
                    "orientation": "landscape_right"},
        "intrinsics": {"fx": 980.0, "fy": 980.0, "cx": 640.0, "cy": 480.0,
                       "reference_width": 1280, "reference_height": 960},
        "depth": {"width": 256, "height": 192, "encoding": "float32_m",
                  "scale_factor": 1.0, "invalid_value": 0,
                  "aligned_to_rgb": True, "source": "arkit.sceneDepth"},
        "confidence": {"width": 256, "height": 192, "encoding": "uint8",
                       "semantics": "ar_confidence_level"},
        "delivery": {"queue_depth": 0, "attempt": 1, "dropped_since_last": 0},
    }
    return metadata, (b"\xff" * rgb_size, b"\x00" * (256 * 192 * 4),
                      b"\x02" * (256 * 192))


def post_frame(client: TestClient, seq: int = 1, rgb_size: int = 1024):
    metadata, (rgb, depth, conf) = make_bundle(seq, rgb_size)
    return client.post(
        "/api/v1/ingest/frame",
        data={"meta": json.dumps(metadata)},
        files={"rgb": ("rgb.jpg", rgb, "image/jpeg"),
               "depth": ("depth.bin", depth, "application/octet-stream"),
               "confidence": ("confidence.bin", conf, "application/octet-stream")})


def wait_until(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "test.db", queue_capacity=4)
    app = create_app(settings, pipeline_factory=lambda s: FakePipeline())
    with TestClient(app) as test_client:
        yield test_client


def test_health_ok(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "mountain-road-backend"
    assert body["rock_detector"] is False


def test_ingest_returns_202_and_gets_processed(client):
    response = post_frame(client)
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True

    def processed() -> bool:
        return client.get("/api/v1/status").json()["counters"]["processed"] >= 1

    assert wait_until(processed)

    status = client.get("/api/v1/status").json()
    assert status["latest"]["overall_risk"] == "MEDIUM"
    assert status["latest"]["a_to_b"] == "RESTRICTED"
    assert status["db"]["assessments"] == 1


def test_rejects_size_mismatch(client):
    metadata, (rgb, depth, conf) = make_bundle()
    # 声称的 rgb 字节数与实际不符
    metadata["capture"]["bytes"] = len(rgb) + 10
    response = client.post(
        "/api/v1/ingest/frame",
        data={"meta": json.dumps(metadata)},
        files={"rgb": ("rgb.jpg", rgb, "image/jpeg"),
               "depth": ("depth.bin", depth, "application/octet-stream")})
    assert response.status_code == 422
    assert response.json()["code"] == "SIZE_MISMATCH"


def test_rejects_missing_field(client):
    metadata, (rgb, depth, _conf) = make_bundle()
    del metadata["frame_id"]
    response = client.post(
        "/api/v1/ingest/frame",
        data={"meta": json.dumps(metadata)},
        files={"rgb": ("rgb.jpg", rgb, "image/jpeg"),
               "depth": ("depth.bin", depth, "application/octet-stream")})
    assert response.status_code == 422
    assert response.json()["code"] == "MISSING_FIELD"


def test_risk_change_event_recorded(client):
    post_frame(client)
    assert wait_until(
        lambda: client.get("/api/v1/status").json()["counters"]["processed"] >= 1)
    events = client.get("/api/v1/events").json()["items"]
    assert any(e["code"] == "RISK_CHANGED" for e in events)
    assert events[0]["level"] == "WARNING"


def test_client_frame_gap_event(client):
    metadata, (rgb, depth, conf) = make_bundle(seq=7)
    metadata["delivery"]["dropped_since_last"] = 12
    client.post("/api/v1/ingest/frame",
                data={"meta": json.dumps(metadata)},
                files={"rgb": ("rgb.jpg", rgb, "image/jpeg"),
                       "depth": ("depth.bin", depth, "application/octet-stream"),
                       "confidence": ("confidence.bin", conf, "application/octet-stream")})
    assert wait_until(
        lambda: client.get("/api/v1/status").json()["counters"]["processed"] >= 1)
    events = client.get("/api/v1/events").json()["items"]
    gap = [e for e in events if e["code"] == "CLIENT_FRAME_GAP"]
    assert gap and "12" in gap[0]["message"]


def test_assessments_pagination(client):
    for seq in range(3):
        post_frame(client, seq=seq)
    assert wait_until(
        lambda: client.get("/api/v1/status").json()["counters"]["processed"] >= 3)

    first = client.get("/api/v1/assessments?limit=2").json()
    assert len(first["items"]) == 2
    second = client.get("/api/v1/assessments?limit=2&offset=2").json()
    assert len(second["items"]) == 1


def test_retention_purges_old_rows(tmp_path):
    db = Database(tmp_path / "retain.db")
    now = now_ns()
    base = {
        "device_id": "d", "session_id": "s", "seq": 1, "ts_capture_ns": now,
        "width": 1, "height": 1, "jpeg_bytes": 1, "depth_bytes": 1,
        "confidence_bytes": 1, "dropped_since_last": 0, "status": "done", "error": None,
    }
    db.insert_frame({**base, "frame_id": "old", "ts_received_ns": now - 10**12})
    db.insert_frame({**base, "frame_id": "new", "ts_received_ns": now})
    for fid, ts in (("old", now - 10**12), ("new", now)):
        db.insert_assessment({
            "frame_id": fid, "device_id": "d", "ts_assessed_ns": ts,
            "overall_risk": "LOW", "system_status": "OK", "a_to_b": "PASSABLE",
            "b_to_a": "PASSABLE", "alternative_status": "NOT_EVALUATED",
            "road_detected": 1, "road_status": "OK", "road_coverage": 0.2,
            "rock_count": 0, "processing_ms": 5.0, "json": "{}",
        })

    removed = db.purge_older_than(now - 10**11)
    assert removed["frames"] == 1
    assert removed["assessments"] == 1
    assert db.stats()["frames"] == 1
    db.close()


def test_hub_broadcast_drops_dead_clients():
    class FakeWs:
        def __init__(self, alive: bool):
            self.alive = alive
            self.received = []

        async def send_text(self, text: str):
            if not self.alive:
                raise RuntimeError("closed")
            self.received.append(text)

    hub = ConnectionManager()
    good, bad = FakeWs(True), FakeWs(False)
    hub.register(good)  # type: ignore[arg-type]
    hub.register(bad)   # type: ignore[arg-type]

    sent = asyncio.run(hub.broadcast({"type": "assessment", "overall_risk": "HIGH"}))
    assert sent == 1
    assert hub.client_count == 1
    assert json.loads(good.received[0])["overall_risk"] == "HIGH"
