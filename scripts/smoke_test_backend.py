#!/usr/bin/env python3
"""后端端到端自检（不需要视觉模型）。

流程：上传一帧 → 确认落盘与入队 → 模拟 AI 回传感知结果 →
      确认判定落库 → 确认查询接口能读回。

用法:
    perception/.venv/bin/python scripts/smoke_test_backend.py \
        --backend http://127.0.0.1:8000 --frame-dir /tmp/vb_check/<frame>

不传 --frame-dir 时会自行构造一帧（图像内容无意义，只验证通路）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_REPORT = ROOT / "contracts" / "samples" / "perception_report" / \
    "degraded_no_detector.json"

PASS, FAIL = "PASS", "FAIL"


def request(url: str, method: str = "GET", data: bytes | None = None,
            headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def multipart(metadata: dict, rgb: bytes, depth: bytes, confidence: bytes):
    boundary = f"----smoke{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def field(name: str, value: bytes, filename: str | None = None,
              content_type: str | None = None) -> None:
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            header += f'; filename="{filename}"'
        header += "\r\n"
        if content_type:
            header += f"Content-Type: {content_type}\r\n"
        parts.append(header.encode() + b"\r\n" + value + b"\r\n")

    field("meta", json.dumps(metadata).encode())
    field("rgb", rgb, "rgb.jpg", "image/jpeg")
    field("depth", depth, "depth.bin", "application/octet-stream")
    field("confidence", confidence, "confidence.bin", "application/octet-stream")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


def build_frame(frame_dir: Path | None):
    if frame_dir:
        metadata = json.loads((frame_dir / "metadata.json").read_text(encoding="utf-8"))
        rgb = (frame_dir / "rgb.jpg").read_bytes()
        depth = (frame_dir / "depth.bin").read_bytes()
        confidence = (frame_dir / "confidence.bin").read_bytes()
    else:
        rgb = b"\xff\xd8\xff\xd9"  # 最小 JPEG 头尾，内容不重要
        metadata = {
            "schema_version": "1.0", "msg_type": "frame_bundle",
            "frame_id": str(uuid.uuid4()), "device_id": "smoke-test",
            "session_id": str(uuid.uuid4()), "seq": 1,
            "ts_capture_ns": time.time_ns(),
            "capture": {"width": 1280, "height": 960, "bytes": len(rgb),
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
        depth = b"\x00" * (256 * 192 * 4)
        confidence = b"\x02" * (256 * 192)

    metadata["frame_id"] = str(uuid.uuid4())
    return metadata, rgb, depth, confidence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--frame-dir", type=Path, default=None)
    args = ap.parse_args()
    base = args.backend.rstrip("/")

    results: list[tuple[str, str, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, PASS if ok else FAIL, detail))

    code, body = request(f"{base}/api/v1/health")
    health = json.loads(body or b"{}")
    check("健康检查", code == 200 and health.get("status") == "ok", f"HTTP {code}")
    check("推理已解耦", health.get("inference") == "external",
          f"inference={health.get('inference')}")

    metadata, rgb, depth, confidence = build_frame(args.frame_dir)
    boundary, payload = multipart(metadata, rgb, depth, confidence)
    code, body = request(f"{base}/api/v1/ingest/frame", "POST", payload,
                         {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    accepted = json.loads(body or b"{}")
    check("上传返回 202", code == 202, f"HTTP {code}")
    check("落盘并入队", accepted.get("status") == "stored",
          f"status={accepted.get('status')}")

    artifact_dir = Path(accepted.get("artifact_dir") or "")
    check("四件套落盘",
          all((artifact_dir / name).exists()
              for name in ("metadata.json", "rgb.jpg", "depth.bin", "confidence.bin")),
          str(artifact_dir))

    code, body = request(f"{base}/api/v1/frames/pending?limit=8")
    pending = json.loads(body or b"{}")
    check("待处理队列可读", any(item["frame_id"] == metadata["frame_id"]
                          for item in pending.get("items", [])),
          f"count={pending.get('count')}")

    report = json.loads(SAMPLE_REPORT.read_text(encoding="utf-8"))
    report["frame_id"] = metadata["frame_id"]
    code, body = request(f"{base}/api/v1/ingest/perception", "POST",
                         json.dumps({"frame_id": metadata["frame_id"],
                                     "perception": report}).encode(),
                         {"Content-Type": "application/json"})
    verdict = json.loads(body or b"{}")
    check("AI 结果被接收", code == 202, f"HTTP {code}")
    check("风险引擎给出判定", verdict.get("overall_risk") == "UNKNOWN",
          f"risk={verdict.get('overall_risk')} status={verdict.get('system_status')}")

    code, body = request(f"{base}/api/v1/status")
    status = json.loads(body or b"{}")
    check("查询到最新判定",
          (status.get("latest") or {}).get("frame_id") == metadata["frame_id"],
          f"age={status.get('age_ms')}ms stale={status.get('stale')}")

    code, body = request(f"{base}/api/v1/frames/{metadata['frame_id']}/rgb")
    check("原始帧可下载", code == 200 and len(body) == len(rgb), f"{len(body)} bytes")

    print()
    failed = 0
    for name, result, detail in results:
        if result == FAIL:
            failed += 1
        print(f"  {result}  {name:<18} {detail}")
    print()
    print(f"{'全部通过' if failed == 0 else f'{failed} 项失败'} "
          f"({len(results) - failed}/{len(results)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
