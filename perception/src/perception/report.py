"""统一 JSON 输出（M7）。

一条硬规则：`risk_level` 只表示"落石对道路的几何侵入程度"，
**不是"这条路能不能走"的结论**。因此附带
`risk_level_basis` 与 `evidence`，并且永不输出 "SAFE"。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

from .status import IntrusionLevel, RoadMaskStatus, SystemStatus
from .types import (DepthEstimate, FrameReport, IntrusionAssessment,
                    Reason, RoadObservation, RockObservation)

DISCLAIMER = "实验性辅助评估，不构成真实道路驾驶指令；未检测到落石不代表安全。"

SEVERITY_ORDER = [IntrusionLevel.NONE, IntrusionLevel.EDGE,
                  IntrusionLevel.PARTIAL, IntrusionLevel.FULL, IntrusionLevel.UNKNOWN]


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def overall_risk_level(levels: list[IntrusionLevel], road: RoadObservation) -> IntrusionLevel:
    """帧级侵入等级：只要有一块未知就整体未知；否则取最严重。"""
    if not road.detected:
        return IntrusionLevel.UNKNOWN
    if not levels:
        return IntrusionLevel.NONE
    if IntrusionLevel.UNKNOWN in levels:
        return IntrusionLevel.UNKNOWN
    return max(levels, key=SEVERITY_ORDER.index)


def resolve_system_status(road: RoadObservation,
                          depth_ok: bool,
                          depth_reasons: list[str],
                          inference_failed: bool = False) -> tuple[SystemStatus, list[Reason]]:
    reasons: list[Reason] = []
    if inference_failed:
        return SystemStatus.INPUT_ERROR, [Reason("INFERENCE_FAILED", "推理过程异常")]

    if road.status in (RoadMaskStatus.FAILED, RoadMaskStatus.EMPTY):
        return SystemStatus.NO_ROAD, [Reason("NO_ROAD", "未检测到道路区域")]

    degraded = False
    if road.status is RoadMaskStatus.TOO_SMALL:
        return SystemStatus.NO_ROAD, [Reason("ROAD_TOO_SMALL", "道路面积过小，无法作为判据")]
    if road.status in (RoadMaskStatus.LOW_CONFIDENCE, RoadMaskStatus.FRAGMENTED):
        degraded = True
        reasons.append(Reason("ROAD_LOW_QUALITY", f"道路掩码质量异常: {road.status.value}"))

    if not depth_ok:
        for r in depth_reasons:
            reasons.append(Reason(r.split("(")[0], r))
        return SystemStatus.NO_DEPTH, reasons

    return (SystemStatus.DEGRADED if degraded else SystemStatus.OK), reasons


def _rock_entry(rock: RockObservation,
                intr: IntrusionAssessment | None,
                dep: DepthEstimate) -> dict[str, Any]:
    level = intr.level if intr else IntrusionLevel.UNKNOWN
    entry: dict[str, Any] = {
        "rock_id": rock.rock_id,
        "label": rock.label,
        "bbox": [round(v, 2) for v in rock.bbox.as_list()],
        "confidence": round(rock.score, 4),
        "truncated": rock.truncated,
    }
    if intr is None:
        entry.update({
            "road_overlap_ratio": None,
            "bbox_overlap_ratio": None,
            "intrusion": IntrusionLevel.UNKNOWN.value,
            "risk_level": IntrusionLevel.UNKNOWN.value,
            "risk_level_basis": "INTRUSION_GEOMETRY_ONLY",
            "evidence": "UNAVAILABLE",
            "lateral_offset_norm": None,
            "remaining_width_px": None,
            "remaining_width_m": None,
            "in_road_by_center": None,
            "reasons": [],
        })
    else:
        entry.update({
            "road_overlap_ratio": round(intr.overlap_ratio, 4),
            "bbox_overlap_ratio": round(intr.bbox_overlap_ratio, 4),
            "intrusion": intr.level.value,
            "risk_level": level.value,
            "risk_level_basis": "INTRUSION_GEOMETRY_ONLY",
            "evidence": "RGB_D" if dep.source.value == "LIDAR" else "RGB_ONLY",
            "lateral_offset_norm": (round(intr.lateral_offset_norm, 4)
                                    if intr.lateral_offset_norm is not None else None),
            "remaining_width_px": (round(intr.remaining_width_px, 2)
                                   if intr.remaining_width_px is not None else None),
            "remaining_width_m": (round(intr.remaining_width_m, 4)
                                  if intr.remaining_width_m is not None else None),
            "in_road_by_center": intr.in_road_by_center,
            "reasons": [r.as_dict() for r in intr.reasons],
        })

    entry["depth_estimate"] = {
        "distance_m": (round(dep.distance_m, 4) if dep.distance_m is not None else None),
        "source": dep.source.value,
        "valid_ratio": round(dep.valid_ratio, 4),
        "sample_count": dep.sample_count,
        "point_xyz_m": ([round(v, 4) for v in dep.point_xyz_m]
                        if dep.point_xyz_m else None),
        "reason": dep.reason,
    }
    return entry


def build_report(frame_id: str,
                 timestamp_ns: int,
                 road: RoadObservation,
                 rocks: list[RockObservation],
                 intrusions: list[IntrusionAssessment],
                 depth_estimates: dict[str, DepthEstimate],
                 depth_ok: bool,
                 depth_reasons: list[str],
                 union_info: dict | None = None,
                 source: str = "LIVE",
                 timings_ms: dict[str, float] | None = None,
                 inference_failed: bool = False) -> FrameReport:
    status, status_reasons = resolve_system_status(road, depth_ok, depth_reasons,
                                                   inference_failed)
    intr_by_id = {a.rock_id: a for a in intrusions}
    entries = [_rock_entry(r, intr_by_id.get(r.rock_id),
                           depth_estimates.get(r.rock_id, DepthEstimate()))
               for r in rocks]

    levels = [IntrusionLevel(e["risk_level"]) for e in entries]
    frame_level = overall_risk_level(levels, road)

    reasons = list(road.reasons) + status_reasons
    noted = {r.code for r in reasons}
    if not road.detected and "NO_ROAD" not in noted:
        reasons.append(Reason("NO_ROAD", "道路未检出，禁止推断通行安全"))
    if not rocks and road.detected:
        reasons.append(Reason("NO_ROCK_DETECTED", "未检出落石（不等于安全，受观测范围限制）"))

    return FrameReport(
        frame_id=frame_id,
        timestamp_ns=int(timestamp_ns),
        system_status=status,
        road_detected=road.detected,
        road_mask_status=road.status,
        rockfalls=entries,
        road={
            "coverage_ratio": round(road.coverage_ratio, 4),
            "confidence": (round(road.confidence, 4) if road.confidence is not None else None),
            "component_count": road.component_count,
            "hole_ratio": round(road.hole_ratio, 4),
            "polygon": [[round(x, 1), round(y, 1)] for x, y in road.polygon],
            "width_profile": road.width_profile,
            "centerline": [[round(x, 1), round(y, 1)] for x, y in road.centerline],
        },
        depth={
            "usable": bool(depth_ok),
            "reasons": depth_reasons,
        },
        reasons=reasons,
        metrics={
            "rock_count": len(rocks),
            "max_risk_level": frame_level.value,
            "combined_remaining_width_px": (union_info or {}).get("combined_remaining_width_px"),
            "combined_blocked": (union_info or {}).get("combined_blocked"),
            "timings_ms": {k: round(v, 2) for k, v in (timings_ms or {}).items()},
        },
        source=source,
    )


def report_to_dict(report: FrameReport) -> dict[str, Any]:
    """序列化成统一 JSON。np 类型统一转 Python 原生，避免 json 报错。"""
    def _native(v: Any) -> Any:
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            return v.tolist()
        return v

    return {
        "msg_type": "perception_report",
        "schema_version": report.schema_version,
        "frame_id": report.frame_id,
        "timestamp": _iso(report.timestamp_ns),
        "timestamp_ns": report.timestamp_ns,
        "source": report.source,
        "system_status": report.system_status.value,
        "road_detected": bool(report.road_detected),
        "road_mask_status": report.road_mask_status.value,
        "road": {k: _native(v) for k, v in report.road.items()},
        "rockfalls": [{k: _native(v) for k, v in r.items()} for r in report.rockfalls],
        "depth": report.depth,
        "metrics": {k: _native(v) for k, v in report.metrics.items()},
        "reasons": [r.as_dict() for r in report.reasons],
        "disclaimer": DISCLAIMER,
    }
