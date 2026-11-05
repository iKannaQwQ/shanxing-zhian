"""推理管线：解码 → 感知 → 风险引擎。

与 `scripts/live_pipeline.py` 的区别：这里的对象是被后台 worker 调用的，
不直接服务 HTTP 请求，因此可以在一个常驻线程里跑，不会阻塞事件循环。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PERCEPTION_SRC = ROOT / "perception" / "src"
ENGINE_BIN = ROOT / "risk_engine" / "build" / "rock_risk_cli"

if str(PERCEPTION_SRC) not in sys.path:
    sys.path.insert(0, str(PERCEPTION_SRC))

from perception.depth.frame import Intrinsics, decode_depth  # noqa: E402
from perception.pipeline import FrameInput, PerceptionPipeline  # noqa: E402
from perception.road.orientation import rotation_cw  # noqa: E402


class RiskEngine:
    """常驻 C++ 引擎：每行一帧，避免每帧 20+ ms 的进程启动。"""

    def __init__(self, binary: Path = ENGINE_BIN):
        if not Path(binary).exists():
            raise FileNotFoundError(
                f"未找到风险引擎 {binary}，请先运行 scripts/build_risk_engine.sh")
        self._process = subprocess.Popen([str(binary), "--stream"],
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         text=True, bufsize=1)

    def assess(self, report: dict) -> dict:
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


def decode_bundle(metadata: dict, rgb_bytes: bytes, depth_bytes: bytes,
                  confidence_bytes: bytes | None):
    rgb = cv2.imdecode(np.frombuffer(rgb_bytes, np.uint8), cv2.IMREAD_COLOR)
    if rgb is None:
        raise ValueError("rgb 无法解码")

    d = metadata["depth"]
    dh, dw = d["height"], d["width"]
    # 契约暂未携带 bytesPerRow，用总长反推；无 padding 时即 width*4
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


def inject_synthetic_rock(report: dict, level: str) -> None:
    """注入明确标注的合成落石，用于验证中高风险路径。

    不是伪造检测：`source` 置为 SIMULATED，并带 SYNTHETIC_DETECTION 理由码。
    """
    remaining = 0.03 if level == "high" else 0.15
    intrusion = "FULL" if level == "high" else "PARTIAL"
    report["source"] = "SIMULATED"
    report["rockfalls"] = [{
        "rock_id": "SIM-1", "label": "rock(simulated)",
        "bbox": [420.0, 640.0, 760.0, 820.0], "confidence": 0.99, "truncated": False,
        "road_overlap_ratio": 0.72, "bbox_overlap_ratio": 0.55,
        "intrusion": intrusion, "risk_level": intrusion,
        "risk_level_basis": "INTRUSION_GEOMETRY_ONLY", "evidence": "RGB_D",
        "lateral_offset_norm": 0.0, "remaining_width_px": 120.0,
        "remaining_width_m": remaining, "in_road_by_center": True,
        "depth_estimate": {"distance_m": 0.6, "source": "LIDAR", "valid_ratio": 1.0,
                           "sample_count": 9, "point_xyz_m": [0.0, 0.0, 0.6],
                           "reason": "OK"},
        "reasons": [{"code": "SYNTHETIC_DETECTION", "message": "注入的测试目标"}],
    }]
    report["metrics"]["rock_count"] = 1
    report["metrics"]["max_risk_level"] = intrusion
    report["reasons"].append({"code": "SYNTHETIC_DETECTION", "message": "本帧含注入测试落石"})
    report["system_status"] = "OK"


class InferencePipeline:
    """串行推理。构造时加载模型，之后 `process_bundle` 可重复调用。"""

    def __init__(self, device: str = "auto", rock_weights: str | None = None,
                 simulate_rock: str = "none"):
        from perception.road.segmenter import YolopRoadSegmenter

        self.device = device
        self.simulate_rock = simulate_rock
        self.segmenter = YolopRoadSegmenter(device=device)

        detector = None
        if rock_weights:
            from perception.rocks.detector import YoloRockDetector
            detector = YoloRockDetector(rock_weights, device=device)
        self.rock_detector = detector
        self.pipeline = PerceptionPipeline(road_segmenter=self.segmenter, rock_detector=detector)
        self.engine = RiskEngine()

    def process_bundle(self, metadata: dict, rgb_bytes: bytes, depth_bytes: bytes,
                       confidence_bytes: bytes | None) -> dict[str, Any]:
        t0 = time.perf_counter()
        device_orientation = metadata.get("capture", {}).get("device_orientation")
        rgb, depth, confidence, intrinsics = decode_bundle(
            metadata, rgb_bytes, depth_bytes, confidence_bytes)
        decode_ms = (time.perf_counter() - t0) * 1000

        # 设备竖持时 capturedImage 里的道路是横躺的，必须先转正再推理
        rotation = rotation_cw(device_orientation)
        self.segmenter.rotation_cw = rotation

        t1 = time.perf_counter()
        report = self.pipeline.process(FrameInput(
            frame_id=metadata["frame_id"],
            timestamp_ns=metadata["ts_capture_ns"],
            rgb=rgb, depth=depth, intrinsics=intrinsics, confidence=confidence,
            rocks=None, source="LIVE"))
        perception_ms = (time.perf_counter() - t1) * 1000

        if self.simulate_rock != "none":
            inject_synthetic_rock(report, self.simulate_rock)
        elif self.rock_detector is None:
            report["system_status"] = "DEGRADED"
            report["reasons"].append({
                "code": "ROCK_DETECTOR_UNAVAILABLE",
                "message": "未部署落石检测器，落石类结论不可用"})

        t2 = time.perf_counter()
        status = self.engine.assess(report)
        engine_ms = (time.perf_counter() - t2) * 1000

        return {
            "road_status": status,
            "perception": report,
            "decode_ms": round(decode_ms, 2),
            "perception_ms": round(perception_ms, 1),
            "engine_ms": round(engine_ms, 2),
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def close(self) -> None:
        self.engine.close()
