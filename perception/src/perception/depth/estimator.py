"""单块落石的深度估计与像素-米制换算（M5）。

限制必须写在代码里而不是注释里：
  * 深度缺失/置信度不足/超出 LiDAR 有效量程 → 一律返回 None，状态保留 UNKNOWN；
  * 只对落石"接地中心"做小窗口采样，避免框内背景深度污染；
  * 米制宽度由 Z/fx 换算，依赖相机内参，内参缺失时不给米制值。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..status import DepthSource
from ..types import BBox, DepthEstimate
from .align import (AlignmentReport, check_alignment, rgb_to_depth_px,
                    sample_confidence, sample_patch)
from .frame import DepthMap, Intrinsics


@dataclass(frozen=True)
class DepthPolicy:
    win: int = 3
    min_valid_ratio: float = 0.34     # 窗口内有效点占比下限
    min_confidence: float = 0.30      # confidenceMap 均值下限
    min_range_m: float = 0.20         # LiDAR 近距下限（需实测校准）
    max_range_m: float = 5.00         # LiDAR 远距上限


def contact_point(bbox: BBox, rgb_shape: tuple[int, int]) -> tuple[float, float]:
    """接地点：检测框底部中心。落石与地面的接触位置比框中心更接近真实距离。"""
    h, w = rgb_shape
    x = float(np.clip((bbox.x1 + bbox.x2) / 2.0, 0, w - 1))
    y = float(np.clip(bbox.y2 - 1.0, 0, h - 1))
    return x, y


def px_to_meters(px: float, z_m: float, intr: Intrinsics) -> float:
    """使用针孔模型把横向像素差换算成米。z 为该处深度。"""
    if not np.isfinite(z_m) or intr.fx <= 0:
        return float("nan")
    return float(px) * z_m / float(intr.fx)


def estimate_rock_depth(bbox: BBox,
                        rgb_shape: tuple[int, int],
                        depth: DepthMap | None,
                        intrinsics: Intrinsics | None,
                        confidence: np.ndarray | None = None,
                        policy: DepthPolicy | None = None,
                        report: AlignmentReport | None = None) -> DepthEstimate:
    policy = policy or DepthPolicy()

    if depth is None:
        return DepthEstimate(source=DepthSource.NONE, reason="NO_DEPTH_DATA")

    rep = report or check_alignment(rgb_shape, depth)
    if not rep.ok:
        return DepthEstimate(source=DepthSource.NONE, reason="ALIGNMENT_UNVERIFIED")

    cx, cy = contact_point(bbox, rgb_shape)
    dpx = rgb_to_depth_px(cx, cy, rgb_shape, depth, rep)
    if dpx is None:
        return DepthEstimate(source=DepthSource.NONE, reason="ALIGNMENT_UNVERIFIED")

    z, ratio, n = sample_patch(depth, dpx[0], dpx[1], policy.win)
    if not np.isfinite(z):
        return DepthEstimate(source=DepthSource.NONE, valid_ratio=ratio, sample_count=n,
                             reason="DEPTH_INVALID_AT_CONTACT")

    if ratio < policy.min_valid_ratio:
        return DepthEstimate(source=DepthSource.NONE, valid_ratio=ratio, sample_count=n,
                             reason=f"DEPTH_SPARSE(valid={ratio:.2f})")

    conf = sample_confidence(confidence, depth, dpx[0], dpx[1], policy.win)
    if conf is not None and conf < policy.min_confidence:
        return DepthEstimate(source=DepthSource.NONE, valid_ratio=ratio, sample_count=n,
                             reason=f"LOW_CONFIDENCE({conf:.2f})")

    if z < policy.min_range_m or z > policy.max_range_m:
        return DepthEstimate(source=DepthSource.NONE, valid_ratio=ratio, sample_count=n,
                             reason=f"OUT_OF_LIDAR_RANGE({z:.2f}m)")

    point = None
    if intrinsics is not None:
        h, w = rgb_shape
        k = intrinsics.scaled_to(w, h)
        u, v = cx, cy
        point = (float((u - k.cx) * z / k.fx),
                 float((v - k.cy) * z / k.fy),
                 float(z))

    return DepthEstimate(distance_m=z, source=DepthSource.LIDAR,
                         valid_ratio=ratio, sample_count=n,
                         reason="OK", point_xyz_m=point)


def depth_quality(depth: DepthMap | None, report: AlignmentReport | None,
                  min_ratio: float = 0.30) -> tuple[bool, list[str]]:
    """整帧深度是否可用于米制推断。"""
    reasons: list[str] = []
    if depth is None:
        return False, ["NO_DEPTH_DATA"]
    if report is not None and not report.ok:
        reasons.extend(r.split(":", 1)[0] for r in report.reasons)
    if depth.valid_ratio < min_ratio:
        reasons.append(f"DEPTH_VALID_RATIO_LOW({depth.valid_ratio:.2f})")
    return (len(reasons) == 0), reasons
