#!/usr/bin/env python3
"""AeroScapes → 二分类道路分割数据集。

类别依据（两项独立证据，均非二手资料）：
  1. 论文 Figure 2 图注给出的类别顺序：
     Background, Person, Bike, Car, Drone, Boat, Animal, Obstacle,
     Construction, Vegetation, Road, Sky
  2. 官方 Visualizations 的颜色反查 + 逐类目视确认（行人/汽车/杆件/植被/道路/天空）

映射：Road -> 前景(255)，其余一切 -> 背景(0)。
      背景(Background, 0) 也映射为 0 —— **任何未知/未标注像素都不会变成道路**。

用法:
    perception/.venv/bin/python perception/tools/prepare_aeroscapes.py \
        --raw data/aeroscapes/raw/aeroscapes --out data/aeroscapes/processed
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

# 论文 Figure 2 的类别顺序（索引即 mask 像素值）
CLASSES = [
    "Background", "Person", "Bike", "Car", "Drone", "Boat",
    "Animal", "Obstacle", "Construction", "Vegetation", "Road", "Sky",
]
ROAD_ID = CLASSES.index("Road")          # 10
BACKGROUND_ID = CLASSES.index("Background")  # 0
NONROAD_IDS = [i for i in range(len(CLASSES)) if i != ROAD_ID]


def sequence_of(stem: str) -> str:
    """000001_003 -> 000001。官方文件名的前缀就是视频序列号。"""
    return stem.split("_")[0]


def inspect_structure(raw: Path) -> dict:
    images = sorted((raw / "JPEGImages").glob("*.jpg"))
    masks = sorted((raw / "SegmentationClass").glob("*.png"))
    visuals = sorted((raw / "Visualizations").glob("*.png"))
    img_stems = {p.stem for p in images}
    mask_stems = {p.stem for p in masks}
    vis_stems = {p.stem for p in visuals}

    trn = [l.strip() for l in (raw / "ImageSets" / "trn.txt").read_text().splitlines() if l.strip()]
    val = [l.strip() for l in (raw / "ImageSets" / "val.txt").read_text().splitlines() if l.strip()]

    trn_seq = {sequence_of(s) for s in trn}
    val_seq = {sequence_of(s) for s in val}

    problems = []
    if img_stems != mask_stems:
        problems.append(f"图像与 mask stem 不一致: 仅图像 {len(img_stems-mask_stems)}，"
                        f"仅 mask {len(mask_stems-img_stems)}")
    if img_stems != vis_stems:
        problems.append("图像与可视化 stem 不一致")
    if (set(trn) | set(val)) != img_stems:
        problems.append("官方划分未覆盖全部图像")

    return {
        "images": len(images), "masks": len(masks), "visualizations": len(visuals),
        "official_train": len(trn), "official_val": len(val),
        "sequences_total": len({sequence_of(s) for s in img_stems}),
        "sequences_train": len(trn_seq), "sequences_val": len(val_seq),
        "sequence_overlap_train_val": len(trn_seq & val_seq),
        "problems": problems,
        "trn": trn, "val": val,
    }


def class_statistics(raw: Path, stems: list[str]) -> dict:
    pixels = defaultdict(int)
    image_hits = defaultdict(int)
    total = 0
    for stem in stems:
        m = cv2.imread(str(raw / "SegmentationClass" / f"{stem}.png"), cv2.IMREAD_UNCHANGED)
        if m is None:
            continue
        unique, counts = np.unique(m, return_counts=True)
        for value, count in zip(unique.tolist(), counts.tolist()):
            pixels[value] += count
            image_hits[value] += 1
        total += m.size

    rows = []
    for class_id in range(len(CLASSES)):
        rows.append({
            "id": class_id,
            "name": CLASSES[class_id],
            "role": "road" if class_id == ROAD_ID else "nonroad",
            "pixels": pixels.get(class_id, 0),
            "pixel_ratio": round(pixels.get(class_id, 0) / total, 6) if total else 0.0,
            "images": image_hits.get(class_id, 0),
        })
    unexpected = sorted(set(pixels) - set(range(len(CLASSES))))
    return {"total_pixels": total, "classes": rows, "unexpected_values": unexpected}


def split_sequences(stats: dict, seed: int, test_fraction: float) -> dict:
    """train = 官方 trn 的全部序列；官方 val 的序列按比例切成 val / test。

    这样 train/val/test 三者序列互不重叠，杜绝同一段视频跨集合泄漏。
    """
    trn_seq = sorted({sequence_of(s) for s in stats["trn"]})
    val_seq = sorted({sequence_of(s) for s in stats["val"]})

    rng = random.Random(seed)
    shuffled = val_seq[:]
    rng.shuffle(shuffled)
    n_test = max(1, int(round(len(shuffled) * test_fraction)))
    test_seq = sorted(shuffled[:n_test])
    inner_val_seq = sorted(shuffled[n_test:])

    def collect(sequences: set[str], source: list[str]) -> list[str]:
        return [s for s in source if sequence_of(s) in sequences]

    return {
        "train": collect(set(trn_seq), stats["trn"]),
        "val": collect(set(inner_val_seq), stats["val"]),
        "test": collect(set(test_seq), stats["val"]),
        "sequences": {"train": trn_seq, "val": inner_val_seq, "test": test_seq},
    }


def write_binary_masks(raw: Path, out: Path, splits: dict) -> dict:
    """生成 0/255 二分类 mask；原始多分类 mask 完全不动。"""
    counts = {}
    for split in ("train", "val", "test"):
        stems = splits[split]
        target = out / "masks" / split
        target.mkdir(parents=True, exist_ok=True)
        empty = 0
        for stem in stems:
            src = raw / "SegmentationClass" / f"{stem}.png"
            mask = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
            if mask is None:
                raise FileNotFoundError(src)
            binary = np.where(mask == ROAD_ID, 255, 0).astype(np.uint8)
            if binary.max() == 0:
                empty += 1
            cv2.imwrite(str(target / f"{stem}.png"), binary)
        counts[split] = {"images": len(stems), "no_road_frames": empty}
    return counts


def link_images(raw: Path, out: Path, splits: dict) -> None:
    """图像用软链接，避免复制 700 MB。"""
    for split in ("train", "val", "test"):
        stems = splits[split]
        target = out / "images" / split
        target.mkdir(parents=True, exist_ok=True)
        for stem in stems:
            src = (raw / "JPEGImages" / f"{stem}.jpg").resolve()
            dst = target / f"{stem}.jpg"
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(src, dst)


def make_visualizations(raw: Path, out: Path, splits: dict, count: int, seed: int) -> list[str]:
    directory = out.parent / "visualizations"
    directory.mkdir(parents=True, exist_ok=True)
    pool = [(split, stem) for split, stems in splits.items() for stem in stems]
    rng = random.Random(seed)
    picked = rng.sample(pool, min(count, len(pool)))

    written = []
    for split, stem in picked:
        rgb = cv2.imread(str(raw / "JPEGImages" / f"{stem}.jpg"))
        gt = cv2.imread(str(raw / "SegmentationClass" / f"{stem}.png"), cv2.IMREAD_UNCHANGED)
        road = (gt == ROAD_ID).astype(np.uint8)

        gt_vis = np.zeros_like(rgb)
        gt_vis[road == 1] = (255, 255, 255)
        gt_vis = np.where(road[..., None] == 1, gt_vis, rgb // 4)

        overlay = rgb.copy()
        overlay[road == 1] = (0.45 * overlay[road == 1] + 0.55 * np.array([0, 255, 0])).astype(np.uint8)

        panel = np.hstack([rgb, gt_vis, overlay])
        cv2.putText(panel, f"{stem}  [{split}]  road={road.mean()*100:.1f}%",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        path = directory / f"{split}_{stem}.jpg"
        cv2.imwrite(str(path), panel, [cv2.IMWRITE_JPEG_QUALITY, 88])
        written.append(str(path))
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("data/aeroscapes/raw/aeroscapes"))
    ap.add_argument("--out", type=Path, default=Path("data/aeroscapes/processed"))
    ap.add_argument("--test-fraction", type=float, default=0.4,
                    help="从官方 val 序列中划出多少比例作为 test")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--visualize", type=int, default=30)
    ap.add_argument("--skip-masks", action="store_true")
    args = ap.parse_args()

    if not (args.raw / "JPEGImages").exists():
        raise SystemExit(f"原始目录不存在: {args.raw}")

    print("== 1/5 结构校验 ==")
    stats = inspect_structure(args.raw)
    print(f"  图像 {stats['images']} | mask {stats['masks']} | 可视化 {stats['visualizations']}")
    print(f"  官方 trn {stats['official_train']} ({stats['sequences_train']} 序列) | "
          f"val {stats['official_val']} ({stats['sequences_val']} 序列)")
    print(f"  序列总数 {stats['sequences_total']} | trn/val 序列交集 {stats['sequence_overlap_train_val']}")
    if stats["problems"]:
        for problem in stats["problems"]:
            print(f"  [问题] {problem}")

    print("== 2/5 类别统计 ==")
    classes = class_statistics(args.raw, [p.stem for p in
                                          sorted((args.raw / "JPEGImages").glob("*.jpg"))])
    for row in classes["classes"]:
        print(f"  {row['id']:>2} {row['name']:<13} {row['pixel_ratio']*100:>6.2f}%  "
              f"{row['images']:>5} 张  {'<- ROAD' if row['role'] == 'road' else ''}")
    if classes["unexpected_values"]:
        print(f"  [问题] 出现未定义的类别值: {classes['unexpected_values']}")

    print("== 3/5 划分（按视频序列分组）==")
    splits = split_sequences(stats, args.seed, args.test_fraction)
    for split in ("train", "val", "test"):
        print(f"  {split:<5} {len(splits[split]):>5} 张  "
              f"{len(splits['sequences'][split]):>3} 序列")
    overlap = (set(splits["sequences"]["train"]) & set(splits["sequences"]["val"])) | \
              (set(splits["sequences"]["train"]) & set(splits["sequences"]["test"])) | \
              (set(splits["sequences"]["val"]) & set(splits["sequences"]["test"]))
    print(f"  三集合序列交集: {len(overlap)} {'（无泄漏）' if not overlap else '（有泄漏！）'}")

    print("== 4/5 生成二分类 mask 与图像链接 ==")
    args.out.mkdir(parents=True, exist_ok=True)
    mask_counts = {} if args.skip_masks else write_binary_masks(args.raw, args.out, splits)
    link_images(args.raw, args.out, splits)
    print(f"  完成，mask 目录 {args.out/'masks'}")

    print("== 5/5 可视化与报告 ==")
    visuals = make_visualizations(args.raw, args.out, splits, args.visualize, args.seed)
    print(f"  {len(visuals)} 组对照图 -> {args.out.parent/'visualizations'}")

    class_map = {
        "source": "AeroScapes (Nigam, Huang, Ramanan, WACV 2018)",
        "license": "CC BY-SA 4.0",
        "evidence": ["paper Figure 2 caption", "Visualizations color reverse-lookup",
                     "per-class visual confirmation"],
        "classes": {c["name"]: c["id"] for c in classes["classes"]},
        "binary": {"road": [ROAD_ID], "nonroad": NONROAD_IDS},
        "note": "Background(0) 视为非道路；任何未定义像素也映射为 0，绝不映射为道路",
    }
    (args.out / "class_map.json").write_text(
        json.dumps(class_map, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "raw": str(args.raw), "out": str(args.out),
        "structure": {k: v for k, v in stats.items() if k not in ("trn", "val")},
        "class_statistics": classes,
        "split": {s: {"images": len(splits[s]), "sequences": len(splits["sequences"][s])}
                  for s in ("train", "val", "test")},
        "sequence_overlap": sorted(overlap),
        "mask_counts": mask_counts,
        "seed": args.seed, "test_fraction": args.test_fraction,
        "visualizations": visuals,
    }
    (args.out / "dataset_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for split in ("train", "val", "test"):
        (args.out / f"{split}.txt").write_text("\n".join(splits[split]), encoding="utf-8")

    print(f"\n报告: {args.out/'dataset_report.json'}")
    print(f"类别映射: {args.out/'class_map.json'}")
    return 0 if not stats["problems"] and not classes["unexpected_values"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
