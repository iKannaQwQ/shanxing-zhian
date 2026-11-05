"""后端测试：接收 → 落盘 → 排队 → AI 回传 → 判定 → 查询。

不加载任何模型：AI 结果直接由测试按契约 POST 进来，
这正是解耦之后后端应该被测试的样子。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import ROOT, Settings
from backend.app.db import Database, now_ns
from backend.app.main import create_app

SAMPLE_REPORT = ROOT / "contracts" / "samples" / "perception_report" / \
    "degraded_no_detector.json"


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
                    "orientation": "landscape_right",
                    "device_orientation": "landscape_right"},
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
    response = client.post(
        "/api/v1/ingest/frame",
        data={"meta": json.dumps(metadata)},
        files={"rgb": ("rgb.jpg", rgb, "image/jpeg"),
               "depth": ("depth.bin", depth, "application/octet-stream"),
               "confidence": ("confidence.bin", conf, "application/octet-stream")})
    return response, metadata


def post_perception(client: TestClient, frame_id: str, report: dict | None = None):
    payload = dict(report or json.loads(SAMPLE_REPORT.read_text(encoding="utf-8")))
    payload["frame_id"] = frame_id
    return client.post("/api/v1/ingest/perception",
                       json={"frame_id": frame_id, "perception": payload})


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "test.db",
                        artifacts_dir=tmp_path / "artifacts")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_external_inference(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["inference"] == "external"


def test_ingest_stores_files_and_queues(client, tmp_path):
    response, metadata = post_frame(client)
    assert response.status_code == 202
    assert response.json()["status"] == "stored"

    directory = tmp_path / "artifacts" / metadata["frame_id"]
    assert (directory / "rgb.jpg").exists()
    assert (directory / "depth.bin").stat().st_size == 256 * 192 * 4
    assert (directory / "confidence.bin").exists()
    assert (directory / "metadata.json").exists()

    status = client.get("/api/v1/status").json()
    assert status["queue"]["depth"] == 1
    assert status["counters"]["queued"] == 1


def test_pending_returns_paths_not_binary(client):
    _, metadata = post_frame(client)
    body = client.get("/api/v1/frames/pending?limit=4").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["frame_id"] == metadata["frame_id"]
    assert item["metadata"]["frame_id"] == metadata["frame_id"]
    assert Path(item["rgb_path"]).exists()
    assert "rgb" not in item  # 不返回二进制


def test_perception_ingest_runs_engine_and_marks_processed(client):
    _, metadata = post_frame(client)
    response = post_perception(client, metadata["frame_id"])
    assert response.status_code == 202
    body = response.json()
    assert body["overall_risk"] == "UNKNOWN"      # 无落石检测器 -> 不能给 SAFE
    assert body["system_status"] == "DEGRADED"

    status = client.get("/api/v1/status").json()
    assert status["counters"]["processed"] == 1
    assert status["queue"]["depth"] == 0
    assert status["latest"]["frame_id"] == metadata["frame_id"]


def test_duplicate_perception_is_rejected(client):
    _, metadata = post_frame(client)
    assert post_perception(client, metadata["frame_id"]).status_code == 202
    again = post_perception(client, metadata["frame_id"])
    assert again.json()["code"] == "ALREADY_PROCESSED"


def test_perception_for_unknown_frame(client):
    response = post_perception(client, "does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "UNKNOWN_FRAME"


def test_rejects_size_mismatch(client):
    metadata, (rgb, depth, _conf) = make_bundle()
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


def test_artifact_download(client):
    _, metadata = post_frame(client)
    response = client.get(f"/api/v1/frames/{metadata['frame_id']}/rgb")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff")

    missing = client.get(f"/api/v1/frames/{metadata['frame_id']}/nope")
    assert missing.status_code == 404


def test_status_reports_staleness(client):
    _, metadata = post_frame(client)
    post_perception(client, metadata["frame_id"])
    status = client.get("/api/v1/status").json()
    assert status["age_ms"] is not None and status["age_ms"] >= 0
    assert status["stale"] is False
    assert status["stale_after_ms"] > 0


def test_device_online_after_upload(client):
    post_frame(client)
    devices = client.get("/api/v1/devices").json()["items"]
    assert len(devices) == 1
    assert devices[0]["online"] is True


def test_heartbeat_thermal_event(client):
    client.post("/api/v1/devices/heartbeat",
                json={"device_id": "dev-1",
                      "meta": {"thermal": "过热", "target_fps": 5, "jpeg_quality": 0.65}})
    events = client.get("/api/v1/events").json()["items"]
    assert any(event["code"] == "DEVICE_THERMAL_DEGRADED" for event in events)


def test_frame_expiry(tmp_path):
    db = Database(tmp_path / "expire.db")
    now = now_ns()
    db.insert_frame({
        "frame_id": "old", "device_id": "d", "session_id": "s", "seq": 1,
        "ts_capture_ns": now, "ts_received_ns": now - 10**12,
        "width": 1, "height": 1, "jpeg_bytes": 1, "depth_bytes": 1,
        "confidence_bytes": 1, "dropped_since_last": 0, "status": "stored",
        "error": None, "artifact_dir": "", "rgb_path": "", "depth_path": "",
        "confidence_path": "",
    })
    expired = db.expire_stale_frames(now - 10**11)
    assert expired == ["old"]
    assert db.frame("old")["status"] == "expired"
    db.close()


def test_backend_does_not_import_perception():
    """解耦的硬性检查：后端源码不得引用感知库。"""
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from perception" not in text, f"{path.name} 导入了感知库"
        assert "import perception" not in text, f"{path.name} 导入了感知库"
