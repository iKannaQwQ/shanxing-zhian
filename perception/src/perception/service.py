"""感知服务：解码原始帧 → 模型推理 → 产出 PerceptionReport。

这一层刻意**不包含风险判定**。判定属于后端的职责，感知只回答
"画面里有什么、有多确定"。

调用方：`scripts/ai_worker.py`（独立进程）。后端不导入本模块。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .depth.frame import Intrinsics, decode_depth
from .pipeline import FrameInput, PerceptionPipeline
from .road.orientation import rotation_cw


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


class PerceptionService:
    """加载一次模型，之后可反复调用。串行使用（不是线程安全的）。"""

    def __init__(self, device: str = "auto", rock_weights: str | None = None,
                 simulate_rock: str = "none", road_model: str = "yolop"):
        self.device = device
        self.simulate_rock = simulate_rock

        if road_model and road_model != "yolop":
            from .road.unet_segmenter import UNetRoadSegmenter
            self.segmenter = UNetRoadSegmenter(road_model, device=device)
            print(f"[perception] 道路分割: U-Net ({road_model})", flush=True)
        else:
            from .road.segmenter import YolopRoadSegmenter
            self.segmenter = YolopRoadSegmenter(device=device)
            print("[perception] 道路分割: YOLOP", flush=True)

        detector = None
        if rock_weights:
            from .rocks.detector import YoloRockDetector
            detector = YoloRockDetector(rock_weights, device=device)
            print(f"[perception] 落石检测器: {rock_weights}", flush=True)
        else:
            print("[perception] 未部署落石检测器，落石类结论将不可用", flush=True)
        self.rock_detector = detector

        self.pipeline = PerceptionPipeline(road_segmenter=self.segmenter,
                                           rock_detector=detector)

    def process_bytes(self, metadata: dict, rgb_bytes: bytes, depth_bytes: bytes,
                      confidence_bytes: bytes | None) -> dict[str, Any]:
        started = time.perf_counter()
        rgb, depth, confidence, intrinsics = decode_bundle(
            metadata, rgb_bytes, depth_bytes, confidence_bytes)

        # 手机竖持时 capturedImage 里的道路是横躺的，必须先转正再推理
        self.segmenter.rotation_cw = rotation_cw(
            metadata.get("capture", {}).get("device_orientation"))

        report = self.pipeline.process(FrameInput(
            frame_id=metadata["frame_id"],
            timestamp_ns=metadata["ts_capture_ns"],
            rgb=rgb, depth=depth, intrinsics=intrinsics, confidence=confidence,
            rocks=None, source="LIVE"))

        if self.simulate_rock != "none":
            inject_synthetic_rock(report, self.simulate_rock)
        elif self.rock_detector is None:
            report["system_status"] = "DEGRADED"
            report["reasons"].append({
                "code": "ROCK_DETECTOR_UNAVAILABLE",
                "message": "未部署落石检测器，落石类结论不可用"})

        # 契约规定 metrics.timings_ms 是自由结构的数值表，耗时放这里
        report["metrics"].setdefault("timings_ms", {})["perception_total"] = round(
            (time.perf_counter() - started) * 1000, 1)
        return report

    def process_files(self, metadata: dict, rgb_path: str | Path,
                      depth_path: str | Path,
                      confidence_path: str | Path | None) -> dict[str, Any]:
        rgb_bytes = Path(rgb_path).read_bytes()
        depth_bytes = Path(depth_path).read_bytes()
        conf_bytes = (Path(confidence_path).read_bytes()
                      if confidence_path and Path(confidence_path).exists() else None)
        return self.process_bytes(metadata, rgb_bytes, depth_bytes, conf_bytes)
