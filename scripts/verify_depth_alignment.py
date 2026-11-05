#!/usr/bin/env python3
"""深度 ↔ RGB 对齐验证。

问题：契约只保证"同源共网格 + 分辨率不同"，但**没有证明**深度像素 (x,y)
真的对应 RGB 像素 (5x, 5y)。若存在系统性偏移或缩放误差，所有米制结论都会错位，
而画面上看不出来。

做法：
  1. 在深度图上求梯度，取梯度最大的若干像素作为"几何边缘"；
  2. 把这些边缘按比例映射到 RGB 分辨率；
  3. 统计这些位置上的 RGB 梯度均值，与全图均值相比得到「富集倍数」；
  4. 扫描偏移量，找富集最大的位置。

判读：
  * 最佳偏移接近 (0,0) 且富集明显 > 1  -> 对齐成立；
  * 最佳偏移远离 (0,0)                  -> 存在系统性偏移，米制结论不可信。

用法:
    perception/.venv/bin/python scripts/verify_depth_alignment.py /tmp/vb_check
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def load_frame(directory: Path):
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    rgb = cv2.imread(str(directory / "rgb.jpg"))
    if rgb is None:
        return None
    d = meta["depth"]
    dh, dw = d["height"], d["width"]
    bpr = (directory / "depth.bin").stat().st_size // dh
    floats_per_row = bpr // 4
    depth = np.fromfile(directory / "depth.bin", dtype=np.float32,
                        count=floats_per_row * dh).reshape(dh, floats_per_row)[:, :dw]
    return meta, rgb, depth


def depth_edge_mask(depth: np.ndarray, quantile: float = 0.90) -> np.ndarray:
    """深度梯度最大的像素。无效值不参与。"""
    valid = np.isfinite(depth) & (depth > 0)
    filled = np.where(valid, depth, np.nanmedian(depth[valid]) if valid.any() else 0)
    gx = cv2.Sobel(filled, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(filled, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.hypot(gx, gy)
    magnitude[~valid] = 0
    if magnitude.max() <= 0:
        return np.zeros_like(magnitude, dtype=bool)
    threshold = np.quantile(magnitude[valid], quantile) if valid.any() else 0
    return magnitude >= max(threshold, 1e-6)


def rgb_gradient(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy)


def enrichment(rgb_grad: np.ndarray, edge_mask: np.ndarray,
               dx: int = 0, dy: int = 0) -> float:
    """边缘位置上的 RGB 梯度均值 / 全图均值。"""
    base = float(rgb_grad.mean())
    if base <= 0 or not edge_mask.any():
        return 0.0
    shifted = np.roll(np.roll(edge_mask, dy, axis=0), dx, axis=1)
    if not shifted.any():
        return 0.0
    return float(rgb_grad[shifted].mean()) / base


def verify_directory(directory: Path, search: int = 16, step: int = 2):
    loaded = load_frame(directory)
    if loaded is None:
        return None
    meta, rgb, depth = loaded

    dh, dw = depth.shape
    rh, rw = rgb.shape[:2]
    scale_x, scale_y = rw / dw, rh / dh

    edges = depth_edge_mask(depth)
    # 最近邻放大到 RGB 分辨率：深度边缘本来就是块状的，不做插值以免糊边
    edges_rgb = cv2.resize(edges.astype(np.uint8), (rw, rh),
                           interpolation=cv2.INTER_NEAREST).astype(bool)
    grad = rgb_gradient(rgb)

    at_zero = enrichment(grad, edges_rgb, 0, 0)
    best = (-1.0, 0, 0)
    for dy in range(-search, search + 1, step):
        for dx in range(-search, search + 1, step):
            value = enrichment(grad, edges_rgb, dx, dy)
            if value > best[0]:
                best = (value, dx, dy)

    return {
        "frame": directory.name[:8],
        "rgb": f"{rw}x{rh}",
        "depth": f"{dw}x{dh}",
        "scale": (round(scale_x, 4), round(scale_y, 4)),
        "edge_px": int(edges_rgb.sum()),
        "enrich_at_zero": round(at_zero, 3),
        "best_enrich": round(best[0], 3),
        "best_offset": (best[1], best[2]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="FrameBundles 目录")
    ap.add_argument("--search", type=int, default=16, help="偏移搜索范围（RGB 像素）")
    ap.add_argument("--step", type=int, default=2)
    args = ap.parse_args()

    directories = sorted(p for p in args.path.iterdir()
                         if p.is_dir() and (p / "metadata.json").exists())
    if not directories:
        raise SystemExit(f"{args.path} 下没有 frame_* 目录")

    rows = []
    for directory in directories:
        try:
            row = verify_directory(directory, args.search, args.step)
        except Exception as exc:  # noqa: BLE001
            print(f"跳过 {directory.name}: {exc}")
            continue
        if row:
            rows.append(row)

    print(f"{'帧':<10}{'RGB':>10}{'深度':>9}{'缩放':>12}{'边缘像素':>9}"
          f"{'0偏移富集':>10}{'最佳富集':>9}{'最佳偏移':>11}")
    for row in rows:
        print(f"{row['frame']:<10}{row['rgb']:>10}{row['depth']:>9}"
              f"{str(row['scale']):>12}{row['edge_px']:>9}"
              f"{row['enrich_at_zero']:>10.2f}{row['best_enrich']:>9.2f}"
              f"{str(row['best_offset']):>11}")

    if not rows:
        return 1

    offsets = np.array([r["best_offset"] for r in rows], dtype=float)
    zero_enrich = np.array([r["enrich_at_zero"] for r in rows])
    best_enrich = np.array([r["best_enrich"] for r in rows])

    print()
    print(f"帧数            {len(rows)}")
    print(f"最佳偏移 中位数  ({np.median(offsets[:,0]):.0f}, {np.median(offsets[:,1]):.0f}) px")
    print(f"最佳偏移 标准差  ({offsets[:,0].std():.1f}, {offsets[:,1].std():.1f}) px")
    print(f"富集 @0偏移     中位数 {np.median(zero_enrich):.2f}")
    print(f"富集 @最佳偏移  中位数 {np.median(best_enrich):.2f}")
    print(f"达到最佳所需位移 中位数 {np.median(np.abs(offsets).max(axis=1)):.0f} px")

    drift = float(np.median(np.abs(offsets).max(axis=1)))
    print()
    if drift <= max(2, args.step):
        print("结论：最佳偏移与 0 重合，**按比例缩放的对齐假设成立**。")
    else:
        print(f"结论：最佳偏移约 {drift:.0f} px，**存在系统性偏移，需要显式配准**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
