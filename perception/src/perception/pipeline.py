"""感知流水线：RGB(+Depth) -> 统一 JSON（M6/M7 的编排层）。

编排顺序刻意固定为：
    道路分割 -> 落石检测 -> 深度关联 -> 侵入计算 -> 状态裁决 -> 序列化
任一步失败都不得让后续步骤"假装成功"：道路失败时侵入等级必须是 UNKNOWN。
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .depth.align import check_alignment
from .depth.estimator import (DepthPolicy, depth_quality, estimate_rock_depth,
                              px_to_meters)
from .depth.frame import DepthMap, Intrinsics
from .geometry.intrusion import (IntrusionPolicy, assess_intrusion,
                                 assess_multiple_intrusions)
from .report import build_report, report_to_dict
from .road.mask_ops import assess_road, clean_mask
from .status import IntrusionLevel, RoadMaskStatus, SystemStatus
from .types import Reason, RockObservation


@dataclass
class FrameInput:
    frame_id: str
    timestamp_ns: int
    rgb: np.ndarray | None = None
    depth: DepthMap | None = None
    intrinsics: Intrinsics | None = None
    confidence: np.ndarray | None = None
    road_mask: np.ndarray | None = None
    rocks: list[RockObservation] | None = None
    source: str = "LIVE"


@dataclass
class PipelineConfig:
    mask_kernel: int = 5
    min_road_coverage: float = 0.02
    max_components: int = 4
    min_road_confidence: float = 0.5
    max_rocks: int = 50
    min_depth_ratio: float = 0.30
    timeout_ms: float = 2000.0
    intrusion: IntrusionPolicy = field(default_factory=IntrusionPolicy)
    depth: DepthPolicy = field(default_factory=DepthPolicy)


class PerceptionPipeline:
    def __init__(self,
                 road_segmenter: Any | None = None,
                 rock_detector: Any | None = None,
                 config: PipelineConfig | None = None):
        self.road_segmenter = road_segmenter
        self.last_road_mask = None
        self.rock_detector = rock_detector
        self.cfg = config or PipelineConfig()

    # ---------- 对外 ----------

    def process(self, inp: FrameInput) -> dict[str, Any]:
        t_start = time.perf_counter()
        timings: dict[str, float] = {}

        if inp.rgb is None and inp.road_mask is None:
            return self._error_report(inp, "INPUT_ERROR", "INPUT_MISSING",
                                      "既无 RGB 也无道路掩码")

        try:
            t0 = time.perf_counter()
            road = self._segment_road(inp)
            # 保留掩码供可视化（worker 会存成 PNG 供管理后台叠加显示）
            self.last_road_mask = road.mask
            timings["road_ms"] = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            rocks = self._detect_rocks(inp)
            timings["rock_ms"] = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            depth_ok, depth_reasons, align_rep = self._depth_status(inp, road)
            timings["depth_ms"] = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            estimates = {}
            for rock in rocks:
                estimates[rock.rock_id] = estimate_rock_depth(
                    rock.bbox, inp.rgb.shape[:2] if inp.rgb is not None else road.mask.shape,
                    inp.depth, inp.intrinsics, inp.confidence,
                    self.cfg.depth, align_rep)
            timings["depth_est_ms"] = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            intrusions, union = assess_multiple_intrusions(
                rocks, road, self.cfg.intrusion)
            for a in intrusions:
                est = estimates.get(a.rock_id)
                if est is not None and est.distance_m is not None and inp.intrinsics is not None:
                    if a.remaining_width_px is not None:
                        m = px_to_meters(a.remaining_width_px, est.distance_m, inp.intrinsics)
                        a.remaining_width_m = float(m) if np.isfinite(m) else None
            timings["intrusion_ms"] = (time.perf_counter() - t0) * 1000

            timings["total_ms"] = (time.perf_counter() - t_start) * 1000
            report = build_report(
                frame_id=inp.frame_id, timestamp_ns=inp.timestamp_ns, road=road,
                rocks=rocks, intrusions=intrusions, depth_estimates=estimates,
                depth_ok=depth_ok, depth_reasons=depth_reasons, union_info=union,
                source=inp.source, timings_ms=timings,
            )

            if timings["total_ms"] > self.cfg.timeout_ms:
                report.system_status = SystemStatus.TIMEOUT
                report.reasons.append(Reason(
                    "INFERENCE_TIMEOUT",
                    f"处理耗时 {timings['total_ms']:.0f}ms 超过预算 {self.cfg.timeout_ms:.0f}ms"))
            return report_to_dict(report)

        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            return self._error_report(inp, "INPUT_ERROR", "PIPELINE_EXCEPTION",
                                      f"{type(e).__name__}: {e} | {tb.splitlines()[-1]}")

    def disconnected(self, frame_id: str, timestamp_ns: int) -> dict[str, Any]:
        """摄像头断开时也要给出一帧结构完整的输出，状态为 DISCONNECTED。"""
        return {
            "msg_type": "perception_report",
            "schema_version": "1.0",
            "frame_id": frame_id,
            "timestamp": _iso(timestamp_ns),
            "timestamp_ns": timestamp_ns,
            "source": "LIVE",
            "system_status": SystemStatus.DISCONNECTED.value,
            "road_detected": False,
            "road_mask_status": RoadMaskStatus.FAILED.value,
            "road": {"coverage_ratio": 0.0, "confidence": None, "component_count": 0,
                     "hole_ratio": 0.0, "polygon": [], "width_profile": [], "centerline": []},
            "rockfalls": [],
            "depth": {"usable": False, "reasons": ["CAMERA_DISCONNECTED"]},
            "metrics": {"rock_count": 0, "max_risk_level": IntrusionLevel.UNKNOWN.value,
                        "combined_remaining_width_px": None, "combined_blocked": None,
                        "timings_ms": {}},
            "reasons": [{"code": "CAMERA_DISCONNECTED", "message": "摄像头连接中断"}],
            "disclaimer": "实验性辅助评估，不构成真实道路驾驶指令；未检测到落石不代表安全。",
        }

    # ---------- 内部 ----------

    def _segment_road(self, inp: FrameInput):
        mask = inp.road_mask
        conf: float | None = None

        if mask is not None:
            mask = clean_mask(mask, self.cfg.mask_kernel)
        elif self.road_segmenter is not None:
            mask, conf = self.road_segmenter.predict(inp.rgb)
            mask = clean_mask(mask, self.cfg.mask_kernel)
        else:
            return assess_road(None)

        return assess_road(mask, confidence=conf,
                           min_coverage=self.cfg.min_road_coverage,
                           max_components=self.cfg.max_components,
                           min_confidence=self.cfg.min_road_confidence)

    def _detect_rocks(self, inp: FrameInput) -> list[RockObservation]:
        if inp.rocks is not None:
            rocks = list(inp.rocks)
        elif self.rock_detector is not None and inp.rgb is not None:
            rocks = self.rock_detector.predict(inp.rgb)
        else:
            rocks = []
        rocks.sort(key=lambda r: r.score, reverse=True)
        return rocks[: self.cfg.max_rocks]

    def _depth_status(self, inp: FrameInput, road) -> tuple[bool, list[str], Any]:
        if inp.rgb is None:
            shape = road.mask.shape if road.mask is not None else (1, 1)
        else:
            shape = inp.rgb.shape[:2]
        rep = check_alignment(shape, inp.depth) if inp.depth is not None else None
        ok, reasons = depth_quality(inp.depth, rep, self.cfg.min_depth_ratio)
        return ok, reasons, rep

    def _error_report(self, inp: FrameInput, status: str, code: str,
                      message: str) -> dict[str, Any]:
        return {
            "msg_type": "perception_report",
            "schema_version": "1.0",
            "frame_id": inp.frame_id,
            "timestamp": _iso(inp.timestamp_ns),
            "timestamp_ns": inp.timestamp_ns,
            "source": inp.source,
            "system_status": status,
            "road_detected": False,
            "road_mask_status": RoadMaskStatus.FAILED.value,
            "road": {"coverage_ratio": 0.0, "confidence": None, "component_count": 0,
                     "hole_ratio": 0.0, "polygon": [], "width_profile": [], "centerline": []},
            "rockfalls": [],
            "depth": {"usable": False, "reasons": [code]},
            "metrics": {"rock_count": 0, "max_risk_level": IntrusionLevel.UNKNOWN.value,
                        "combined_remaining_width_px": None, "combined_blocked": None,
                        "timings_ms": {}},
            "reasons": [{"code": code, "message": message}],
            "disclaimer": "实验性辅助评估，不构成真实道路驾驶指令；未检测到落石不代表安全。",
        }


def _iso(ns: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()
