#!/usr/bin/env python3
"""校验 iOS 采集端落盘的 FrameBundle 目录（M4/M6）。

对每个 frame_* 目录检查：
  * metadata.json 是否符合 contracts/schemas/frame_bundle.schema.json
  * rgb.jpg 能否解码，尺寸是否与 metadata 一致
  * depth.bin 大小是否等于 bytesPerRow × height，数值是否合理
  * confidence.bin 大小是否等于 width × height，取值是否落在 ARConfidenceLevel
  * 三者的 frame 内一致性（同一帧、同一尺寸关系）

用法:
    python scripts/verify_frame_bundle.py <目录>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "frame_bundle.schema.json"

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    sys.exit("缺少 jsonschema：请用 perception/.venv/bin/python 运行")


def verify_one(directory: Path, validator: Draft202012Validator) -> list[str]:
    errors: list[str] = []
    meta_path = directory / "metadata.json"
    jpg_path = directory / "rgb.jpg"
    depth_path = directory / "depth.bin"
    conf_path = directory / "confidence.bin"

    for required in (meta_path, jpg_path, depth_path):
        if not required.exists():
            errors.append(f"缺少文件: {required.name}")
    if errors:
        return errors

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"metadata.json 解析失败: {exc}"]

    for err in validator.iter_errors(meta):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"[schema] {path}: {err.message}")
    if errors:
        return errors

    # --- RGB ---
    image = cv2.imread(str(jpg_path))
    if image is None:
        errors.append("rgb.jpg 无法解码")
    else:
        h, w = image.shape[:2]
        cap = meta["capture"]
        if (w, h) != (cap["width"], cap["height"]):
            errors.append(f"rgb.jpg 尺寸 {w}x{h} 与 metadata {cap['width']}x{cap['height']} 不符")
        if jpg_path.stat().st_size != cap["bytes"]:
            errors.append(f"rgb.jpg 实际 {jpg_path.stat().st_size} 字节，metadata 记录 {cap['bytes']}")

    # --- 深度 ---
    d = meta["depth"]
    expected_depth = d["width"] * d["height"] * 4  # float32
    actual_depth = depth_path.stat().st_size
    if actual_depth < expected_depth:
        errors.append(f"depth.bin {actual_depth} 字节，小于 {d['width']}x{d['height']}x4={expected_depth}")
    else:
        raw = np.fromfile(depth_path, dtype=np.float32)
        values = raw[: d["width"] * d["height"] * (actual_depth // expected_depth)]
        valid = values[np.isfinite(values) & (values > 0)]
        if valid.size == 0:
            errors.append("depth.bin 没有任何有效深度值")
        else:
            ratio = valid.size / values.size
            if valid.min() < 0.05 or valid.max() > 10.0:
                errors.append(f"深度范围异常: {valid.min():.3f}~{valid.max():.3f} m")
            print(f"      深度: {int(ratio * 100)}% 有效, {valid.min():.3f}~{valid.max():.3f} m")

    # --- 置信度 ---
    if conf_path.exists():
        c = meta.get("confidence", {})
        expected_conf = c.get("width", 0) * c.get("height", 0)
        actual_conf = conf_path.stat().st_size
        if expected_conf and actual_conf != expected_conf:
            errors.append(f"confidence.bin {actual_conf} 字节，期望 {expected_conf}")
        else:
            values = np.fromfile(conf_path, dtype=np.uint8)
            unique = np.unique(values)
            if not set(unique.tolist()).issubset({0, 1, 2}):
                errors.append(f"置信度取值不是 ARConfidenceLevel(0/1/2): {unique[:10].tolist()}")
            counts = np.bincount(values, minlength=3)[:3]
            total = values.size
            print("      置信度: " + " / ".join(
                f"{name} {counts[i] * 100 // total}%"
                for i, name in enumerate(("低", "中", "高"))))
    else:
        errors.append("缺少 confidence.bin")

    # --- 跨字段一致性 ---
    intr = meta["intrinsics"]
    if (intr["reference_width"], intr["reference_height"]) != (
        meta["capture"]["width"], meta["capture"]["height"]
    ):
        errors.append("内参参考分辨率与图像尺寸不一致")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="FrameBundles 根目录或其下的某个 frame_* 目录")
    args = ap.parse_args()

    validator = Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        format_checker=FormatChecker())

    target = args.path
    if not target.exists():
        sys.exit(f"路径不存在: {target}")

    directories = [target] if (target / "metadata.json").exists() else sorted(
        p for p in target.iterdir() if p.is_dir() and (p / "metadata.json").exists())

    if not directories:
        sys.exit(f"在 {target} 下没找到 frame_* 目录")

    failed = 0
    for directory in directories:
        errors = verify_one(directory, validator)
        status = "✓" if not errors else "✗"
        print(f"{status} {directory.name}")
        for err in errors:
            print(f"      - {err}")
        failed += 1 if errors else 0

    print(f"\n{len(directories) - failed}/{len(directories)} 帧通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
