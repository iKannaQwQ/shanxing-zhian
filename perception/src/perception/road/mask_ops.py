"""道路掩码几何处理（M2）。

不依赖车道线：全部信息来自可行驶区域 mask 的形态。
允许弯曲/不规则形状；对山体遮挡造成的断口只做"度量并上报"，不擅自填补
（断口本身可能就是落石遮挡，填补会掩盖证据）。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..status import RoadMaskStatus
from ..types import Reason, RoadObservation


def to_binary_mask(mask: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    """把概率图/标签图统一成 0/1 的 uint8。"""
    if mask is None:
        raise ValueError("mask 为 None")
    m = np.asarray(mask)
    if m.ndim == 3:
        m = np.argmax(m, axis=0) if m.shape[0] <= 4 else np.argmax(m, axis=-1)
    m = (m > threshold).astype(np.uint8) if m.dtype != np.uint8 or m.max() <= 1 else (m > 0).astype(np.uint8)
    return m


def clean_mask(mask: np.ndarray, kernel_size: int = 5, min_area_ratio: float = 0.004) -> np.ndarray:
    """去噪：开运算 + 移除过小连通域。不填补孔洞。"""
    m = to_binary_mask(mask)
    if not m.any():
        return m
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return m
    total = m.size
    out = np.zeros_like(m)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area_ratio * total:
            out[labels == i] = 1
    return out


def component_stats(mask: np.ndarray, min_area_ratio: float = 0.004) -> tuple[int, list[int]]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(to_binary_mask(mask), connectivity=8)
    areas = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
             if stats[i, cv2.CC_STAT_AREA] >= min_area_ratio * mask.size]
    return len(areas), sorted(areas, reverse=True)


def hole_ratio(mask: np.ndarray) -> float:
    """孔洞面积 / 外轮廓填充面积。

    孔洞 ≈ 落石或遮挡物在道路 mask 上"挖掉"的区域，也可能来自分割噪声。
    只度量，不填补。
    """
    m = to_binary_mask(mask)
    if not m.any():
        return 0.0
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(m)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    filled_px = int(filled.sum())
    if filled_px == 0:
        return 0.0
    holes = int(np.count_nonzero((filled == 1) & (m == 0)))
    return holes / filled_px


def largest_contour(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(to_binary_mask(mask), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def extract_polygon(mask: np.ndarray, epsilon_ratio: float = 0.01) -> list[tuple[float, float]]:
    """外轮廓多边形近似。epsilon 相对周长，允许弯曲形状（不做凸包）。"""
    c = largest_contour(mask)
    if c is None:
        return []
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, epsilon_ratio * peri, True)
    return [(float(p[0][0]), float(p[0][1])) for p in approx]


def _longest_run(row: np.ndarray) -> tuple[int, int] | None:
    """返回该行最长连续 1 段的 [start, end) 列区间。"""
    if not row.any():
        return None
    idx = np.flatnonzero(row)
    splits = np.split(idx, np.where(np.diff(idx) != 1)[0] + 1)
    best = max(splits, key=len)
    return int(best[0]), int(best[-1]) + 1


def width_profile(mask: np.ndarray, bands: int = 16,
                  min_row_ratio: float = 0.05) -> list[dict[str, float]]:
    """按行带统计道路横向宽度。

    每个 band 取该区域内"最长连续段"的左右边界——这样弯道与不规则形状也能处理，
    而不会被同行的其他连通域撑宽。
    """
    m = to_binary_mask(mask)
    h = m.shape[0]
    if h < bands:
        bands = max(1, h)
    edges = np.linspace(0, h, bands + 1).astype(int)
    out: list[dict[str, float]] = []
    for i in range(bands):
        y0, y1 = edges[i], edges[i + 1]
        if y1 <= y0:
            continue
        band = m[y0:y1]
        row = (band.mean(axis=0) >= 0.5).astype(np.uint8)
        run = _longest_run(row)
        if run is None:
            out.append({"y": float((y0 + y1) / 2), "left": -1.0, "right": -1.0,
                        "width_px": 0.0, "y0": float(y0), "y1": float(y1)})
            continue
        xl, xr = run
        out.append({"y": float((y0 + y1) / 2), "left": float(xl), "right": float(xr - 1),
                    "width_px": float(xr - xl), "y0": float(y0), "y1": float(y1)})
    return out


def centerline(profile: list[dict[str, float]]) -> list[tuple[float, float]]:
    return [((p["left"] + p["right"]) / 2.0, p["y"]) for p in profile if p["width_px"] > 0]


def road_extent_at(mask: np.ndarray, y: float) -> tuple[int, int] | None:
    """指定行的道路左右边界（最长连续段）。用于侵入判定。"""
    m = to_binary_mask(mask)
    y = int(np.clip(round(y), 0, m.shape[0] - 1))
    return _longest_run(m[y])


def assess_road(mask: np.ndarray | None,
                confidence: float | None = None,
                min_coverage: float = 0.02,
                max_components: int = 4,
                min_confidence: float = 0.5,
                bands: int = 16) -> RoadObservation:
    """把原始 mask 变成带状态与理由的道路观测。"""
    reasons: list[Reason] = []

    if mask is None:
        return RoadObservation(status=RoadMaskStatus.FAILED,
                               reasons=[Reason("ROAD_MASK_MISSING", "未提供道路掩码")])

    m = to_binary_mask(mask)
    if not m.any():
        return RoadObservation(status=RoadMaskStatus.EMPTY, mask=m,
                               reasons=[Reason("ROAD_MASK_EMPTY", "道路掩码为空")])

    coverage = float(m.sum()) / float(m.size)
    n_comp, areas = component_stats(m)
    poly = extract_polygon(m)
    prof = width_profile(m, bands=bands)

    status = RoadMaskStatus.OK
    if coverage < min_coverage:
        status = RoadMaskStatus.TOO_SMALL
        reasons.append(Reason("ROAD_TOO_SMALL",
                              f"道路面积占比 {coverage:.3f} 低于阈值 {min_coverage}"))
    elif confidence is not None and confidence < min_confidence:
        status = RoadMaskStatus.LOW_CONFIDENCE
        reasons.append(Reason("ROAD_LOW_CONFIDENCE",
                              f"分割置信度 {confidence:.2f} 低于阈值 {min_confidence}"))
    elif n_comp > max_components:
        status = RoadMaskStatus.FRAGMENTED
        reasons.append(Reason("ROAD_FRAGMENTED",
                              f"连通域 {n_comp} 个，超过上限 {max_components}"))

    gaps = sum(1 for p in prof if p["width_px"] == 0)
    if gaps and status is RoadMaskStatus.OK:
        reasons.append(Reason("ROAD_HAS_GAPS", f"{gaps} 个行带无道路（遮挡或断口）"))

    hr = hole_ratio(m)
    if hr > 0.35:
        reasons.append(Reason("ROAD_LARGE_HOLE", f"孔洞占比 {hr:.2f}，疑似遮挡或落石"))

    return RoadObservation(
        status=status, mask=m, coverage_ratio=coverage, confidence=confidence,
        polygon=poly, centerline=centerline(prof), width_profile=prof,
        component_count=n_comp, hole_ratio=hr, reasons=reasons,
    )
