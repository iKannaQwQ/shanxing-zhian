#!/usr/bin/env python3
"""M6 实时/离线视频推理：逐帧产出统一 JSON（JSONL），可选写出标注视频。

用法:
    python perception/tools/run_video.py --source <视频或图片目录>
    python perception/tools/run_video.py --source 0 --max-frames 60        # 摄像头
    python perception/tools/run_video.py --source demo.mp4 --rocks weights/rock.pt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.pipeline import FrameInput, PerceptionPipeline, PipelineConfig  # noqa: E402
from perception.road.segmenter import YolopRoadSegmenter  # noqa: E402

LEVEL_COLORS = {
    "NONE": (180, 180, 180),
    "EDGE": (0, 215, 255),
    "PARTIAL": (0, 140, 255),
    "FULL": (0, 0, 255),
    "UNKNOWN": (255, 0, 255),
}
STATUS_COLORS = {"OK": (0, 200, 0), "DEGRADED": (0, 180, 255),
                 "NO_ROAD": (0, 0, 255), "NO_DEPTH": (0, 180, 255),
                 "INPUT_ERROR": (0, 0, 255), "TIMEOUT": (0, 0, 255),
                 "DISCONNECTED": (0, 0, 255)}


def annotate(frame: np.ndarray, rep: dict) -> np.ndarray:
    out = frame.copy()
    poly = rep.get("road", {}).get("polygon") or []
    if len(poly) >= 3:
        pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], True, (0, 255, 0), 2)
    for r in rep.get("rockfalls", []):
        x1, y1, x2, y2 = [int(v) for v in r["bbox"]]
        c = LEVEL_COLORS.get(r["risk_level"], (255, 255, 255))
        cv2.rectangle(out, (x1, y1), (x2, y2), c, 2)
        tag = f"{r['rock_id']} {r['risk_level']} {r['confidence']:.2f}"
        cv2.putText(out, tag, (x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, c, 1, cv2.LINE_AA)
    st = rep["system_status"]
    cv2.putText(out, f"{st} | road={rep['road_mask_status']} | "
                     f"rocks={len(rep.get('rockfalls', []))} | "
                     f"risk={rep['metrics']['max_risk_level']}",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                STATUS_COLORS.get(st, (255, 255, 255)), 2, cv2.LINE_AA)
    return out


def iter_frames(source: str):
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
        if not cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 {source}")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
        cap.release()
        return

    p = Path(source)
    if p.is_dir():
        files = sorted([f for f in p.iterdir()
                        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}])
        for f in files:
            img = cv2.imread(str(f))
            if img is not None:
                yield img
        return

    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频 {source}")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        yield frame
    cap.release()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="视频路径 / 图片目录 / 摄像头序号")
    ap.add_argument("--rocks", default=None, help="落石检测权重（可选）")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--jsonl", default="perception/results/frames.jsonl")
    ap.add_argument("--save-video", default=None)
    ap.add_argument("--conf", type=float, default=0.35)
    args = ap.parse_args()

    seg = YolopRoadSegmenter(device=args.device)
    detector = None
    if args.rocks:
        from perception.rocks.detector import YoloRockDetector
        detector = YoloRockDetector(args.rocks, conf=args.conf, device=args.device)

    pipe = PerceptionPipeline(road_segmenter=seg, rock_detector=detector,
                              config=PipelineConfig())

    out_path = Path(args.jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    n = 0
    t0 = time.perf_counter()

    with out_path.open("w", encoding="utf-8") as fout:
        for frame in iter_frames(args.source):
            n += 1
            ts = time.time_ns()
            inp = FrameInput(frame_id=f"frame-{n:06d}", timestamp_ns=ts, rgb=frame,
                             rocks=None, source="LIVE")
            rep = pipe.process(inp)
            fout.write(json.dumps(rep, ensure_ascii=False) + "\n")

            if args.save_video:
                ann = annotate(frame, rep)
                if writer is None:
                    h, w = ann.shape[:2]
                    writer = cv2.VideoWriter(args.save_video,
                                             cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (w, h))
                writer.write(ann)

            if n % 30 == 0:
                fps = n / max(1e-6, time.perf_counter() - t0)
                print(f"[{n}] {rep['system_status']:<10} risk={rep['metrics']['max_risk_level']:<8} "
                      f"{fps:.1f} fps")

            if args.max_frames and n >= args.max_frames:
                break

    if writer is not None:
        writer.release()

    fps = n / max(1e-6, time.perf_counter() - t0)
    print(f"完成 {n} 帧，平均 {fps:.1f} fps，结果写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
