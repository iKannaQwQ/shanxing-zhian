#!/usr/bin/env python3
"""用已知高度的台阶测量 LiDAR 深度精度。

做法：把**已知厚度的物体放在画面正中央**，手机垂直向下拍。
比较"中心区域"与"外围环形区域"的深度差，即为测得的台阶高度。

用法:
    # 测量最新一帧
    perception/.venv/bin/python scripts/measure_depth_step.py --latest --height-mm 20

    # 测量指定帧目录
    perception/.venv/bin/python scripts/measure_depth_step.py \
        --frame-dir data/artifacts/<frame_id> --height-mm 20

判读：
    误差 = 测量值 - 实际值，±3 mm 内算正常（取决于机型与距离）。

前提：
    * 手机**垂直向下**，正对**平整**表面（桌面/沙盘），否则中心与外围的
      深度差会混入场景本身的起伏，测出来的不是台阶。
    * 台阶物体放在**画面正中央**，占画面比例明显大于 --center 区域。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def load_depth(frame_dir: Path) -> tuple[np.ndarray, dict]:
    metadata = json.loads((frame_dir / "metadata.json").read_text(encoding="utf-8"))
    depth_meta = metadata["depth"]
    height, width = depth_meta["height"], depth_meta["width"]
    raw_bytes = (frame_dir / "depth.bin").read_bytes()
    floats_per_row = (len(raw_bytes) // height) // 4
    depth = np.frombuffer(raw_bytes, dtype=np.float32,
                          count=floats_per_row * height).reshape(height, floats_per_row)[:, :width]
    return depth.copy(), metadata


def latest_frame() -> Path:
    root = ROOT / "data" / "artifacts"
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "depth.bin").exists()]
    if not candidates:
        raise SystemExit(f"{root} 下没有含 depth.bin 的帧")
    return max(candidates, key=lambda p: (p / "depth.bin").stat().st_mtime)


def analyze(depth: np.ndarray, center_ratio: float, ring_ratio: float):
    height, width = depth.shape
    valid = np.isfinite(depth) & (depth > 0)

    cy, cx = height / 2, width / 2
    ch, cw = height * center_ratio / 2, width * center_ratio / 2
    y, x = np.ogrid[:height, :width]
    center_mask = (np.abs(y - cy) <= ch) & (np.abs(x - cx) <= cw)

    rh, rw = height * ring_ratio / 2, width * ring_ratio / 2
    ring_mask = (np.abs(y - cy) > rh) | (np.abs(x - cx) > rw)

    center = depth[center_mask & valid]
    ring = depth[ring_mask & valid]
    if center.size < 20 or ring.size < 20:
        raise SystemExit("有效像素太少，无法测量")

    return {
        "center_median": float(np.median(center)),
        "center_iqr": float(np.percentile(center, 75) - np.percentile(center, 25)),
        "ring_median": float(np.median(ring)),
        "ring_iqr": float(np.percentile(ring, 75) - np.percentile(ring, 25)),
        "center_valid": float(center.size / max(1, center_mask.sum())),
        "ring_valid": float(ring.size / max(1, ring_mask.sum())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", action="store_true", help="取 data/artifacts 里最新一帧")
    ap.add_argument("--frame-dir", type=Path, default=None)
    ap.add_argument("--height-mm", type=float, default=None,
                    help="台阶实际高度（直尺量），给出后会计算误差")
    ap.add_argument("--center", type=float, default=0.30,
                    help="中心区域占画面边长的比例，默认 0.30")
    ap.add_argument("--ring", type=float, default=0.80,
                    help="外围环形内边界比例，默认 0.80")
    args = ap.parse_args()

    if args.frame_dir:
        frame_dir = args.frame_dir
    elif args.latest:
        frame_dir = latest_frame()
    else:
        frame_dir = latest_frame()

    depth, metadata = load_depth(frame_dir)
    stats = analyze(depth, args.center, args.ring)

    measured_mm = (stats["ring_median"] - stats["center_median"]) * 1000

    print(f"帧目录      {frame_dir.name[:8]}")
    print(f"深度网格    {depth.shape[1]}x{depth.shape[0]}")
    print(f"中心深度    {stats['center_median']*1000:8.1f} mm  (IQR {stats['center_iqr']*1000:.1f} mm, "
          f"有效 {stats['center_valid']*100:.0f}%)")
    print(f"外围深度    {stats['ring_median']*1000:8.1f} mm  (IQR {stats['ring_iqr']*1000:.1f} mm, "
          f"有效 {stats['ring_valid']*100:.0f}%)")
    print(f"测得台阶    {measured_mm:8.1f} mm")

    if args.height_mm is not None:
        error = measured_mm - args.height_mm
        print(f"实际台阶    {args.height_mm:8.1f} mm")
        percent = f" ({error / args.height_mm * 100:+.1f}%)" if args.height_mm else ""
        print(f"误差        {error:+8.1f} mm{percent}")
        if abs(error) <= 3:
            print("结论        精度良好（±3 mm 内）")
        elif abs(error) <= 8:
            print("结论        可接受，但米制结论应保留余量")
        else:
            print("结论        误差偏大，米制结论不可信，需检查安装距离或标注方式")

    return 0


if __name__ == "__main__":
    sys.exit(main())
