#!/usr/bin/env python3
"""实测 LiDAR 深度在平面上的噪声，判断"几何异常检测"是否可行。

思路：固定俯视相机下，路面近似一个平面。若平面拟合残差的噪声远小于
目标障碍物高度（如 2 cm），则用"偏离平面"找障碍物是可行的，且不需要标注数据。

用法:
    perception/.venv/bin/python scripts/measure_depth_plane_noise.py --latest
    perception/.venv/bin/python scripts/measure_depth_plane_noise.py --frame-dir data/artifacts/<id>

输出:
    - 有效像素比例
    - 工作距离分位
    - 平面拟合内点比例、残差标准差与分位
    - 合成凸起（5/10/20/30 mm）的可检出性与信噪比
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def load_depth(frame_dir: Path) -> np.ndarray:
    meta = json.loads((frame_dir / "metadata.json").read_text(encoding="utf-8"))
    h, w = meta["depth"]["height"], meta["depth"]["width"]
    raw = (frame_dir / "depth.bin").read_bytes()
    floats_per_row = (len(raw) // h) // 4
    return np.frombuffer(raw, dtype=np.float32, count=floats_per_row * h) \
        .reshape(h, floats_per_row)[:, :w].astype(np.float64)


def points_from_depth(depth: np.ndarray, fx: float, fy: float, cx: float, cy: float):
    h, w = depth.shape
    ys, xs = np.mgrid[0:h, 0:w]
    valid = np.isfinite(depth) & (depth > 0)
    z = depth[valid]
    x = (xs[valid] - cx) * z / fx
    y = (ys[valid] - cy) * z / fy
    return np.stack([x, y, z], axis=1), valid


def fit_plane_ransac(points: np.ndarray, threshold: float = 0.005,
                     iterations: int = 600, seed: int = 0):
    """返回 (法向, 偏移, 内点掩码)；平面方程 n·p + d = 0。"""
    rng = np.random.default_rng(seed)
    best_inliers = None
    n_points = len(points)
    for _ in range(iterations):
        idx = rng.choice(n_points, 3, replace=False)
        p0, p1, p2 = points[idx]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        offset = -normal.dot(p0)
        residual = np.abs(points @ normal + offset)
        inliers = residual < threshold
        if best_inliers is None or inliers.sum() > best_inliers.sum():
            best_inliers = inliers
    if best_inliers is None or best_inliers.sum() < 30:
        return None, None, None
    # 最小二乘精修
    subset = points[best_inliers]
    centroid = subset.mean(axis=0)
    _, _, vh = np.linalg.svd(subset - centroid)
    normal = vh[-1]
    offset = -normal.dot(centroid)
    residual = np.abs(points @ normal + offset)
    return normal, offset, residual < threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--frame-dir", type=Path, default=None)
    ap.add_argument("--threshold", type=float, default=0.005, help="内点阈值（米）")
    args = ap.parse_args()

    if args.frame_dir:
        frame_dir = args.frame_dir
    else:
        candidates = [p for p in (ROOT / "data" / "artifacts").iterdir()
                      if p.is_dir() and (p / "depth.bin").exists()]
        if not candidates:
            raise SystemExit("没有可用于分析的深度帧（data/artifacts 为空）")
        frame_dir = max(candidates, key=lambda p: (p / "depth.bin").stat().st_mtime)

    meta = json.loads((frame_dir / "metadata.json").read_text(encoding="utf-8"))
    i = meta["intrinsics"]
    depth = load_depth(frame_dir)

    # 深度图与 RGB 分辨率不同，内参需按比例缩放
    scale = depth.shape[1] / float(i["reference_width"])
    fx, fy = i["fx"] * scale, i["fy"] * scale
    cx, cy = i["cx"] * scale, i["cy"] * scale

    valid_ratio = float((np.isfinite(depth) & (depth > 0)).mean())
    values = depth[np.isfinite(depth) & (depth > 0)]

    points, _ = points_from_depth(depth, fx, fy, cx, cy)
    normal, offset, inliers = fit_plane_ransac(points, args.threshold)
    if normal is None:
        raise SystemExit("平面拟合失败（有效点太少或场景非平面）")

    residual = points @ normal + offset
    inlier_residual = residual[inliers]
    sigma = float(inlier_residual.std())
    p95 = float(np.percentile(np.abs(inlier_residual), 95))

    print(f"帧           {frame_dir.name[:8]}")
    print(f"深度网格     {depth.shape[1]}x{depth.shape[0]}  有效 {valid_ratio*100:.1f}%")
    if values.size:
        print(f"工作距离     {values.min()*1000:.0f} ~ {values.max()*1000:.0f} mm "
              f"(中位 {np.median(values)*1000:.0f} mm)")
    print(f"平面内点     {inliers.mean()*100:.1f}%  (阈值 {args.threshold*1000:.0f} mm)")
    print(f"残差标准差   {sigma*1000:.2f} mm")
    print(f"残差 P95     {p95*1000:.2f} mm")

    print("\n合成凸起可检出性（以 3σ 为检测门限）:")
    print(f"  {'高度':>6}{'信噪比':>10}{'相对 P95':>12}  判定")
    for height_mm in (5, 10, 20, 30):
        height = height_mm / 1000.0
        snr = height / sigma if sigma > 0 else float("inf")
        ratio = height / p95 if p95 > 0 else float("inf")
        verdict = "可检出" if snr >= 3 and ratio >= 1.5 else "边缘" if snr >= 2 else "不可靠"
        print(f"  {height_mm:>5}mm{snr:>9.1f}σ{ratio:>11.1f}x  {verdict}")

    print("\n判读：σ 是这套深度数据的噪声底。目标障碍物高度应至少达到 3σ 且"
          "超过 P95 残差的 1.5 倍，才能在固定阈值下稳定检出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
