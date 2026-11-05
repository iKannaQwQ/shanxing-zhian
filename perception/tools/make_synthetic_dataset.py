#!/usr/bin/env python3
"""生成合成沙盘数据，用于在真实数据到位前验证训练管线。

生成内容：
  * 弯曲路面（灰度多边形）
  * 若干落石（深色椭圆）
  * 道路掩码（掩码只含路面，不含落石）
  * YOLO 检测标签（落石外接框）

这些图**不是**真实采集，只用于跑通 prepare → train → predict 这条链路。
真实数据到位后直接替换 data/sandbox/raw 即可。

用法:
    perception/.venv/bin/python perception/tools/make_synthetic_dataset.py \
        --out data/sandbox/raw --count 24
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np


def make_sample(rng: random.Random, width: int, height: int):
    image = np.full((height, width, 3), 0, dtype=np.uint8)
    # 背景：偏绿的桌面色，带噪声
    base = np.array([60, 90, 70], dtype=np.int16)
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 6, (height, width, 3))
    image = np.clip(base + noise, 0, 255).astype(np.uint8)

    # 道路：一条带弯曲的带状区域
    mask = np.zeros((height, width), dtype=np.uint8)
    centre = width * rng.uniform(0.4, 0.6)
    road_half = width * rng.uniform(0.14, 0.20)
    points = []
    for y in range(0, height, 8):
        offset = (y / height) ** 1.5 * width * rng.uniform(-0.12, 0.12)
        points.append((int(centre + offset), y))
    left = [(int(x - road_half), y) for x, y in points]
    right = [(int(x + road_half), y) for x, y in reversed(points)]
    cv2.fillPoly(mask, [np.array(left + right, dtype=np.int32)], 1)
    image[mask == 1] = (140, 140, 145)

    labels = []
    for _ in range(rng.randint(0, 4)):
        cy = rng.randint(int(height * 0.35), height - 6)
        row = [p for p in points if abs(p[1] - cy) < 6]
        if not row:
            continue
        cx_center = row[0][0]
        cx = int(np.clip(cx_center + rng.uniform(-road_half, road_half),
                         road_half * 0.3, width - road_half * 0.3))
        radius = rng.randint(6, 14)
        cv2.circle(image, (cx, cy), radius, (40, 42, 48), -1)
        x1, y1 = max(0, cx - radius), max(0, cy - radius)
        x2, y2 = min(width, cx + radius), min(height, cy + radius)
        labels.append((0, ((x1 + x2) / 2) / width, ((y1 + y2) / 2) / height,
                       (x2 - x1) / width, (y2 - y1) / height))

    return image, mask * 255, labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/sandbox/raw"))
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    for sub in ("images", "masks", "labels"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    for index in range(args.count):
        image, mask, labels = make_sample(rng, args.width, args.height)
        stem = f"syn_{index:04d}"
        cv2.imwrite(str(args.out / "images" / f"{stem}.jpg"), image)
        cv2.imwrite(str(args.out / "masks" / f"{stem}.png"), mask)
        (args.out / "labels" / f"{stem}.txt").write_text(
            "\n".join(f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for c, x, y, w, h in labels),
            encoding="utf-8")

    print(f"已生成 {args.count} 帧 -> {args.out}")
    print("注意：这是合成数据，只用于验证管线，不能用来评估真实性能。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
