#!/usr/bin/env python3
"""端到端演示：道路分割 + RGB-D 几何障碍 + C++ 风险评估。

用一张 AeroScapes 道路图 + 合成深度（平面 + 凸起）走完整链路，
验证"系统能否给出非 UNKNOWN 的判定"。

⚠️ 深度是**合成的**，只用于验证链路是否打通，不代表真实检测能力。

用法:
    perception/.venv/bin/python scripts/demo_geometric_obstacle.py \
        --image data/aeroscapes/processed/images/test/041005_001.jpg \
        --mask  data/aeroscapes/processed/masks/test/041005_001.png \
        --weights perception/weights/aeroscapes_unet_512_expA.pt
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "perception" / "src"))

from perception.depth.frame import DepthMap, Intrinsics  # noqa: E402
from perception.pipeline import FrameInput, PerceptionPipeline, PipelineConfig  # noqa: E402
from perception.road.unet_segmenter import UNetRoadSegmenter  # noqa: E402

DEPTH_W, DEPTH_H = 256, 192
WORKING_M = 0.64


def synthetic_depth(road_mask_small: np.ndarray, bump_mm: float,
                    bump_radius: int = 9) -> np.ndarray:
    ys, xs = np.mgrid[0:DEPTH_H, 0:DEPTH_W]
    depth = np.full((DEPTH_H, DEPTH_W), WORKING_M, dtype=np.float32)
    depth += np.random.default_rng(0).normal(0, 0.002, depth.shape).astype(np.float32)
    if bump_mm > 0:
        roi = np.argwhere(road_mask_small > 0)
        if len(roi) == 0:
            cy, cx = DEPTH_H // 2, DEPTH_W // 2
        else:
            cy, cx = roi.mean(axis=0).astype(int)
        depth[((ys - cy) ** 2 + (xs - cx) ** 2) < bump_radius ** 2] -= bump_mm / 1000.0
    return depth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--mask", type=Path, required=True)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--bump-mm", type=float, default=30.0)
    ap.add_argument("--engine", type=Path,
                    default=ROOT / "risk_engine" / "build" / "rock_risk_cli")
    args = ap.parse_args()

    rgb = cv2.imread(str(args.image))
    gt = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    # 深度是 4:3（256x192），RGB 需裁成 4:3 才能与真机一致，否则判 ASPECT_MISMATCH
    h, w = rgb.shape[:2]
    target_h = int(w * 3 / 4)
    if target_h <= h:
        top = (h - target_h) // 2
        rgb, gt = rgb[top:top + target_h], gt[top:top + target_h]
    else:
        target_w = int(h * 4 / 3)
        left = (w - target_w) // 2
        rgb, gt = rgb[:, left:left + target_w], gt[:, left:left + target_w]
    road_small = cv2.resize(gt, (DEPTH_W, DEPTH_H), interpolation=cv2.INTER_NEAREST)

    depth_meters = synthetic_depth(road_small, args.bump_mm)
    depth = DepthMap(meters=depth_meters,
                     valid=np.isfinite(depth_meters) & (depth_meters > 0),
                     source="SYNTHETIC", aligned_to_rgb=True)
    # 内参：按深度网格给出等效值（fx≈260 @256 宽，约等于 1300 @1280）
    intrinsics = Intrinsics(fx=260.0, fy=260.0, cx=DEPTH_W / 2, cy=DEPTH_H / 2,
                            ref_width=DEPTH_W, ref_height=DEPTH_H)

    segmenter = UNetRoadSegmenter(args.weights, device="cpu")
    config = PipelineConfig(obstacle_mode="geometric", timeout_ms=5000)
    pipeline = PerceptionPipeline(road_segmenter=segmenter, config=config)

    report = pipeline.process(FrameInput(
        frame_id="demo", timestamp_ns=1, rgb=rgb, depth=depth, intrinsics=intrinsics))

    geometry = pipeline.last_obstacle_geometry
    print("=== 感知 ===")
    print(f"  道路: detected={report['road_detected']} mask={report['road_mask_status']} "
          f"覆盖率={report['road']['coverage_ratio']*100:.1f}%")
    if geometry and geometry.plane:
        print(f"  地面: σ={geometry.plane.sigma_m*1000:.2f}mm "
              f"内点={geometry.plane.inlier_ratio*100:.0f}%")
    print(f"  障碍: {len(report['rockfalls'])} 个")
    for rock in report["rockfalls"]:
        estimate = rock.get("depth_estimate") or {}
        print(f"    {rock['rock_id']} bbox={tuple(round(v) for v in rock['bbox'])} "
              f"侵入={rock['intrusion']} 剩余={rock.get('remaining_width_m')} "
              f"深度={estimate.get('distance_m')}")

    # 没有落石检测器时，标 DEGRADED；这里用几何源，改为显式标注来源
    report["system_status"] = "DEGRADED"
    report["reasons"].append({
        "code": "OBSTACLE_SOURCE_GEOMETRIC",
        "message": "障碍由 RGB-D 几何检出，不含类别信息（本演示深度为合成）"})
    report["source"] = "SIMULATED"

    engine = subprocess.Popen([str(args.engine), "--stream"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, bufsize=1)
    engine.stdin.write(json.dumps(report, ensure_ascii=False) + "\n")
    engine.stdin.flush()
    status = json.loads(engine.stdout.readline())
    engine.stdin.close()
    engine.terminate()

    print("\n=== 风险判定 ===")
    print(f"  overall_risk : {status['overall_risk']}")
    print(f"  system_status: {status['system_status']}")
    for d in status["directions"]:
        print(f"  {d['direction']}: pass={d['passability']} "
              f"remaining={d.get('remaining_width_m')}")
    print(f"  替代路径     : {status['alternative_path']['status']}")
    codes = [r["code"] for r in status.get("reasons", [])][:6]
    print(f"  理由         : {codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
