"""基于 RGB-D 几何的道路障碍检测（不依赖任何目标检测模型）。

思路：固定俯视相机下，路面/地面近似一个平面。障碍物 = 明显高出该平面的连通区域。

为什么可行（实测依据，见 docs/20）：
  * 真机 LiDAR 在 132–418 mm 处的平面拟合残差 σ ≈ 2.0 mm，P95 ≈ 4.4 mm；
  * 10 mm 障碍 ≈ 4.7σ、20 mm ≈ 9.4σ，信噪比充足。

设计原则：
  * **不需要标注数据**，也不需要训练；
  * 深度无效或平面拟合不可靠时返回空列表 + 明确原因，由上游转 UNKNOWN；
  * 输出仍是 `RockObservation`，下游（侵入判定、C++ 引擎、前端）零改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..types import BBox, RockObservation  # noqa: F401

#: 平面内点阈值（米）。经验值：约 2.5σ，σ 由实测给出。
DEFAULT_INLIER_M = 0.005
#: 判定为障碍的最小高度（米）。低于此值视为噪声/路面纹理。
DEFAULT_MIN_HEIGHT_M = 0.008
#: 障碍连通域的最小像素面积（深度图分辨率下）
DEFAULT_MIN_AREA_PX = 12
#: 有效深度比例低于该值时不拟合平面（fail-closed）
DEFAULT_MIN_VALID_RATIO = 0.30
#: 忽略画面边框这么多像素内的连通域。
#: 实测深度传感器在边缘有系统偏差，不加抑制会稳定误报（见 docs/22）
DEFAULT_BORDER_MARGIN_PX = 6


@dataclass
class GroundPlane:
    normal: np.ndarray          # 单位法向
    offset: float               # 平面方程 n·p + d = 0
    sigma_m: float              # 内点残差标准差（噪声底）
    inlier_ratio: float
    sample_count: int


@dataclass
class Protrusion:
    """一块高出地面的区域。"""
    bbox: BBox                  # 深度图坐标系下的包围盒
    peak_height_m: float
    mean_height_m: float
    area_px: int
    snr: float                  # peak_height / sigma

    def to_observation(self, index: int, label: str = "obstacle:geometric",
                       image_scale: float = 1.0) -> RockObservation:
        x1, y1, x2, y2 = self.bbox
        scaled = BBox(x1 * image_scale, y1 * image_scale,
                      x2 * image_scale, y2 * image_scale)
        # 置信度：3σ 起步算 0.5，6σ 及以上算 0.95，中间线性
        score = float(np.clip(0.5 + (self.snr - 3.0) / 3.0 * 0.45, 0.0, 0.95))
        return RockObservation(rock_id=f"GEO-{index + 1}", bbox=scaled,
                               score=score, label=label)


@dataclass
class GeometryResult:
    plane: GroundPlane | None
    protrusions: list[Protrusion] = field(default_factory=list)
    reason: str = "OK"
    valid_ratio: float = 0.0


def _dot(points: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """逐元素点积。显式绕开 BLAS：小矩阵走 BLAS 时每次调用有毫秒级线程调度开销，
    在 RANSAC 的几百次迭代里会被放大成十几秒。"""
    return points[:, 0] * normal[0] + points[:, 1] * normal[1] + points[:, 2] * normal[2]


def _backproject(depth: np.ndarray, fx: float, fy: float, cx: float, cy: float):
    h, w = depth.shape
    ys, xs = np.mgrid[0:h, 0:w]
    valid = np.isfinite(depth) & (depth > 0)
    z = depth[valid]
    x = (xs[valid] - cx) * z / fx
    y = (ys[valid] - cy) * z / fy
    return np.stack([x, y, z], axis=1), valid


def fit_ground_plane(depth: np.ndarray, fx: float, fy: float, cx: float, cy: float,
                     inlier_m: float = DEFAULT_INLIER_M,
                     min_valid_ratio: float = DEFAULT_MIN_VALID_RATIO,
                     iterations: int = 400, seed: int = 0) -> tuple[GroundPlane | None, str]:
    valid_ratio = float((np.isfinite(depth) & (depth > 0)).mean())
    if valid_ratio < min_valid_ratio:
        return None, "DEPTH_TOO_SPARSE"

    points, _ = _backproject(depth, fx, fy, cx, cy)
    if len(points) < 50:
        return None, "DEPTH_TOO_SPARSE"

    # 平面拟合不需要全部点：下采样到 ~4000 点足够，成本降一个量级
    stride = max(1, len(points) // 4000)
    sample = points[::stride]

    rng = np.random.default_rng(seed)
    best_mask = None
    best_count = -1
    for _ in range(iterations):
        idx = rng.integers(0, len(sample), 3)
        p0, p1, p2 = sample[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = float(np.sqrt(normal @ normal))
        if norm < 1e-9:
            continue
        normal = normal / norm
        residual = _dot(sample, normal) - float(normal @ p0)
        mask = np.abs(residual) < inlier_m
        count = int(mask.sum())
        if count > best_count:
            best_count, best_mask = count, mask

    if best_mask is None or best_mask.sum() < 50:
        return None, "PLANE_FIT_FAILED"

    subset = sample[best_mask]
    centroid = subset.mean(axis=0)
    centered = subset - centroid
    # 3x3 协方差的最小特征向量即法向：比 SVD 快且稳定
    cov = centered.T @ centered
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    normal = eigenvectors[:, int(np.argmin(eigenvalues))]
    offset = -float(normal @ centroid)
    residual_sample = _dot(sample, normal) + offset
    sigma = float(residual_sample[best_mask].std())
    if sigma <= 0:
        return None, "PLANE_FIT_FAILED"

    return GroundPlane(normal=normal, offset=offset, sigma_m=sigma,
                       inlier_ratio=float(best_count / len(sample)),
                       sample_count=best_count), "OK"


def detect_protrusions(depth: np.ndarray, plane: GroundPlane,
                       fx: float, fy: float, cx: float, cy: float,
                       min_height_m: float = DEFAULT_MIN_HEIGHT_M,
                       min_area_px: int = DEFAULT_MIN_AREA_PX,
                       sigma_floor_m: float = 0.003,
                       border_margin_px: int = DEFAULT_BORDER_MARGIN_PX) -> list[Protrusion]:
    """找出显著高于地面的连通区域。

    高度用竖直方向（相机 y 轴）而非沿法向，这样"高出路面"就是字面意义。
    """
    h, w = depth.shape
    ys, xs = np.mgrid[0:h, 0:w]
    valid = np.isfinite(depth) & (depth > 0)
    if not valid.any():
        return []

    z = depth[valid]
    x = (xs[valid] - cx) * z / fx
    y = (ys[valid] - cy) * z / fy
    points = np.stack([x, y, z], axis=1)

    # 到平面的**垂直**距离即"高出地面"的量；符号取"朝向相机"为正。
    # 相机在原点：offset<0 时原点在负侧，靠近相机的点残差更负，故取反。
    signed = points @ plane.normal + plane.offset
    height = -signed if plane.offset < 0 else signed

    sigma = max(plane.sigma_m, sigma_floor_m)
    # 逐像素用较低阈值（2σ）只是为了"连成一片"；
    # 真正的判定交给连通域的**均值高度**与**峰值信噪比**，
    # 否则 10 mm 的物体在 σ=2 mm 噪声下会被逐像素阈值打散。
    pixel_threshold = max(2.0 * sigma, 0.004)

    mask = np.zeros((h, w), dtype=np.uint8)
    above = height > pixel_threshold
    if not above.any():
        return []
    mask[valid] = above.astype(np.uint8)

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    height_map = np.full((h, w), -np.inf)
    height_map[valid] = height

    results: list[Protrusion] = []
    for i in range(1, count):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area_px:
            continue
        x0, y0 = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        # 贴边连通域一律丢弃：边缘深度不可靠，实测会稳定误报
        if (x0 <= border_margin_px or y0 <= border_margin_px
                or x0 + bw >= w - border_margin_px
                or y0 + bh >= h - border_margin_px):
            continue
        region = labels == i
        peak = float(height_map[region].max())
        mean = float(height_map[region].mean())
        # 判定：均值达到最小高度，且峰值显著高于噪声
        if mean < min_height_m or peak < 3.0 * sigma:
            continue
        results.append(Protrusion(bbox=(float(x0), float(y0), float(x0 + bw), float(y0 + bh)),
                                  peak_height_m=peak, mean_height_m=mean,
                                  area_px=area, snr=peak / sigma))
    results.sort(key=lambda p: p.snr, reverse=True)
    return results


def detect_obstacles(depth: np.ndarray, fx: float, fy: float, cx: float, cy: float,
                     min_height_m: float = DEFAULT_MIN_HEIGHT_M,
                     min_area_px: int = DEFAULT_MIN_AREA_PX,
                     border_margin_px: int = DEFAULT_BORDER_MARGIN_PX,
                     image_scale: float = 1.0,
                     label: str = "obstacle:geometric") -> GeometryResult:
    """一步到位：拟合地面 → 找凸起 → 转成 RockObservation 可用的结构。"""
    valid_ratio = float((np.isfinite(depth) & (depth > 0)).mean())
    plane, reason = fit_ground_plane(depth, fx, fy, cx, cy)
    if plane is None:
        return GeometryResult(plane=None, reason=reason, valid_ratio=valid_ratio)

    protrusions = detect_protrusions(depth, plane, fx, fy, cx, cy,
                                     min_height_m=min_height_m,
                                     min_area_px=min_area_px,
                                     border_margin_px=border_margin_px)
    return GeometryResult(plane=plane, protrusions=protrusions, reason="OK",
                          valid_ratio=valid_ratio)


def to_observations(result: GeometryResult, image_scale: float = 1.0,
                    label: str = "obstacle:geometric") -> list[RockObservation]:
    return [p.to_observation(i, label=label, image_scale=image_scale)
            for i, p in enumerate(result.protrusions)]
