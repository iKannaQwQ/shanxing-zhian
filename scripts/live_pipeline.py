#!/usr/bin/env python3
"""端到端最小链路（方案 B）：接收 → 感知 → 风险引擎 → 回显。

与 `dev_receiver.py` 的区别：那个只做"收下来 + 校验"，这个把数据真正跑过
YOLOP 道路分割和 C++ 风险引擎，并把 RoadStatus 原路返回给手机。

链路真实性：
  * 没有部署落石检测器时，感知结果会被显式标为 DEGRADED，
    风险引擎因此只能给出 UNKNOWN —— **不会因为"没检出落石"而报 SAFE**。

用法:
    perception/.venv/bin/python scripts/live_pipeline.py --port 8000
    # 可选: --rock-weights path/to/rock.pt  --save-dir data/incoming
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "perception" / "src"))

from perception.depth.frame import Intrinsics, decode_depth  # noqa: E402
from perception.pipeline import FrameInput, PerceptionPipeline  # noqa: E402
from perception.road.segmenter import YolopRoadSegmenter  # noqa: E402

SCHEMA_PATH = ROOT / "contracts" / "schemas" / "frame_bundle.schema.json"
ENGINE_BIN = ROOT / "risk_engine" / "build" / "rock_risk_cli"
OUT_DIR = ROOT / "data" / "incoming"

app = FastAPI(title="mountain-live-pipeline")

_state: dict = {
    "pipeline": None,
    "engine": None,
    "lock": threading.Lock(),
    "processed": 0,
    "rejected": 0,
    "save_dir": None,
    "rock_detector": False,
    "last_status": None,
    "last_error": None,
    "simulate_rock": "none",
}


class RiskEngine:
    """常驻 C++ 引擎进程：每行一帧，避免每次 20+ ms 的进程启动开销。"""

    def __init__(self, binary: Path):
        if not binary.exists():
            raise FileNotFoundError(f"未找到风险引擎: {binary}（先运行 scripts/build_risk_engine.sh）")
        self.process = __import__("subprocess").Popen(
            [str(binary), "--stream"],
            stdin=__import__("subprocess").PIPE,
            stdout=__import__("subprocess").PIPE,
            text=True,
            bufsize=1)

    def assess(self, report: dict) -> dict:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(report, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("风险引擎进程意外退出")
        return json.loads(line)


def decode_bundle(metadata: dict, rgb_bytes: bytes, depth_bytes: bytes,
                  confidence_bytes: bytes | None):
    rgb = cv2.imdecode(np.frombuffer(rgb_bytes, np.uint8), cv2.IMREAD_COLOR)
    if rgb is None:
        raise ValueError("rgb 无法解码")

    d = metadata["depth"]
    dh, dw = d["height"], d["width"]
    # 契约未携带 bytesPerRow，用总长反推；无 padding 时即 width*4
    bytes_per_row = len(depth_bytes) // dh
    floats_per_row = bytes_per_row // 4
    raw = np.frombuffer(depth_bytes, dtype=np.float32,
                        count=floats_per_row * dh).reshape(dh, floats_per_row)[:, :dw]
    depth = decode_depth(raw, encoding="float32_m", aligned_to_rgb=True,
                         source=d.get("source", "arkit.sceneDepth"))

    confidence = None
    if confidence_bytes is not None and "confidence" in metadata:
        c = metadata["confidence"]
        ch, cw = c["height"], c["width"]
        conf_bpr = len(confidence_bytes) // ch
        confidence = np.frombuffer(confidence_bytes, dtype=np.uint8,
                                   count=conf_bpr * ch).reshape(ch, conf_bpr)[:, :cw]

    i = metadata["intrinsics"]
    intrinsics = Intrinsics(i["fx"], i["fy"], i["cx"], i["cy"],
                            i["reference_width"], i["reference_height"])
    return rgb, depth, confidence, intrinsics


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "service": "mountain-live-pipeline",
            "rock_detector": _state["rock_detector"],
            "processed": _state["processed"], "epoch": time.time()}


@app.get("/api/v1/latest")
async def latest() -> dict:
    return {"status": _state["last_status"], "error": _state["last_error"]}


@app.post("/api/v1/ingest/frame")
async def ingest(meta: str = Form(...),
                 rgb: UploadFile = File(...),
                 depth: UploadFile = File(...),
                 confidence: UploadFile | None = File(default=None)):
    started = time.perf_counter()
    try:
        metadata = json.loads(meta)
    except json.JSONDecodeError as exc:
        return _reject("BAD_JSON", str(exc))

    rgb_bytes = await rgb.read()
    depth_bytes = await depth.read()
    conf_bytes = await confidence.read() if confidence is not None else None

    problems = []
    if len(rgb_bytes) != metadata["capture"]["bytes"]:
        problems.append(f"rgb {len(rgb_bytes)} != {metadata['capture']['bytes']}")
    expected = metadata["depth"]["width"] * metadata["depth"]["height"] * 4
    if len(depth_bytes) < expected:
        problems.append(f"depth {len(depth_bytes)} < {expected}")
    if problems:
        return _reject("SIZE_MISMATCH", "; ".join(problems))

    try:
        payload = json.loads(meta)
        rgb_image, depth_map, confidence_map, intrinsics = decode_bundle(
            payload, rgb_bytes, depth_bytes, conf_bytes)
    except Exception as exc:  # noqa: BLE001
        return _reject("DECODE_FAILED", str(exc))

    # 串行处理：HTTP 层天然形成背压，不会在服务端堆出无界队列
    with _state["lock"]:
        try:
            t_perception = time.perf_counter()
            report = _state["pipeline"].process(FrameInput(
                frame_id=payload["frame_id"],
                timestamp_ns=payload["ts_capture_ns"],
                rgb=rgb_image,
                depth=depth_map,
                intrinsics=intrinsics,
                confidence=confidence_map,
                rocks=None,
                source="REPLAY"))
            perception_ms = (time.perf_counter() - t_perception) * 1000

            if _state["simulate_rock"] != "none":
                _inject_synthetic_rock(report, _state["simulate_rock"])
            if not _state["rock_detector"] and _state["simulate_rock"] == "none":
                # 没有落石检测器就不能声称"没发现落石" -> 显式降级
                report["system_status"] = "DEGRADED"
                report["reasons"].append({
                    "code": "ROCK_DETECTOR_UNAVAILABLE",
                    "message": "未部署落石检测器，落石类结论不可用"})

            t_engine = time.perf_counter()
            status = _state["engine"].assess(report)
            engine_ms = (time.perf_counter() - t_engine) * 1000

            _state["processed"] += 1
            _state["last_status"] = {
                "frame_id": payload["frame_id"],
                "overall_risk": status["overall_risk"],
                "system_status": status["system_status"]}

            if _state["save_dir"] is not None:
                _persist(_state["save_dir"], payload, rgb_bytes, depth_bytes, conf_bytes)

            total_ms = (time.perf_counter() - started) * 1000
            if _state["processed"] % 10 == 0:
                print(f"[{_state['processed']:>5}] {payload['frame_id'][:8]} "
                      f"road={report['road_mask_status']} rocks={len(report['rockfalls'])} "
                      f"risk={status['overall_risk']:<8} sys={status['system_status']:<10} "
                      f"感知 {perception_ms:.0f}ms 引擎 {engine_ms:.1f}ms 总 {total_ms:.0f}ms")

            return {"ok": True,
                    "frame_id": payload["frame_id"],
                    "processing_ms": round(total_ms, 1),
                    "perception_ms": round(perception_ms, 1),
                    "engine_ms": round(engine_ms, 2),
                    "road_status": status}
        except Exception as exc:  # noqa: BLE001
            return _reject("PIPELINE_FAILED", f"{type(exc).__name__}: {exc}")


def _inject_synthetic_rock(report: dict, level: str) -> None:
    """注入一个**明确标注**的合成落石，用于验证中高风险判定路径。

    这不是伪造检测结果：`source` 被置为 SIMULATED，落石自带
    SYNTHETIC_DETECTION 理由码，界面上能一眼看出是测试注入。
    """
    remaining = 0.03 if level == "high" else 0.15
    intrusion = "FULL" if level == "high" else "PARTIAL"
    report["source"] = "SIMULATED"
    report["rockfalls"] = [{
        "rock_id": "SIM-1",
        "label": "rock(simulated)",
        "bbox": [420.0, 640.0, 760.0, 820.0],
        "confidence": 0.99,
        "truncated": False,
        "road_overlap_ratio": 0.72,
        "bbox_overlap_ratio": 0.55,
        "intrusion": intrusion,
        "risk_level": intrusion,
        "risk_level_basis": "INTRUSION_GEOMETRY_ONLY",
        "evidence": "RGB_D",
        "lateral_offset_norm": 0.0,
        "remaining_width_px": 120.0,
        "remaining_width_m": remaining,
        "in_road_by_center": True,
        "depth_estimate": {"distance_m": 0.6, "source": "LIDAR", "valid_ratio": 1.0,
                           "sample_count": 9, "point_xyz_m": [0.0, 0.0, 0.6],
                           "reason": "OK"},
        "reasons": [{"code": "SYNTHETIC_DETECTION",
                     "message": "注入的测试目标，非真实检测结果"}],
    }]
    report["metrics"]["rock_count"] = 1
    report["metrics"]["max_risk_level"] = intrusion
    report["reasons"].append({"code": "SYNTHETIC_DETECTION",
                              "message": "本帧包含注入的测试落石"})
    report["system_status"] = "OK"


def _persist(directory: Path, metadata: dict, rgb: bytes, depth: bytes,
             confidence: bytes | None) -> None:
    target = directory / metadata["frame_id"]
    target.mkdir(parents=True, exist_ok=True)
    (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (target / "rgb.jpg").write_bytes(rgb)
    (target / "depth.bin").write_bytes(depth)
    if confidence is not None:
        (target / "confidence.bin").write_bytes(confidence)


def _reject(code: str, message: str) -> JSONResponse:
    _state["rejected"] += 1
    _state["last_error"] = f"{code}: {message}"
    print(f"[REJECT] {code}: {message}")
    return JSONResponse(status_code=422,
                        content={"ok": False, "code": code, "message": message})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--device", default="auto", help="YOLOP 推理设备: auto/cpu/mps/cuda")
    ap.add_argument("--rock-weights", default=None, help="落石检测权重（可选）")
    ap.add_argument("--simulate-rock", default="none",
                    choices=["none", "medium", "high"],
                    help="注入一个标注为 SIMULATED 的合成落石，用于验证风险判定路径")
    ap.add_argument("--save-dir", default=None, help="落盘目录（默认不落盘，避免每轮几百 MB）")
    args = ap.parse_args()

    print("加载 YOLOP 道路分割…")
    segmenter = YolopRoadSegmenter(device=args.device)

    detector = None
    if args.rock_weights:
        from perception.rocks.detector import YoloRockDetector
        detector = YoloRockDetector(args.rock_weights, device=args.device)

    _state["pipeline"] = PerceptionPipeline(road_segmenter=segmenter, rock_detector=detector)
    _state["rock_detector"] = detector is not None
    _state["engine"] = RiskEngine(ENGINE_BIN)
    _state["simulate_rock"] = args.simulate_rock
    if args.save_dir:
        _state["save_dir"] = Path(args.save_dir)
        _state["save_dir"].mkdir(parents=True, exist_ok=True)

    print(f"设备: {segmenter.device} | 落石检测器: {'已加载' if detector else '未部署（将降级为 UNKNOWN）'}")
    print(f"风险引擎: {ENGINE_BIN.name} --stream")
    print(f"落盘: {_state['save_dir'] or '关闭'}")
    if args.simulate_rock != "none":
        print(f"⚠️  已注入合成落石（{args.simulate_rock}），结果标注为 SIMULATED，仅用于测试")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
