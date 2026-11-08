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

import cv2
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
from .types import BBox, Reason, RockObservation


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
    # 模型平均预测熵上限：超过视为分布外，直接判 LOW_CONFIDENCE
    max_road_uncertainty: float = 0.20
    # 障碍来源：none | geometric（用深度几何，不需要检测模型）
    obstacle_mode: str = "none"
    # 可通行区来源：road_model（U-Net 道路分割）| plane（深度平面内点）
    # plane 只适用于"可通行区 = 平整地面"的固定俯视场景（沙盘/桌面）
    road_source: str = "road_model"
    min_obstacle_height_m: float = 0.008
    min_obstacle_area_px: int = 12
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
        self.last_obstacle_geometry = None
        self._geometry_cache_input = None
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
        if self.cfg.road_source == "plane" and inp.depth is not None \
                and inp.intrinsics is not None:
            return self._plane_corridor(inp)

        mask = inp.road_mask
        conf: float | None = None

        if mask is not None:
            mask = clean_mask(mask, self.cfg.mask_kernel)
        elif self.road_segmenter is not None:
            mask, conf = self.road_segmenter.predict(inp.rgb)
            mask = clean_mask(mask, self.cfg.mask_kernel)
        else:
            return assess_road(None)

        uncertainty = getattr(self.road_segmenter, "last_uncertainty", None)
        return assess_road(mask, confidence=conf,
                           min_coverage=self.cfg.min_road_coverage,
                           max_components=self.cfg.max_components,
                           min_confidence=self.cfg.min_road_confidence,
                           uncertainty=uncertainty,
                           max_uncertainty=self.cfg.max_road_uncertainty)

    def _detect_rocks(self, inp: FrameInput) -> list[RockObservation]:
        if inp.rocks is not None:
            rocks = list(inp.rocks)
        elif self.rock_detector is not None and inp.rgb is not None:
            rocks = self.rock_detector.predict(inp.rgb)
        elif (self.cfg.obstacle_mode == "geometric" and inp.depth is not None
              and inp.intrinsics is not None and inp.rgb is not None):
            rocks = self._geometric_obstacles(inp)
        else:
            rocks = []
        rocks.sort(key=lambda r: r.score, reverse=True)
        return rocks[: self.cfg.max_rocks]

    def _geometry_for(self, inp: FrameInput):
        """按需计算一次几何结果，供可通行区与障碍检测复用。"""
        if self._geometry_cache_input is inp and self._last_geometry is not None:
            return self._last_geometry
        from .obstacles.geometry import fit_ground_plane

        dh, dw = inp.depth.shape
        intr = inp.intrinsics.scaled_to(dw, dh)
        plane, reason = fit_ground_plane(inp.depth.meters, intr.fx, intr.fy, intr.cx, intr.cy)
        self._last_geometry = (plane, reason, (dh, dw))
        self._geometry_cache_input = inp
        return self._last_geometry

    def _plane_corridor(self, inp: FrameInput):
        """把"平整地面"当作可通行区。仅用于固定俯视的沙盘/桌面场景。"""
        import numpy as np
        from .road.mask_ops import assess_road
        from .status import RoadMaskStatus

        plane, reason, (dh, dw) = self._geometry_for(inp)
        if plane is None:
            return assess_road(None)

        # 内点 = 距平面很近的点，即"平整地面"
        from .obstacles.geometry import _backproject, _dot
        intr = inp.intrinsics.scaled_to(dw, dh)
        points, valid = _backproject(inp.depth.meters, intr.fx, intr.fy, intr.cx, intr.cy)
        residual = _dot(points, plane.normal) + plane.offset
        flat = np.zeros((dh, dw), dtype=np.uint8)
        flat[valid] = (np.abs(residual) < max(3.0 * plane.sigma_m, 0.008)).astype(np.uint8)

        # 放大到 RGB 分辨率，与道路 mask 保持一致
        rh, rw = inp.rgb.shape[:2]
        mask = cv2.resize(flat, (rw, rh), interpolation=cv2.INTER_NEAREST)
        road = assess_road(mask, confidence=1.0,
                           min_coverage=self.cfg.min_road_coverage,
                           max_components=self.cfg.max_components)
        if road.status is RoadMaskStatus.OK:
            road.reasons.append(Reason(
                "CORRIDOR_FROM_PLANE",
                "可通行区由深度平面内点给出（固定俯视场景），非道路分割结果"))
        return road

    def _geometric_obstacles(self, inp: FrameInput) -> list[RockObservation]:
        """用 RGB-D 几何找障碍：不识别类别，只判断"高出地面"。"""
        from .obstacles.geometry import detect_obstacles

        dh, dw = inp.depth.shape
        intr = inp.intrinsics.scaled_to(dw, dh)
        result = detect_obstacles(
            inp.depth.meters, intr.fx, intr.fy, intr.cx, intr.cy,
            min_height_m=self.cfg.min_obstacle_height_m,
            min_area_px=self.cfg.min_obstacle_area_px)
        self.last_obstacle_geometry = result

        if not result.protrusions:
            return []
        # 深度网格 -> RGB 像素（两者同源对齐，缩放比通常相等，但分开算更稳）
        rh, rw = inp.rgb.shape[:2]
        sx, sy = rw / float(dw), rh / float(dh)
        observations: list[RockObservation] = []
        for index, protrusion in enumerate(result.protrusions):
            obs = protrusion.to_observation(index)
            box = obs.bbox
            observations.append(RockObservation(
                rock_id=obs.rock_id,
                bbox=BBox(box.x1 * sx, box.y1 * sy, box.x2 * sx, box.y2 * sy),
                score=obs.score, label=obs.label, truncated=False))
        return observations

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
