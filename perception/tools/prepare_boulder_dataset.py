#!/usr/bin/env python3
"""把 HuggingFace parquet 形式的检测数据集转换成 ultralytics YOLO 目录结构。

来源: Mirali33/mb-boulder_det (CC-BY-4.0)，火星 HiRISE 巨石检测。
仅用于打通 M3 的数据/训练链路——**该数据集与本项目沙盘场景域差异极大**。

用法:
    python perception/tools/prepare_boulder_dataset.py \
        --src data/public/mb-boulder --dst data/public/mb-boulder/yolo
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pyarrow.parquet as pq

SPLITS = {"train": "train", "val": "val", "test": "test"}


def convert_split(parquet: Path, img_dir: Path, lbl_dir: Path, prefix: str) -> dict:
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    table = pq.read_table(parquet)
    stats = {"rows": table.num_rows, "images": 0, "boxes": 0, "skipped": 0,
             "clipped": 0, "dropped_degenerate": 0}

    for i, row in enumerate(table.to_pylist()):
        raw = row["image"]["bytes"]
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            stats["skipped"] += 1
            continue

        h, w = img.shape[:2]
        stem = f"{prefix}_{i:05d}"
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])

        lines = []
        bboxes = row["yolo_annotation"]["bbox"]
        cats = row["yolo_annotation"]["category"]
        for b, cat in zip(bboxes, cats):
            if cat != "boulder":
                continue
            cx, cy, bw, bh = [float(v) for v in b]
            if not all(np.isfinite([cx, cy, bw, bh])):
                continue
            # 裁到 [0,1]，并丢弃退化框
            x1 = max(0.0, min(1.0, cx - bw / 2))
            x2 = max(0.0, min(1.0, cx + bw / 2))
            y1 = max(0.0, min(1.0, cy - bh / 2))
            y2 = max(0.0, min(1.0, cy + bh / 2))
            if x2 - x1 <= 1e-4 or y2 - y1 <= 1e-4:
                stats["dropped_degenerate"] += 1
                continue
            if (x1, y1, x2, y2) != (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2):
                stats["clipped"] += 1
            lines.append(f"0 {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} "
                         f"{x2 - x1:.6f} {y2 - y1:.6f}")

        (lbl_dir / f"{stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        stats["images"] += 1
        stats["boxes"] += len(lines)

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/public/mb-boulder")
    ap.add_argument("--dst", default="data/public/mb-boulder/yolo")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    report = {}
    for split in SPLITS:
        pq_path = src / f"{split}.parquet"
        if not pq_path.exists():
            print(f"[skip] {pq_path} 不存在")
            continue
        stats = convert_split(pq_path, dst / "images" / split, dst / "labels" / split, split)
        report[split] = stats
        print(f"[{split}] {stats}")

    yaml_text = (
        f"path: {dst.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: 1\n"
        "names: ['boulder']\n"
    )
    (dst / "data.yaml").write_text(yaml_text, encoding="utf-8")
    (dst / "conversion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {dst / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
